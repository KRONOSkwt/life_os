"""Encrypted local snapshots. Restoration only creates a new directory."""

import io
import json
import os
import sqlite3
import zipfile
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from cryptography import x509
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

MAGIC = b"LIFEOSBACKUP\x01"
MAX_BYTES = 256 * 1024 * 1024
DATABASE = "life-os.sqlite3"
CERTIFICATE = "tls/server-cert.pem"
PRIVATE_KEY = "tls/server-key.pem"
TABLES = ("records", "changes", "operations", "devices", "pairing")


def _key(password: str, salt: bytes) -> bytes:
    if not password or len(password.encode("utf-8")) > 1024:
        raise ValueError("La contraseña debe tener entre 1 y 1024 bytes UTF-8.")
    return Scrypt(salt=salt, length=32, n=2**15, r=8, p=1).derive(password.encode("utf-8"))


def _info(connection: sqlite3.Connection) -> dict:
    if connection.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
        raise ValueError("La base no superó la comprobación de integridad.")
    if connection.execute("PRAGMA foreign_key_check").fetchone():
        raise ValueError("La base contiene referencias inconsistentes.")
    if connection.execute("PRAGMA user_version").fetchone()[0] != 2:
        raise ValueError("Solo se admiten bases Life OS con esquema 2.")
    row = connection.execute("SELECT value FROM metadata WHERE key = 'database_id'").fetchone()
    if not row or str(UUID(row[0])) != row[0]:
        raise ValueError("La base no tiene una identidad válida.")
    return {"server_id": row[0], "schema_version": 2,
            "counts": {table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                       for table in TABLES}}


def _fingerprint(files: dict[str, bytes]) -> str | None:
    if CERTIFICATE not in files:
        return None
    certificate = x509.load_pem_x509_certificate(files[CERTIFICATE])
    key = serialization.load_pem_private_key(files[PRIVATE_KEY], password=None)
    encoding, public_format = serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    if (key.public_key().public_bytes(encoding, public_format)
            != certificate.public_key().public_bytes(encoding, public_format)):
        raise ValueError("El certificado y la clave HTTPS no coinciden.")
    # An expired identity must remain recoverable; serving still validates its dates.
    return certificate.fingerprint(hashes.SHA256()).hex()


def _write_private(path: Path, content: bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as target:
            target.write(content)
            target.flush()
            os.fsync(target.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def create_backup(database: Path, output: Path, password: str, *, tls_directory: Path | None) -> dict:
    """Use SQLite's online backup API, including committed WAL data, without copying WAL files."""
    if len(password) < 12:
        raise ValueError("Usa una contraseña de al menos 12 caracteres para la copia.")
    if output.exists():
        raise FileExistsError("El archivo de destino ya existe; no se sobrescribió.")

    def bound(_status, _remaining, total):
        if total * page_size > MAX_BYTES - 128 * 1024:
            raise ValueError("La base supera el límite de 256 MiB de esta versión.")

    try:
        with closing(sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)) as source:
            page_size = source.execute("PRAGMA page_size").fetchone()[0]
            with closing(sqlite3.connect(":memory:")) as snapshot:
                source.backup(snapshot, pages=256, progress=bound)
                # Rebuild the in-memory snapshot as a standalone rollback-journal DB.
                # Otherwise serialize() retains WAL header flags and cannot be deserialized.
                snapshot.execute("VACUUM")
                info = _info(snapshot)
                files = {DATABASE: snapshot.serialize()}
    except sqlite3.Error as error:
        raise ValueError("No se pudo obtener una copia válida de la base existente.") from error
    if tls_directory is not None:
        files[CERTIFICATE] = (tls_directory / "server-cert.pem").read_bytes()
        files[PRIVATE_KEY] = (tls_directory / "server-key.pem").read_bytes()
    fingerprint = _fingerprint(files)
    manifest = {"format_version": 1, "created_at": datetime.now(timezone.utc).isoformat(),
                **info, "certificate_sha256": fingerprint}
    files["manifest.json"] = json.dumps(manifest, ensure_ascii=True).encode("utf-8")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_STORED) as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    salt, nonce = os.urandom(16), os.urandom(12)
    header = MAGIC + salt + nonce
    ciphertext = AESGCM(_key(password, salt)).encrypt(nonce, buffer.getvalue(), header)
    if len(header) + len(ciphertext) > MAX_BYTES:
        raise ValueError("La copia supera el límite de 256 MiB de esta versión.")
    _write_private(output, header + ciphertext)
    return {"backup": str(output), **manifest}


def _read_backup(source: Path, password: str) -> tuple[dict, dict[str, bytes]]:
    with source.open("rb") as stream:
        raw = stream.read(MAX_BYTES + 1)
    header_size = len(MAGIC) + 28
    if len(raw) > MAX_BYTES or len(raw) < header_size + 16 or not raw.startswith(MAGIC):
        raise ValueError("Formato de copia desconocido, incompleto o demasiado grande.")
    header = raw[:header_size]
    salt, nonce = header[len(MAGIC):len(MAGIC) + 16], header[-12:]
    try:
        payload = AESGCM(_key(password, salt)).decrypt(nonce, raw[header_size:], header)
    except InvalidTag:
        raise ValueError("Contraseña incorrecta o copia dañada; no se restauró nada.") from None
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            names = archive.namelist()
            allowed = {"manifest.json", DATABASE}
            if CERTIFICATE in names or PRIVATE_KEY in names:
                allowed |= {CERTIFICATE, PRIVATE_KEY}
            if set(names) != allowed or len(names) != len(allowed):
                raise ValueError("La copia contiene archivos inesperados, duplicados o incompletos.")
            for entry in archive.infolist():
                limit = MAX_BYTES if entry.filename == DATABASE else 64 * 1024
                if entry.compress_type != zipfile.ZIP_STORED or entry.file_size > limit:
                    raise ValueError("La copia contiene un archivo con formato o tamaño inválido.")
            files = {name: archive.read(name) for name in names}
        manifest = json.loads(files.pop("manifest.json"))
        if not isinstance(manifest, dict) or manifest.get("format_version") != 1:
            raise ValueError("Versión del manifiesto no compatible.")
        datetime.fromisoformat(manifest["created_at"])
        with closing(sqlite3.connect(":memory:")) as database:
            database.deserialize(files[DATABASE])
            info = _info(database)
        if any(manifest.get(key) != value for key, value in info.items()):
            raise ValueError("La base y el manifiesto no coinciden.")
        if manifest.get("certificate_sha256") != _fingerprint(files):
            raise ValueError("La identidad HTTPS y el manifiesto no coinciden.")
        return manifest, files
    except (zipfile.BadZipFile, RuntimeError, KeyError, UnicodeError, TypeError, sqlite3.Error) as error:
        raise ValueError("El contenido de la copia no es válido; no se restauró nada.") from error


def restore_backup(source: Path, destination: Path, password: str) -> dict:
    """Authenticate and validate everything before creating an exclusively new destination."""
    if destination.exists():
        raise FileExistsError("La carpeta de destino ya existe; no se sobrescribió.")
    manifest, files = _read_backup(source, password)
    destination.mkdir(mode=0o700)  # No parents or exist_ok: never merge into an existing folder.
    owned_files = []
    tls_created = False
    try:
        if CERTIFICATE in files:
            (destination / "tls").mkdir(mode=0o700)
            tls_created = True
        for name, content in files.items():
            path = destination / name  # Names were checked against a fixed allowlist.
            _write_private(path, content)
            owned_files.append(path)
        marker = destination / "restore-info.json"
        _write_private(marker, json.dumps(manifest, indent=2).encode("utf-8"))
        owned_files.append(marker)
    except BaseException:
        for path in reversed(owned_files):
            path.unlink(missing_ok=True)
        if tls_created:
            (destination / "tls").rmdir()
        destination.rmdir()
        raise
    return {"restored_to": str(destination), **manifest}
