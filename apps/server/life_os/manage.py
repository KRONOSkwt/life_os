"""Local owner commands. Administration is deliberately not exposed through HTTP."""

import argparse
import getpass
import json
import os
import secrets
import ssl
import warnings
from pathlib import Path
from urllib.parse import urlsplit

import uvicorn

from .api import ROOT, Settings, create_app
from .auth import Credentials
from .backup import create_backup, restore_backup
from .store import Store
from .postgres import PostgresStore, open_store
from .tls import covers_host, create_identity, identity_info, load_identity


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Administrar el servidor personal Life OS")
    commands = result.add_subparsers(dest="command", required=True)
    for name in ("tls-init", "tls-info", "pair", "devices", "revoke", "serve", "backup"):
        command = commands.add_parser(name)
        command.add_argument("--database", type=Path,
                             default=Path(os.environ.get("LIFE_OS_DATABASE", ROOT / ".data/life-os.sqlite3")))
        if name in ("tls-init", "tls-info", "pair", "serve", "backup"):
            command.add_argument("--tls-dir", type=Path, default=ROOT / ".data/tls")
        if name == "tls-init":
            command.add_argument("--host", action="append", default=[])
        elif name == "pair":
            command.add_argument("--url", required=True)
        elif name == "revoke":
            command.add_argument("device_id")
        elif name == "serve":
            command.add_argument("--host", default="127.0.0.1")
            command.add_argument("--port", type=int, default=8443)
        elif name == "backup":
            command.add_argument("--output", type=Path, required=True)
            command.add_argument("--without-tls", action="store_true",
                                 help="Solo para un servidor de desarrollo que no tenga identidad HTTPS")
    command = commands.add_parser("restore")
    command.add_argument("--input", type=Path, required=True)
    command.add_argument("--destination", type=Path, required=True,
                         help="Carpeta nueva; nunca se sobrescribe una existente")
    commands.add_parser("postgres-init", help="Inicializar una base PostgreSQL vacía configurada por entorno")
    return result


def _password(*, confirm: bool) -> str:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", getpass.GetPassWarning)
            value = getpass.getpass("Contraseña de la copia: ")
            if confirm:
                repeated = getpass.getpass("Repite la contraseña: ")
                if not secrets.compare_digest(value.encode("utf-8"), repeated.encode("utf-8")):
                    raise ValueError("Las contraseñas no coinciden; no se creó la copia.")
            return value
    except (getpass.GetPassWarning, EOFError):
        raise ValueError("Usa una terminal interactiva que oculte la contraseña.") from None


def main(argv=None) -> None:
    arguments = parser().parse_args(argv)
    if arguments.command == "postgres-init":
        url = os.environ.get("LIFE_OS_DATABASE_URL")
        if not url:
            raise ValueError("Configura LIFE_OS_DATABASE_URL en el entorno privado.")
        store = PostgresStore.initialize(url)
        output = {"initialized": True, "database": "postgresql", "server_id": store.database_id}
    elif arguments.command == "backup":
        if os.environ.get("LIFE_OS_DATABASE_URL"):
            raise ValueError("backup cubre SQLite; no respalda PostgreSQL. Usa una copia PostgreSQL comprobada.")
        output = create_backup(arguments.database, arguments.output, _password(confirm=True),
                               tls_directory=None if arguments.without_tls else arguments.tls_dir)
    elif arguments.command == "restore":
        output = restore_backup(arguments.input, arguments.destination, _password(confirm=False))
    elif arguments.command == "tls-init":
        output = create_identity(arguments.tls_dir, arguments.host)
    elif arguments.command == "tls-info":
        output = identity_info(load_identity(arguments.tls_dir))
    elif arguments.command == "serve":
        certificate = load_identity(arguments.tls_dir)
        if not 1 <= arguments.port <= 65535 or not covers_host(certificate, arguments.host):
            raise ValueError("Bind address must be included in the certificate and port must be valid")
        settings = Settings(database=arguments.database, mode="paired",
                            database_url=os.environ.get("LIFE_OS_DATABASE_URL"))
        uvicorn.run(create_app(settings), host=arguments.host, port=arguments.port,
                    ssl_certfile=str(arguments.tls_dir / "server-cert.pem"),
                    ssl_keyfile=str(arguments.tls_dir / "server-key.pem"),
                    ssl_version=ssl.PROTOCOL_TLS_SERVER, proxy_headers=False, server_header=False)
        return
    else:
        access = Credentials(open_store(arguments.database, os.environ.get("LIFE_OS_DATABASE_URL")))
        if arguments.command == "pair":
            certificate = load_identity(arguments.tls_dir)
            url = urlsplit(arguments.url)
            if (url.scheme != "https" or not url.hostname or url.username or url.password
                    or url.query or url.fragment or url.path not in ("", "/")):
                raise ValueError("Use an HTTPS server origin without credentials, path, query or fragment")
            if url.port is not None and not 1 <= url.port <= 65535:
                raise ValueError("Invalid server port")
            if not covers_host(certificate, url.hostname):
                raise ValueError("Server hostname is absent from the TLS certificate")
            output = {"protocol_version": 1, "server_url": arguments.url.rstrip("/"),
                      "server_id": access.store.database_id, **identity_info(certificate),
                      **access.issue_pairing()}
        elif arguments.command == "devices":
            output = access.devices()
        else:
            output = {"revoked": access.revoke(arguments.device_id)}
    print(json.dumps(output, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, RuntimeError) as error:
        raise SystemExit(str(error)) from error
    except KeyboardInterrupt:
        raise SystemExit("Operación cancelada.") from None
