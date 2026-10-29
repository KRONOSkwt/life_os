import copy
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from life_os.api import ROOT, Settings, create_app
from life_os.auth import Credentials, PairingDenied
from life_os.store import Store, canonical


DEV_TOKEN = "development-test-token-" + "x" * 32


@pytest.fixture
def access(tmp_path):
    return Credentials(Store(tmp_path / "server.sqlite3"))


def paired(access, name="Honor X8A"):
    code = access.issue_pairing()["pairing_code"]
    return access.exchange(code, name)


def settings(access, mode="paired"):
    return Settings(database=access.store.path, mode=mode,
                    token=DEV_TOKEN if mode == "development" else None,
                    web_directory=access.store.path.parent / "unused-web")


def auth(grant):
    return {"Authorization": f"Bearer {grant['access_token']}"}


def test_pairing_is_single_use_and_only_hashes_are_stored(access):
    challenge = access.issue_pairing()
    code = challenge["pairing_code"]
    with access.store.connection() as db:
        saved = db.execute("SELECT code_hash FROM pairing").fetchone()[0]
        assert code.replace("-", "") not in saved
    grant = access.exchange(code.lower(), " Honor X8A ")
    assert grant["server_id"] == access.store.database_id
    assert access.authenticate(grant["access_token"]) == grant["device_id"]
    with pytest.raises(PairingDenied):
        access.exchange(code, "Another device")
    with access.store.connection() as db:
        token_hash = db.execute("SELECT token_hash FROM devices").fetchone()[0]
        assert grant["access_token"] != token_hash and len(token_hash) == 64
    assert access.devices()[0]["name"] == "Honor X8A"
    assert "access_token" not in canonical(access.devices())


def test_expired_challenge_cannot_be_consumed(tmp_path):
    clock = [1000]
    access = Credentials(Store(tmp_path / "expiry.sqlite3"), clock=lambda: clock[0])
    code = access.issue_pairing(ttl=1)["pairing_code"]
    clock[0] = 1001
    with pytest.raises(PairingDenied):
        access.exchange(code, "Phone")
    assert access.devices() == []


def test_failed_attempts_lock_challenge_and_new_challenge_replaces_it(access):
    code = access.issue_pairing()["pairing_code"]
    raw = code.replace("-", "")
    wrong = ("B" if raw[0] == "A" else "A") + raw[1:]
    for _ in range(5):
        with pytest.raises(PairingDenied):
            access.exchange(wrong, "Unknown")
    with pytest.raises(PairingDenied):
        Credentials(Store(access.store.path)).exchange(code, "Phone")
    replacement = access.issue_pairing()["pairing_code"]
    with pytest.raises(PairingDenied):
        access.exchange(code, "Phone")
    assert access.exchange(replacement, "Phone")["access_token"]


def test_simultaneous_pairing_can_issue_only_one_device(access):
    code = access.issue_pairing()["pairing_code"]
    def attempt(_):
        try:
            return access.exchange(code, "Phone")
        except PairingDenied:
            return None
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(attempt, range(2)))
    assert sum(result is not None for result in results) == 1
    assert len(access.devices()) == 1


def test_plain_http_and_forwarded_header_cannot_enable_pairing(access):
    code = access.issue_pairing()["pairing_code"]
    with TestClient(create_app(settings(access)), base_url="http://testserver") as client:
        response = client.post("/api/v1/pairing/exchange", headers={"X-Forwarded-Proto": "https"},
                               json={"pairing_code": code, "device_name": "Phone"})
        assert response.status_code == 400
        assert client.get("/api/v1/sync/pull", headers={"X-Forwarded-Proto": "https"}).status_code == 400
    assert access.devices() == []


def test_https_pairing_rejects_browser_origin_without_consuming_code(access):
    code = access.issue_pairing()["pairing_code"]
    with TestClient(create_app(settings(access)), base_url="https://testserver") as client:
        body = {"pairing_code": code, "device_name": "Phone"}
        assert client.post("/api/v1/pairing/exchange", json=body,
                           headers={"Origin": "https://other.example"}).status_code == 403
        response = client.post("/api/v1/pairing/exchange", json=body)
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        assert client.post("/api/v1/pairing/exchange", json=body).status_code == 401
        assert client.get("/api/v1/sync/pull", headers=auth(response.json())).status_code == 200


def test_pairing_body_is_bounded_before_parsing_and_does_not_consume_code(access):
    code = access.issue_pairing()["pairing_code"]
    with TestClient(create_app(settings(access)), base_url="https://testserver") as client:
        assert client.post("/api/v1/pairing/exchange", content=b"x" * 4097,
                           headers={"Content-Type": "application/json"}).status_code == 413
        assert client.post("/api/v1/pairing/exchange", content="{}",
                           headers={"Content-Type": "text/plain"}).status_code == 415
    assert access.exchange(code, "Phone")["access_token"]


@pytest.mark.parametrize("field,value", [("device_name", "   "), ("device_name", "x" * 81),
                                         ("pairing_code", "x" * 100)])
def test_pair_request_validation(access, field, value):
    body = {"pairing_code": access.issue_pairing()["pairing_code"], "device_name": "Phone"}
    body[field] = value
    with TestClient(create_app(settings(access)), base_url="https://testserver") as client:
        assert client.post("/api/v1/pairing/exchange", json=body).status_code == 422
    assert access.devices() == []


def test_tokens_survive_restart_and_revocation_does_not_affect_other_devices(access):
    first = paired(access, "Phone")
    second = paired(access, "Tablet")
    with TestClient(create_app(settings(access)), base_url="https://testserver") as client:
        assert client.get("/api/v1/sync/pull", headers=auth(first)).status_code == 200
        assert access.revoke(first["device_id"])
        assert not access.revoke(first["device_id"])
        assert client.get("/api/v1/sync/pull", headers=auth(first)).status_code == 401
        assert client.get("/api/v1/sync/pull", headers=auth(second)).status_code == 200
    with TestClient(create_app(settings(access)), base_url="https://testserver") as restarted:
        assert restarted.get("/api/v1/sync/pull", headers=auth(first)).status_code == 401
        assert restarted.get("/api/v1/sync/pull", headers=auth(second)).status_code == 200


def test_device_id_cannot_be_spoofed_and_receipts_are_scoped_to_credential(access):
    first = paired(access, "Phone")
    second = paired(access, "Tablet")
    example = json.loads((ROOT / "contracts/examples/create-routine.json").read_text(encoding="utf-8"))
    with TestClient(create_app(settings(access)), base_url="https://testserver") as client:
        assert client.post("/api/v1/sync/push", json=example, headers=auth(first)).json()["results"][0]["status"] == "applied"
        spoofed = {**auth(second), "X-Device-ID": first["device_id"]}
        assert client.post("/api/v1/sync/push", json=example, headers=spoofed).json()["results"][0]["status"] == "conflict"
        assert client.post("/api/v1/sync/push", json=example, headers=auth(first)).json()["results"][0]["status"] == "applied"
        assert client.get("/api/v1/devices", headers=auth(first)).status_code == 404
    with access.store.connection() as db:
        assert {row[0] for row in db.execute("SELECT device_id FROM operations")} == {first["device_id"], second["device_id"]}


def test_development_secret_is_not_accepted_in_paired_mode(access):
    # Even an explicitly supplied development token must have no authority here.
    config = Settings(database=access.store.path, mode="paired", token=DEV_TOKEN)
    with TestClient(create_app(config), base_url="https://testserver") as client:
        assert client.get("/api/v1/sync/pull", headers={"Authorization": f"Bearer {DEV_TOKEN}"}).status_code == 401


def test_development_secret_is_loopback_only_and_pairing_is_disabled(access):
    config = settings(access, "development")
    headers = {"Authorization": f"Bearer {DEV_TOKEN}"}
    with TestClient(create_app(config), client=("192.168.1.50", 50000)) as remote:
        assert remote.get("/api/v1/sync/pull", headers=headers).status_code == 401
    with TestClient(create_app(config), client=("127.0.0.1", 50000)) as local:
        assert local.get("/api/v1/sync/pull", headers=headers).status_code == 200
        assert local.post("/api/v1/pairing/exchange", json={"pairing_code": "A" * 20, "device_name": "Phone"}).status_code == 404


def test_migration_preserves_v1_records_cursor_identity_and_receipts(tmp_path):
    database = tmp_path / "version1.sqlite3"
    schema = Path(__file__).parents[1] / "life_os/migrations/001_initial.sql"
    identity = str(uuid4())
    item = json.loads((ROOT / "contracts/examples/create-routine.json").read_text(encoding="utf-8"))["operations"][0]
    record = {"entity_type": "routine", "entity_id": item["entity_id"], "version": 1, "deleted": False, "data": item["data"]}
    receipt = {"operation_id": item["operation_id"], "status": "applied", "record": record}
    with sqlite3.connect(database) as db:
        db.executescript(schema.read_text(encoding="utf-8"))
        db.execute("INSERT INTO metadata VALUES ('database_id', ?)", (identity,))
        db.execute("INSERT INTO records VALUES ('routine', ?, 1, 0, ?)", (item["entity_id"], canonical(record)))
        db.execute("INSERT INTO changes(record) VALUES (?)", (canonical(record),))
        db.execute("INSERT INTO operations VALUES ('local-development', ?, ?, ?)",
                   (item["operation_id"], canonical(item), canonical(receipt)))
    store = Store(database)
    assert store.database_id == identity
    assert store.pull(None, 100)["changes"] == [record]
    assert store.apply("local-development", copy.deepcopy(item)) == receipt
    with store.connection() as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 2
    assert paired(Credentials(store))["access_token"]
