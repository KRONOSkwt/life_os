import copy
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from life_os.api import ROOT, Settings, create_app
from life_os.store import InvalidCursor, Store
from life_os.validation import Messages


TOKEN = "test-only-token-" + "a" * 32
CONTRACT = Messages(ROOT / "contracts" / "sync-v1.schema.json")


def routine():
    return {
        "name": "Push",
        "exercises": [{
            "id": str(uuid4()), "name": "Press de banca",
            "target_sets": 3, "reps_min": 8, "reps_max": 12,
        }],
    }


def operation(data=None, *, entity_type="routine", entity_id=None, version=0):
    return {
        "operation_id": str(uuid4()), "entity_type": entity_type,
        "entity_id": entity_id or str(uuid4()), "base_version": version,
        "action": "upsert", "data": data if data is not None else routine(),
    }


def session(template, routine_id):
    return {
        "routine_id": routine_id, "routine_version": 1,
        "routine_snapshot": copy.deepcopy(template),
        "started_at": "2026-10-28T07:15:00-04:00",
        "finished_at": "2026-10-28T08:00:00-04:00",
        "sets": [{
            "id": str(uuid4()), "exercise_id": template["exercises"][0]["id"],
            "weight": "22.500", "unit": "kg", "reps": 10, "kind": "work", "rir": 2,
        }],
    }


@pytest.fixture
def settings(tmp_path):
    return Settings(database=tmp_path / "life-os.sqlite3", token=TOKEN, web_directory=tmp_path / "web")


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings), client=("127.0.0.1", 50000)) as instance:
        instance.headers["Authorization"] = f"Bearer {TOKEN}"
        yield instance


def push(client, *operations):
    response = client.post(
        "/api/v1/sync/push", json={"protocol_version": 1, "operations": list(operations)}
    )
    if response.status_code == 200:
        CONTRACT.validate(response.json(), "pushResponse")
    return response


def pull(client, **params):
    response = client.get("/api/v1/sync/pull", params=params)
    assert response.status_code == 200, response.text
    CONTRACT.validate(response.json(), "pullResponse")
    return response.json()


def test_routine_session_restart_and_lost_response(settings, client):
    template = operation()
    assert push(client, template).json()["results"][0]["status"] == "applied"
    bookmark = pull(client)["next_cursor"]
    training = operation(session(template["data"], template["entity_id"]), entity_type="session")
    first = push(client, training)
    assert first.status_code == 200
    # Simulate a client that lost the response, then a server restart.
    with TestClient(create_app(settings), client=("127.0.0.1", 50000)) as restarted:
        restarted.headers["Authorization"] = f"Bearer {TOKEN}"
        assert push(restarted, training).json() == first.json()
        changes = pull(restarted, cursor=bookmark)["changes"]
        assert len(changes) == 1
        assert changes[0]["data"]["sets"][0]["weight"] == "22.500"
        assert len(pull(restarted)["changes"]) == 2


def test_conflict_replay_and_explicit_resolution(client):
    original = operation()
    push(client, original)
    web = operation(entity_id=original["entity_id"], version=1)
    web["data"]["name"] = "Push web"
    assert push(client, web).json()["results"][0]["record"]["version"] == 2
    phone = operation(entity_id=original["entity_id"], version=1)
    phone["data"]["name"] = "Push phone"
    conflict = push(client, phone).json()
    assert conflict["results"][0]["status"] == "conflict"
    assert conflict["results"][0]["record"]["data"]["name"] == "Push web"
    assert push(client, phone).json() == conflict
    resolution = {**phone, "operation_id": str(uuid4()), "base_version": 2}
    assert push(client, resolution).json()["results"][0]["record"]["version"] == 3


def test_operation_id_cannot_be_reused_with_other_content(client):
    item = operation()
    push(client, item)
    modified = copy.deepcopy(item)
    modified["data"]["name"] = "Different payload"
    assert push(client, modified).status_code == 409
    assert len(pull(client)["changes"]) == 1


def test_pages_include_new_writes_without_duplicates(client):
    push(client, operation(), operation(), operation())
    first = pull(client, limit=2)
    assert first["has_more"] is True
    push(client, operation())
    second = pull(client, cursor=first["next_cursor"], limit=2)
    assert second["has_more"] is False
    records = first["changes"] + second["changes"]
    assert len({record["entity_id"] for record in records}) == 4
    empty = pull(client, cursor=second["next_cursor"])
    assert empty["changes"] == []
    assert empty["next_cursor"] == second["next_cursor"]


def test_delete_keeps_session_snapshot_and_prevents_resurrection(client):
    template = operation()
    training = operation(session(template["data"], template["entity_id"]), entity_type="session")
    push(client, template, training)
    deletion = {
        **template, "operation_id": str(uuid4()), "base_version": 1,
        "action": "delete", "data": None,
    }
    deleted = push(client, deletion).json()["results"][0]["record"]
    assert deleted["deleted"] and deleted["data"] is None
    resurrection = operation(entity_id=template["entity_id"], version=2)
    assert push(client, resurrection).json()["results"][0]["status"] == "conflict"
    changes = pull(client)["changes"]
    assert changes[1]["data"]["routine_snapshot"] == template["data"]


@pytest.mark.parametrize("defect", [
    "range", "duplicate_exercise", "blank", "uuid", "extra", "duplicate_operation",
    "date", "unknown_exercise", "numeric_weight", "duplicate_set", "chronology",
])
def test_invalid_batch_applies_nothing(client, defect):
    valid = operation()
    invalid = operation()
    if defect == "range":
        invalid["data"]["exercises"][0]["reps_min"] = 20
    elif defect == "duplicate_exercise":
        invalid["data"]["exercises"] *= 2
    elif defect == "blank":
        invalid["data"]["name"] = "   "
    elif defect == "uuid":
        invalid["entity_id"] = "not-a-uuid"
    elif defect == "extra":
        invalid["extra"] = True
    elif defect == "duplicate_operation":
        invalid["operation_id"] = valid["operation_id"]
    else:
        invalid = operation(session(valid["data"], valid["entity_id"]), entity_type="session")
        data = invalid["data"]
        if defect == "date":
            data["started_at"] = "yesterday"
        elif defect == "unknown_exercise":
            data["sets"][0]["exercise_id"] = str(uuid4())
        elif defect == "numeric_weight":
            data["sets"][0]["weight"] = 22.5
        elif defect == "duplicate_set":
            data["sets"] *= 2
        elif defect == "chronology":
            data["finished_at"] = "2026-10-28T06:00:00-04:00"
    assert push(client, valid, invalid).status_code == 422
    assert pull(client)["changes"] == []


def test_authentication_and_request_bounds(client):
    assert client.get("/health").status_code == 200
    assert client.get("/api/v1/sync/pull", headers={"Authorization": "Bearer wrong"}).status_code == 401
    del client.headers["Authorization"]
    assert client.get("/api/v1/sync/pull").status_code == 401
    assert push(client, operation()).status_code == 401
    client.headers["Authorization"] = f"Bearer {TOKEN}"
    assert client.get("/api/v1/sync/pull?limit=101").status_code == 422
    assert client.get("/api/v1/sync/pull?cursor=broken").status_code == 400
    assert client.post("/api/v1/sync/push", content="{}", headers={"Content-Type": "text/plain"}).status_code == 415
    assert client.post("/api/v1/sync/push", content=b"x" * (1024 * 1024 + 1), headers={"Content-Type": "application/json"}).status_code == 413


def test_simultaneous_retries_commit_once(settings):
    store = Store(settings.database)
    item = operation()
    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(lambda _: store.apply("phone", item), range(8)))
    assert all(result == results[0] for result in results)
    assert len(store.pull(None, 100)["changes"]) == 1


def test_simultaneous_edits_have_one_winner(settings):
    store = Store(settings.database)
    initial = operation()
    store.apply("web", initial)
    updates = [operation(entity_id=initial["entity_id"], version=1) for _ in range(2)]
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda item: store.apply("phone", item), updates))
    assert sorted(result["status"] for result in results) == ["applied", "conflict"]


def test_receipt_failure_rolls_back_record_and_change(settings):
    store = Store(settings.database)
    item = operation()
    with store.connection() as db:
        db.execute("CREATE TRIGGER receipt_failure BEFORE INSERT ON operations BEGIN SELECT RAISE(ABORT, 'injected failure'); END")
    with pytest.raises(sqlite3.IntegrityError):
        store.apply("phone", item)
    assert store.pull(None, 100)["changes"] == []
    with store.connection() as db:
        assert db.execute("SELECT COUNT(*) FROM records").fetchone()[0] == 0
        db.execute("DROP TRIGGER receipt_failure")
    assert store.apply("phone", item)["status"] == "applied"


def test_cursors_are_bound_to_database_and_reject_future_positions(settings, tmp_path):
    first = Store(settings.database)
    other = Store(tmp_path / "other.sqlite3")
    cursor = first.pull(None, 100)["next_cursor"]
    with pytest.raises(InvalidCursor):
        other.pull(cursor, 100)
    with pytest.raises(InvalidCursor):
        first.pull(first._encode_cursor(1), 100)


def test_unknown_schema_version_is_not_modified(tmp_path):
    database = tmp_path / "future.sqlite3"
    with sqlite3.connect(database) as db:
        db.execute("PRAGMA user_version = 99")
    with pytest.raises(RuntimeError, match="Unsupported database version"):
        Store(database)
    with sqlite3.connect(database) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 99


def test_startup_requires_explicit_token(tmp_path):
    with pytest.raises(ValueError, match="LIFE_OS_DEV_TOKEN"):
        Settings(database=tmp_path / "unused.sqlite3", token="")


def test_shared_example_is_accepted_and_can_be_retried(client):
    example = json.loads((ROOT / "contracts/examples/create-routine.json").read_text(encoding="utf-8"))
    response = client.post("/api/v1/sync/push", json=example)
    assert response.status_code == 200
    CONTRACT.validate(response.json(), "pushResponse")
    assert client.post("/api/v1/sync/push", json=example).json() == response.json()


def test_compiled_web_is_served_without_shadowing_api(settings):
    settings.web_directory.mkdir()
    (settings.web_directory / "index.html").write_text('<html><div id="app"></div></html>', encoding="utf-8")
    assets = settings.web_directory / "assets"
    assets.mkdir()
    (assets / "app.js").write_text('console.log("web")', encoding="utf-8")
    with TestClient(create_app(settings), client=("127.0.0.1", 50000)) as client:
        assert 'id="app"' in client.get("/").text
        assert client.get("/assets/app.js").status_code == 200
        assert client.get("/health").json()["status"] == "ok"
        assert client.get("/api/v1/sync/pull").status_code == 401
        assert client.get("/missing").status_code == 404


def test_server_can_run_before_web_is_built(client):
    assert client.get("/").status_code == 404
    assert client.get("/health").status_code == 200
