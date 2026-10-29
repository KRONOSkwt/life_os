"""Exercise a real Uvicorn process using a disposable database and token."""

import copy
import json
import os
import re
import secrets
import socket
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from urllib.error import URLError
from urllib.request import ProxyHandler, Request, build_opener
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[1]
HTTP = build_opener(ProxyHandler({}))


@contextmanager
def temporary_workspace(prefix):
    cache = (ROOT / ".cache").resolve()
    if not cache.is_relative_to(ROOT.resolve()):
        raise RuntimeError("La cache temporal esta fuera del workspace")
    cache.mkdir(exist_ok=True)
    temporary = tempfile.TemporaryDirectory(prefix=prefix, dir=cache)
    directory = Path(temporary.name).resolve()
    if directory.parent != cache:
        raise RuntimeError("El directorio temporal no pertenece al workspace")
    try:
        yield directory
    finally:
        # Windows scanners or a closing child handle can briefly block deletion.
        # Retry only sharing/access errors, and only for this allocated directory.
        for attempt in range(20):
            try:
                temporary.cleanup()
                break
            except PermissionError as error:
                if os.name != "nt" or getattr(error, "winerror", None) not in (5, 32) or attempt == 19:
                    raise
                time.sleep(0.1)


def request(base, path, token=None, payload=None, *, http=HTTP):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    data = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload).encode()
    with http.open(Request(base + path, data=data, headers=headers), timeout=5) as response:
        return response.read()


def message(base, path, token, payload=None, *, http=HTTP):
    return json.loads(request(base, path, token, payload, http=http))


@contextmanager
def server(environment, log_path, *, tls_directory=None, http=HTTP):
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    base = f"{'https' if tls_directory else 'http'}://127.0.0.1:{port}"
    command = (
        [sys.executable, "-I", "-m", "life_os.manage", "serve", "--tls-dir", str(tls_directory),
         "--host", "127.0.0.1", "--port", str(port)]
        if tls_directory else
        [sys.executable, "-I", "-m", "uvicorn", "life_os.api:create_app", "--factory",
         "--host", "127.0.0.1", "--port", str(port), "--no-proxy-headers"]
    )
    with log_path.open("ab") as log:
        process = subprocess.Popen(
            command,
            cwd=ROOT / "apps" / "server", env=environment,
            stdout=log, stderr=subprocess.STDOUT,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        try:
            deadline = time.monotonic() + 15
            while True:
                if process.poll() is not None:
                    raise RuntimeError("Uvicorn termino antes de estar disponible")
                try:
                    health = json.loads(request(base, "/health", http=http))
                    if health.get("status") == "ok":
                        break
                except (URLError, TimeoutError, OSError):
                    pass
                if time.monotonic() >= deadline:
                    raise RuntimeError("El servidor no estuvo disponible en 15 segundos")
                time.sleep(0.1)
            yield base
        finally:
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


def main():
    if sys.prefix == sys.base_prefix:
        raise SystemExit("Ejecutar con .venv/Scripts/python.exe, no con Python global.")
    with temporary_workspace("server-smoke-") as directory:
        token = secrets.token_urlsafe(32)
        environment = {**os.environ, "LIFE_OS_MODE": "development", "LIFE_OS_DEV_TOKEN": token,
                       "LIFE_OS_DATABASE": str(directory / "smoke.sqlite3")}
        log = directory / "uvicorn.log"
        template = json.loads((ROOT / "contracts/examples/create-routine.json").read_text(encoding="utf-8"))
        routine = template["operations"][0]
        session = {
            "operation_id": str(uuid4()), "entity_type": "session", "entity_id": str(uuid4()),
            "base_version": 0, "action": "upsert",
            "data": {
                "routine_id": routine["entity_id"], "routine_version": 1,
                "routine_snapshot": copy.deepcopy(routine["data"]),
                "started_at": "2026-10-28T07:15:00-04:00", "finished_at": "2026-10-28T08:00:00-04:00",
                "sets": [{"id": str(uuid4()), "exercise_id": routine["data"]["exercises"][0]["id"],
                          "weight": "22.500", "unit": "kg", "reps": 10, "kind": "work"}],
            },
        }
        training = {"protocol_version": 1, "operations": [session]}
        web_built = (ROOT / "apps/web/dist/index.html").is_file()
        try:
            with server(environment, log) as base:
                result = message(base, "/api/v1/sync/push", token, template)
                assert result["results"][0]["status"] == "applied"
                saved = message(base, "/api/v1/sync/push", token, training)
                assert saved["results"][0]["status"] == "applied"
                assert message(base, "/api/v1/sync/push", token, training) == saved
                if web_built:
                    html = request(base, "/").decode()
                    assert 'id="app"' in html
                    script = re.search(r'src="(/assets/[^"\s]+\.js)"', html)
                    assert script and request(base, script.group(1))
            with server(environment, log) as base:
                assert message(base, "/api/v1/sync/push", token, training) == saved
                records = message(base, "/api/v1/sync/pull", token)["changes"]
                assert len(records) == 2
                assert records[1]["data"]["sets"][0]["weight"] == "22.500"
                update = copy.deepcopy(routine)
                update.update(operation_id=str(uuid4()), base_version=1)
                update["data"]["name"] = "Push actualizado"
                update_payload = {"protocol_version": 1, "operations": [update]}
                assert message(base, "/api/v1/sync/push", token, update_payload)["results"][0]["status"] == "applied"
                update["operation_id"] = str(uuid4())
                assert message(base, "/api/v1/sync/push", token, update_payload)["results"][0]["status"] == "conflict"
        except Exception:
            print(log.read_text(encoding="utf-8", errors="replace")[-4000:], file=sys.stderr)
            raise
    print("OK: HTTP real, rutina, sesion, reintentos, reinicio y conflicto.")
    print("OK: web compilada y recurso JS servidos." if web_built else "Web omitida: compilar con pnpm build para incluirla en esta prueba.")
    print("Base temporal eliminada; no se utilizaron datos ni credenciales personales.")


if __name__ == "__main__":
    main()
