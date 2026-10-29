"""Run the server suite and a real HTTPS journey against disposable PostgreSQL 17."""

import copy
import json
import os
import re
import runpy
import secrets
import ssl
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import HTTPSHandler, ProxyHandler, build_opener
from uuid import uuid4

from life_os.auth import Credentials
from life_os.postgres import PostgresStore
from life_os.tls import create_identity

ROOT = Path(__file__).resolve().parents[1]
helpers = runpy.run_path(str(ROOT / "scripts/smoke-server.py"), run_name="http_helpers")
IMAGE = "postgres:17-alpine"


def docker(*arguments, capture=True):
    result = subprocess.run(["docker", *arguments], check=True, text=True,
        stdout=subprocess.PIPE if capture else None, stderr=subprocess.PIPE if capture else None,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return result.stdout.strip() if capture else ""


def journey(directory, url):
    store = PostgresStore.initialize(url)
    credentials = Credentials(store)
    challenge = credentials.issue_pairing()
    tls = directory / "tls"
    create_identity(tls, [])
    trusted = build_opener(ProxyHandler({}), HTTPSHandler(
        context=ssl.create_default_context(cafile=str(tls / "server-cert.pem"))))
    absent_sqlite = directory / "must-not-exist.sqlite3"
    environment = {**os.environ, "LIFE_OS_DATABASE_URL": url, "LIFE_OS_DATABASE": str(absent_sqlite)}
    routine = json.loads((ROOT / "contracts/examples/create-routine.json").read_text(encoding="utf-8"))
    template = routine["operations"][0]
    training = {"protocol_version": 1, "operations": [{
        "operation_id": str(uuid4()), "entity_type": "session", "entity_id": str(uuid4()),
        "base_version": 0, "action": "upsert", "data": {
            "routine_id": template["entity_id"], "routine_version": 1,
            "routine_snapshot": copy.deepcopy(template["data"]),
            "started_at": "2026-10-29T07:15:00-04:00", "finished_at": "2026-10-29T08:00:00-04:00",
            "sets": [{"id": str(uuid4()), "exercise_id": template["data"]["exercises"][0]["id"],
                      "weight": "22.500", "unit": "kg", "reps": 10, "kind": "work"}],
        },
    }]}
    with helpers["server"](environment, directory / "postgres-https.log", tls_directory=tls, http=trusted) as base:
        grant = helpers["message"](base, "/api/v1/pairing/exchange", None,
            {"pairing_code": challenge["pairing_code"], "device_name": "Postgres journey"}, http=trusted)
        token = grant["access_token"]
        helpers["message"](base, "/api/v1/sync/push", token, routine, http=trusted)
        receipt = helpers["message"](base, "/api/v1/sync/push", token, training, http=trusted)
    with helpers["server"](environment, directory / "postgres-https.log", tls_directory=tls, http=trusted) as base:
        assert helpers["message"](base, "/api/v1/sync/push", token, training, http=trusted) == receipt
        changes = helpers["message"](base, "/api/v1/sync/pull", token, http=trusted)["changes"]
        assert len(changes) == 2 and changes[1]["data"]["sets"][0]["weight"] == "22.500"
        assert credentials.revoke(grant["device_id"])
        try:
            helpers["message"](base, "/api/v1/sync/pull", token, http=trusted)
        except HTTPError as error:
            assert error.code == 401
        else:
            raise AssertionError("La credencial revocada se aceptó")
    assert not absent_sqlite.exists()


def main():
    if sys.prefix == sys.base_prefix:
        raise SystemExit("Usa el Python de .venv con el extra postgres y las dependencias de prueba.")
    try:
        docker("info", "--format", "{{.ServerVersion}}")
    except (OSError, subprocess.CalledProcessError):
        raise SystemExit("Inicia Docker antes de ejecutar la comprobación PostgreSQL.") from None
    container = None
    with helpers["temporary_workspace"]("postgres-smoke-") as directory:
        try:
            try:
                docker("image", "inspect", IMAGE)
            except subprocess.CalledProcessError:
                docker("pull", IMAGE, capture=False)
            environment_file = directory / "postgres.env"
            password = secrets.token_urlsafe(32)
            environment_file.write_text("POSTGRES_USER=lifeos_test\nPOSTGRES_PASSWORD=" + password +
                                        "\nPOSTGRES_DB=postgres\n", encoding="utf-8")
            name = "lifeos-pg-" + uuid4().hex
            container = docker("run", "--detach", "--name", name, "--env-file", str(environment_file),
                "--publish", "127.0.0.1::5432", "--tmpfs", "/var/lib/postgresql/data", IMAGE)
            if not re.fullmatch(r"[0-9a-f]{64}", container):
                raise RuntimeError("Docker no devolvió el ID del contenedor recién creado")
            port = int(docker("port", container, "5432/tcp").rsplit(":", 1)[1])
            url = f"postgresql://lifeos_test:{password}@127.0.0.1:{port}/postgres?sslmode=disable"
            import psycopg
            for attempt in range(60):
                try:
                    with psycopg.connect(url, connect_timeout=1) as connection:
                        version = connection.info.server_version
                    break
                except psycopg.OperationalError:
                    time.sleep(0.25)
            else:
                raise RuntimeError("El PostgreSQL temporal no estuvo disponible")
            environment = {**os.environ, "LIFE_OS_TEST_POSTGRES_URL": url}
            environment.pop("LIFE_OS_DATABASE_URL", None)
            subprocess.run([sys.executable, "-I", "-m", "pytest", "-q"],
                cwd=ROOT / "apps/server", env=environment, check=True,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            journey(directory, url)
        finally:
            if container and re.fullmatch(r"[0-9a-f]{64}", container):
                docker("rm", "--force", container)
    print(f"OK: suite y HTTPS real sobre PostgreSQL {version}; sesión exacta, reinicio, reenvío y revocación.")
    print("Contenedor y archivos temporales retirados; .data y el teléfono no se modificaron.")


if __name__ == "__main__":
    main()
