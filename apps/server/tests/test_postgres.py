import copy
import os
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

pytest.importorskip("psycopg", reason="Instalar el extra postgres para ejecutar estas pruebas")

from life_os.api import Settings, create_app
from life_os.auth import Credentials, PairingDenied
from life_os.postgres import PostgresStore, _Connection, _connect, connection_parameters
from life_os.store import InvalidCursor, OperationReused
from test_sync import operation, session


@pytest.fixture
def database_url():
    admin = os.environ.get("LIFE_OS_TEST_POSTGRES_URL")
    if not admin:
        pytest.skip("Requiere PostgreSQL temporal: ejecutar scripts/smoke-postgres.py")
    import psycopg
    from psycopg import sql
    name = "lifeos_test_" + uuid4().hex
    connection = _connect(admin)
    try:
        connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
        values = psycopg.conninfo.conninfo_to_dict(admin)
        values["dbname"] = name
        try:
            yield psycopg.conninfo.make_conninfo(**values)
        finally:
            assert name.startswith("lifeos_test_") and len(name) == 44
            connection.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))
    finally:
        connection.close()


@pytest.fixture
def store(database_url):
    return PostgresStore.initialize(database_url)


def test_explicit_initialization_preserves_identity_and_rejects_unknown_schema(database_url):
    with pytest.raises(RuntimeError, match="postgres-init"):
        PostgresStore(database_url)
    first = PostgresStore.initialize(database_url)
    assert PostgresStore.initialize(database_url).database_id == first.database_id
    with first.connection() as db:
        db.execute("UPDATE metadata SET value = '99' WHERE key = 'schema_version'")
    with pytest.raises(RuntimeError, match="no compatible"):
        PostgresStore.initialize(database_url)
    with first.connection() as db:
        assert db.execute("SELECT value FROM metadata WHERE key = 'schema_version'").fetchone()[0] == "99"


def test_partial_existing_schema_is_not_completed(database_url):
    raw = _connect(database_url)
    try:
        raw.execute("CREATE TABLE public.records (keep_me TEXT)")
        raw.execute("INSERT INTO public.records VALUES ('existing data')")
        with pytest.raises(RuntimeError, match="parciales"):
            PostgresStore.initialize(database_url)
        assert raw.execute("SELECT keep_me FROM public.records").fetchone()[0] == "existing data"
        assert raw.execute("SELECT to_regclass('public.metadata')").fetchone()[0] is None
    finally:
        raw.close()


def test_exact_session_receipt_and_cursor_survive_reopen(store, database_url):
    template = operation()
    template["data"]["name"] = "Qué? 100% · sesión"
    store.apply("phone", template)
    cursor = store.pull(None, 100)["next_cursor"]
    training = operation(session(template["data"], template["entity_id"]), entity_type="session")
    receipt = store.apply("phone", training)
    reopened = PostgresStore(database_url)
    assert reopened.database_id == store.database_id
    assert reopened.apply("phone", training) == receipt
    changes = reopened.pull(cursor, 100)["changes"]
    assert len(changes) == 1 and changes[0]["data"]["sets"][0]["weight"] == "22.500"
    altered = copy.deepcopy(training)
    altered["data"]["sets"][0]["weight"] = "25"
    with pytest.raises(OperationReused):
        reopened.apply("phone", altered)
    assert len(reopened.pull(None, 100)["changes"]) == 2


def test_concurrent_create_has_one_winner_and_concurrent_replay_is_identical(store):
    first = operation()
    second = copy.deepcopy(first)
    second["operation_id"] = str(uuid4())
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda op: store.apply("phone", op), [first, second]))
    assert sorted(result["status"] for result in results) == ["applied", "conflict"]
    winner = first if results[0]["status"] == "applied" else second
    with ThreadPoolExecutor(max_workers=4) as pool:
        receipts = list(pool.map(lambda _: store.apply("phone", winner), range(4)))
    assert all(result == receipts[0] for result in receipts)
    assert len(store.pull(None, 100)["changes"]) == 1


def test_pagination_tombstones_and_ahead_cursor(store):
    operations = [operation() for _ in range(3)]
    for item in operations:
        store.apply("phone", item)
    first = store.pull(None, 1)
    assert first["has_more"] and len(first["changes"]) == 1
    second = store.pull(first["next_cursor"], 100)
    assert len(second["changes"]) == 2 and not second["has_more"]
    deletion = copy.deepcopy(operations[0])
    deletion.update(operation_id=str(uuid4()), base_version=1, action="delete", data=None)
    assert store.apply("phone", deletion)["record"]["deleted"]
    resurrection = copy.deepcopy(operations[0])
    resurrection.update(operation_id=str(uuid4()), base_version=2)
    assert store.apply("phone", resurrection)["status"] == "conflict"
    with pytest.raises(InvalidCursor):
        store.pull(store._encode_cursor(10000), 100)


def test_pairing_concurrency_failed_attempts_and_revocation_survive_reopen(store, database_url):
    access = Credentials(store)
    code = access.issue_pairing()["pairing_code"]

    def exchange(_):
        try:
            return access.exchange(code, "phone")
        except PairingDenied:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        grants = [item for item in pool.map(exchange, range(2)) if item]
    assert len(grants) == 1
    grant = grants[0]
    assert access.authenticate(grant["access_token"]) == grant["device_id"]
    assert access.revoke(grant["device_id"])
    assert Credentials(PostgresStore(database_url)).authenticate(grant["access_token"]) is None
    challenge = access.issue_pairing()
    wrong = "A" * 20 if challenge["pairing_code"].replace("-", "") != "A" * 20 else "B" * 20
    for _ in range(5):
        with pytest.raises(PairingDenied):
            access.exchange(wrong, "phone")
    with pytest.raises(PairingDenied):
        access.exchange(challenge["pairing_code"], "phone")


def test_transaction_failure_rolls_back_and_the_next_write_can_proceed(store):
    with pytest.raises(RuntimeError):
        with store.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("INSERT INTO metadata VALUES ('temporary', 'must roll back')")
            raise RuntimeError("interruption")
    with store.connection() as db:
        assert db.execute("SELECT value FROM metadata WHERE key = 'temporary'").fetchone() is None
    assert store.apply("phone", operation())["status"] == "applied"


def test_connection_settings_are_transaction_local(database_url):
    raw = _connect(database_url)
    try:
        original = raw.execute("SHOW statement_timeout").fetchone()[0]
        wrapped = _Connection(raw)
        assert wrapped.execute("SHOW statement_timeout").fetchone()[0] == "15s"
        assert wrapped.execute("SHOW search_path").fetchone()[0] == "public"
        raw.commit()
        assert raw.execute("SHOW statement_timeout").fetchone()[0] == original
    finally:
        raw.close()


def test_fastapi_uses_postgres_without_creating_sqlite_and_keeps_authentication(store, database_url, tmp_path):
    credentials = Credentials(store)
    code = credentials.issue_pairing()["pairing_code"]
    sqlite_path = tmp_path / "must-not-exist.sqlite3"
    settings = Settings(database=sqlite_path, database_url=database_url, mode="paired", web_directory=tmp_path / "web")
    with TestClient(create_app(settings), base_url="https://testserver") as client:
        assert client.get("/api/v1/sync/pull").status_code == 401
        grant = client.post("/api/v1/pairing/exchange", json={"pairing_code": code, "device_name": "cloud test"}).json()
        headers = {"Authorization": f"Bearer {grant['access_token']}"}
        item = operation()
        payload = {"protocol_version": 1, "operations": [item]}
        result = client.post("/api/v1/sync/push", headers=headers, json=payload)
        assert result.status_code == 200
        assert client.post("/api/v1/sync/push", headers=headers, json=payload).json() == result.json()
        assert len(client.get("/api/v1/sync/pull", headers=headers).json()["changes"]) == 1
    assert not sqlite_path.exists()


def test_remote_connections_require_hostname_verification_and_do_not_echo_secrets():
    options = connection_parameters("postgresql://user:private-password@example.invalid/lifeos?sslmode=disable")
    assert options["sslmode"] == "verify-full" and options["sslrootcert"]
    with pytest.raises(ValueError) as error:
        connection_parameters("postgresql://user:private-password@localhost/lifeos?hostaddr=10.0.0.1")
    assert "private-password" not in str(error.value)
