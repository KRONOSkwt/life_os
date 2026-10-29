"""Run the Kotlin client against disposable FastAPI HTTPS, without a phone."""

import json
import os
import runpy
import ssl
import subprocess
import sys
from pathlib import Path
from urllib.request import HTTPSHandler, ProxyHandler, build_opener

from life_os.auth import Credentials
from life_os.store import Store
from life_os.tls import create_identity

ROOT = Path(__file__).resolve().parents[1]
helpers = runpy.run_path(str(ROOT / "scripts/smoke-server.py"), run_name="http_helpers")


def main():
    if sys.prefix == sys.base_prefix:
        raise SystemExit("Ejecutar con .venv/Scripts/python.exe -I scripts/smoke-android.py")
    with helpers["temporary_workspace"]("android-smoke-") as directory:
        tls = directory / "tls"
        identity = create_identity(tls, [])
        store = Store(directory / "server.sqlite3")
        access = Credentials(store)
        example = json.loads((ROOT / "contracts/examples/create-routine.json").read_text(encoding="utf-8"))
        # Seed the routine as a separate device, as the web does.
        store.apply("android-smoke-seed", example["operations"][0])
        trusted = build_opener(ProxyHandler({}), HTTPSHandler(context=ssl.create_default_context(cafile=str(tls / "server-cert.pem"))))
        environment = {**os.environ, "LIFE_OS_DATABASE": str(store.path)}
        with helpers["server"](environment, directory / "server.log", tls_directory=tls, http=trusted) as base:
            invitation = {"protocol_version": 1, "server_url": base, "server_id": store.database_id,
                          **identity, **access.issue_pairing(ttl=600)}
            invitation_path = directory / "invitation.json"
            invitation_path.write_text(json.dumps(invitation), encoding="utf-8")
            environment["LIFE_OS_TEST_INVITATION"] = str(invitation_path)
            wrapper = ROOT / "apps/android/gradlew.bat"
            command = [str(wrapper), "-p", str(ROOT / "apps/android"), "testDebugUnitTest", "--rerun-tasks",
                       "--tests", "personal.lifeos.ServerJourneyTest", "--console=plain"]
            subprocess.run(command, env=environment, check=True, cwd=ROOT)
            sessions = [item for item in store.pull(None, 100)["changes"] if item["entity_type"] == "session"]
            assert len(sessions) == 1
            assert sessions[0]["data"]["sets"][0]["weight"] == "22.500"
    print("OK: Kotlin/Room + FastAPI HTTPS: vincular, descargar, guardar, reabrir y reenviar sin duplicar.")
    print("No equivale a ejecutar el APK ni a comprobar el Honor X8A. Datos temporales eliminados.")


if __name__ == "__main__":
    main()
