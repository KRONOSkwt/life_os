"""Recover a disposable HTTPS server and reuse its device credential and receipts."""

import copy
import json
import os
import runpy
import secrets
import ssl
import sys
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import HTTPSHandler, ProxyHandler, build_opener
from uuid import uuid4

from life_os.auth import Credentials
from life_os.backup import create_backup, restore_backup
from life_os.store import Store
from life_os.tls import create_identity, identity_info, load_identity

ROOT = Path(__file__).resolve().parents[1]
helpers = runpy.run_path(str(ROOT / "scripts/smoke-server.py"), run_name="http_helpers")


def main():
    if sys.prefix == sys.base_prefix:
        raise SystemExit("Ejecutar mediante el Python de .venv.")
    with helpers["temporary_workspace"]("backup-smoke-") as directory:
        tls, database = directory / "tls", directory / "source.sqlite3"
        identity = create_identity(tls, [])
        store = Store(database)
        access = Credentials(store)
        challenge = access.issue_pairing()
        http = build_opener(ProxyHandler({}), HTTPSHandler(
            context=ssl.create_default_context(cafile=str(tls / "server-cert.pem"))))
        environment = {**os.environ, "LIFE_OS_DATABASE": str(database)}
        routine = json.loads((ROOT / "contracts/examples/create-routine.json").read_text(encoding="utf-8"))
        operation = routine["operations"][0]
        training = {"protocol_version": 1, "operations": [{
            "operation_id": str(uuid4()), "entity_type": "session", "entity_id": str(uuid4()),
            "base_version": 0, "action": "upsert", "data": {
                "routine_id": operation["entity_id"], "routine_version": 1,
                "routine_snapshot": copy.deepcopy(operation["data"]),
                "started_at": "2026-10-29T07:15:00-04:00", "finished_at": "2026-10-29T08:00:00-04:00",
                "sets": [{"id": str(uuid4()), "exercise_id": operation["data"]["exercises"][0]["id"],
                          "weight": "22.500", "unit": "kg", "reps": 10, "kind": "work"}],
            },
        }]}
        password = secrets.token_urlsafe(32)
        archive = directory / "snapshot.lifeos-backup"
        with helpers["server"](environment, directory / "source.log", tls_directory=tls, http=http) as base:
            grant = helpers["message"](base, "/api/v1/pairing/exchange", None,
                {"pairing_code": challenge["pairing_code"], "device_name": "Backup journey"}, http=http)
            token = grant["access_token"]
            old = access.exchange(access.issue_pairing()["pairing_code"], "Revoked before backup")
            access.revoke(old["device_id"])
            helpers["message"](base, "/api/v1/sync/push", token, routine, http=http)
            receipt = helpers["message"](base, "/api/v1/sync/push", token, training, http=http)
            create_backup(database, archive, password, tls_directory=tls)
            # A later change must not appear in a point-in-time recovery.
            later = copy.deepcopy(operation)
            later.update(operation_id=str(uuid4()), entity_id=str(uuid4()))
            store.apply(grant["device_id"], later)
        restored = directory / "recovered"
        restore_backup(archive, restored, password)
        assert identity_info(load_identity(restored / "tls")) == identity
        environment["LIFE_OS_DATABASE"] = str(restored / "life-os.sqlite3")
        with helpers["server"](environment, directory / "recovered.log", tls_directory=restored / "tls", http=http) as base:
            changes = helpers["message"](base, "/api/v1/sync/pull", token, http=http)["changes"]
            assert len(changes) == 2
            assert changes[1]["data"]["sets"][0]["weight"] == "22.500"
            assert helpers["message"](base, "/api/v1/sync/push", token, training, http=http) == receipt
            assert len(helpers["message"](base, "/api/v1/sync/pull", token, http=http)["changes"]) == 2
            try:
                helpers["message"](base, "/api/v1/sync/pull", old["access_token"], http=http)
            except HTTPError as error:
                assert error.code == 401
            else:
                raise AssertionError("La credencial revocada se aceptó tras restaurar")
    print("OK: copia cifrada con servidor activo y restauración HTTPS con identidad y credencial previas.")
    print("OK: sesión exacta, recibo idempotente y revocación conservados; cambio posterior excluido.")
    print("Servidores y archivos temporales retirados; .data y el teléfono no se modificaron.")


if __name__ == "__main__":
    main()
