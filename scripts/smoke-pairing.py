"""Verify TLS and device pairing over real sockets, using disposable identities."""

import hashlib
import json
import os
import runpy
import secrets
import socket
import ssl
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPSHandler, ProxyHandler, build_opener

from life_os.auth import Credentials
from life_os.store import Store
from life_os.tls import create_identity


ROOT = Path(__file__).resolve().parents[1]
helpers = runpy.run_path(str(ROOT / "scripts/smoke-server.py"), run_name="http_helpers")
server, message = helpers["server"], helpers["message"]
temporary_workspace = helpers["temporary_workspace"]


def verify_peer(base, context, expected):
    url = urlsplit(base)
    with socket.create_connection((url.hostname, url.port), timeout=5) as connection:
        with context.wrap_socket(connection, server_hostname=url.hostname) as secure:
            actual = hashlib.sha256(secure.getpeercert(binary_form=True)).hexdigest()
            if not secrets.compare_digest(actual, expected):
                raise ssl.SSLError("Certificate fingerprint mismatch")


def denied(function, status):
    try:
        function()
    except HTTPError as error:
        assert error.code == status, error.code
    else:
        raise AssertionError(f"Expected HTTP {status}")


def main():
    if sys.prefix == sys.base_prefix:
        raise SystemExit("Ejecutar mediante el Python de .venv.")
    with temporary_workspace("pairing-smoke-") as directory:
        tls = directory / "tls"
        identity = create_identity(tls, [])
        context = ssl.create_default_context(cafile=str(tls / "server-cert.pem"))
        trusted = build_opener(ProxyHandler({}), HTTPSHandler(context=context))
        untrusted = build_opener(ProxyHandler({}), HTTPSHandler(context=ssl.create_default_context()))
        environment = {**os.environ, "LIFE_OS_DATABASE": str(directory / "paired.sqlite3")}
        access = Credentials(Store(Path(environment["LIFE_OS_DATABASE"])))
        challenge = access.issue_pairing()
        log = directory / "uvicorn.log"
        try:
            with server(environment, log, tls_directory=tls, http=trusted) as base:
                try:
                    message(base, "/health", None, http=untrusted)
                except URLError as error:
                    assert isinstance(error.reason, ssl.SSLCertVerificationError)
                else:
                    raise AssertionError("Untrusted certificate was accepted")
                try:
                    verify_peer(base, context, "0" * 64)
                except ssl.SSLError:
                    pass
                else:
                    raise AssertionError("Wrong fingerprint was accepted")
                verify_peer(base, context, identity["certificate_sha256"])
                grant = message(base, "/api/v1/pairing/exchange", None,
                                {"pairing_code": challenge["pairing_code"], "device_name": "TLS smoke phone"}, http=trusted)
                assert grant["server_id"] == access.store.database_id
                token = grant["access_token"]
                denied(lambda: message(base, "/api/v1/pairing/exchange", None,
                                       {"pairing_code": challenge["pairing_code"], "device_name": "Again"}, http=trusted), 401)
                example = json.loads((ROOT / "contracts/examples/create-routine.json").read_text(encoding="utf-8"))
                result = message(base, "/api/v1/sync/push", token, example, http=trusted)
                assert result["results"][0]["status"] == "applied"
            with server(environment, log, tls_directory=tls, http=trusted) as base:
                assert len(message(base, "/api/v1/sync/pull", token, http=trusted)["changes"]) == 1
                assert message(base, "/api/v1/sync/push", token, example, http=trusted) == result
                assert access.revoke(grant["device_id"])
                denied(lambda: message(base, "/api/v1/sync/pull", token, http=trusted), 401)
                denied(lambda: message(base, "/api/v1/sync/push", token, example, http=trusted), 401)
                assert len(access.store.pull(None, 100)["changes"]) == 1
        except Exception:
            print(log.read_text(encoding="utf-8", errors="replace")[-4000:], file=sys.stderr)
            raise
    print("OK: TLS verificado, certificado no confiable y huella incorrecta rechazados.")
    print("OK: vinculacion unica, sincronizacion, reinicio y revocacion sobre HTTPS real.")
    print("Identidad, credenciales y base temporales eliminadas; ningun servidor queda activo.")


if __name__ == "__main__":
    main()
