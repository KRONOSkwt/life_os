import io
import json
import os
import sqlite3
import warnings
import zipfile
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from life_os import backup, manage
from life_os.api import ROOT
from life_os.auth import Credentials
from life_os.store import Store
from life_os.tls import create_identity, identity_info, load_identity

PASSWORD = "una frase de prueba para recuperar"


@pytest.fixture
def source(tmp_path):
    store = Store(tmp_path / "source.sqlite3")
    tls = tmp_path / "tls"
    create_identity(tls, ["192.168.1.10"])
    return store, tls


def example():
    return json.loads((ROOT / "contracts/examples/create-routine.json").read_text())["operations"][0]


def write_backup(tmp_path, source):
    store, tls = source
    output = tmp_path / "backup.lifeos-backup"
    result = backup.create_backup(store.path, output, PASSWORD, tls_directory=tls)
    return output, result


def test_round_trip_includes_uncheckpointed_wal_tls_devices_and_receipts(tmp_path, source):
    store, tls = source
    access = Credentials(store)
    with sqlite3.connect(store.path) as held:
        held.execute("PRAGMA wal_autocheckpoint=0")
        held.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        active = access.exchange(access.issue_pairing()["pairing_code"], "Honor X8A")
        revoked = access.exchange(access.issue_pairing()["pairing_code"], "Old phone")
        access.revoke(revoked["device_id"])
        operation = example()
        receipt = store.apply(active["device_id"], operation)
        assert Path(str(store.path) + "-wal").stat().st_size > 0
        output, result = write_backup(tmp_path, source)
    assert result["counts"]["records"] == 1
    encrypted = output.read_bytes()
    assert b"Push de ejemplo" not in encrypted and b"PRIVATE KEY" not in encrypted
    destination = tmp_path / "recovered"
    backup.restore_backup(output, destination, PASSWORD)
    restored = Store(destination / "life-os.sqlite3")
    assert restored.database_id == store.database_id
    assert restored.pull(None, 100) == store.pull(None, 100)
    assert Credentials(restored).authenticate(active["access_token"]) == active["device_id"]
    assert Credentials(restored).authenticate(revoked["access_token"]) is None
    assert restored.apply(active["device_id"], operation) == receipt
    assert len(restored.pull(None, 100)["changes"]) == 1
    assert (destination / "tls/server-key.pem").read_bytes() == (tls / "server-key.pem").read_bytes()
    assert identity_info(load_identity(destination / "tls")) == identity_info(load_identity(tls))


@pytest.mark.parametrize("damage", ["password", "body", "salt", "truncated", "version"])
def test_wrong_password_and_tampering_never_create_destination(tmp_path, source, damage):
    output, _ = write_backup(tmp_path, source)
    raw = bytearray(output.read_bytes())
    password = PASSWORD
    if damage == "password":
        password = "otra contraseña incorrecta"
    elif damage == "body":
        raw[-1] ^= 1
    elif damage == "salt":
        raw[len(backup.MAGIC)] ^= 1
    elif damage == "version":
        raw[len(backup.MAGIC) - 1] = 2
    else:
        raw = raw[:20]
    output.write_bytes(raw)
    destination = tmp_path / "recovered"
    with pytest.raises(ValueError):
        backup.restore_backup(output, destination, password)
    assert not destination.exists()


def test_existing_archive_and_directory_are_preserved(tmp_path, source):
    output, _ = write_backup(tmp_path, source)
    before = output.read_bytes()
    with pytest.raises(FileExistsError):
        backup.create_backup(source[0].path, output, PASSWORD, tls_directory=source[1])
    assert output.read_bytes() == before
    target = tmp_path / "recovered"
    target.mkdir()
    marker = target / "important.txt"
    marker.write_text("keep me")
    with pytest.raises(FileExistsError):
        backup.restore_backup(output, target, PASSWORD)
    assert marker.read_text() == "keep me"


@pytest.mark.parametrize("problem", ["missing-database", "unknown-schema", "mismatched-key", "short-password"])
def test_invalid_source_does_not_create_archive(tmp_path, source, problem):
    store, tls = source
    database, password = store.path, PASSWORD
    if problem == "missing-database":
        database = tmp_path / "missing.sqlite3"
    elif problem == "unknown-schema":
        with store.connection() as connection:
            connection.execute("PRAGMA user_version=99")
    elif problem == "mismatched-key":
        other = tmp_path / "other-tls"
        create_identity(other, [])
        (tls / "server-key.pem").write_bytes((other / "server-key.pem").read_bytes())
    else:
        password = "short"
    output = tmp_path / "invalid.lifeos-backup"
    with pytest.raises(ValueError):
        backup.create_backup(database, output, password, tls_directory=tls)
    assert not output.exists() and not (tmp_path / "missing.sqlite3").exists()


def test_database_only_backup_is_explicit_and_restorable(tmp_path, source):
    output = tmp_path / "development.lifeos-backup"
    backup.create_backup(source[0].path, output, PASSWORD, tls_directory=None)
    result = backup.restore_backup(output, tmp_path / "development", PASSWORD)
    assert result["certificate_sha256"] is None
    assert not (tmp_path / "development/tls").exists()


def test_authenticated_archive_still_rejects_unexpected_paths(tmp_path, source):
    output, _ = write_backup(tmp_path, source)
    manifest, files = backup._read_backup(output, PASSWORD)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("manifest.json", json.dumps(manifest))
        for name, data in files.items():
            archive.writestr(name, data)
        archive.writestr("../escaped.txt", "outside")
    salt, nonce = os.urandom(16), os.urandom(12)
    header = backup.MAGIC + salt + nonce
    output.write_bytes(header + AESGCM(backup._key(PASSWORD, salt)).encrypt(nonce, buffer.getvalue(), header))
    with pytest.raises(ValueError, match="inesperados"):
        backup.restore_backup(output, tmp_path / "recovered", PASSWORD)
    assert not (tmp_path / "recovered").exists() and not (tmp_path / "escaped.txt").exists()


def test_restore_write_failure_cleans_only_its_new_directory(tmp_path, source, monkeypatch):
    output, _ = write_backup(tmp_path, source)
    real_write = backup._write_private

    def fail_key(path, content):
        if path.name == "server-key.pem":
            raise OSError("disk full")
        return real_write(path, content)

    monkeypatch.setattr(backup, "_write_private", fail_key)
    with pytest.raises(OSError, match="disk full"):
        backup.restore_backup(output, tmp_path / "recovered", PASSWORD)
    assert not (tmp_path / "recovered").exists()
    assert output.exists() and source[0].path.exists()


def test_cli_prompts_without_exposing_password_and_preserves_existing_source(tmp_path, source, monkeypatch, capsys):
    values = iter([PASSWORD, PASSWORD, PASSWORD])
    monkeypatch.setattr(manage.getpass, "getpass", lambda prompt: next(values))
    output, target = tmp_path / "cli.lifeos-backup", tmp_path / "cli-restored"
    manage.main(["backup", "--database", str(source[0].path), "--tls-dir", str(source[1]), "--output", str(output)])
    manage.main(["restore", "--input", str(output), "--destination", str(target)])
    logged = capsys.readouterr().out
    assert PASSWORD not in logged and "PRIVATE KEY" not in logged
    assert target.is_dir() and source[0].path.exists()


def test_cli_rejects_mismatched_confirmation_and_echo_fallback(monkeypatch):
    values = iter([PASSWORD, "different"])
    monkeypatch.setattr(manage.getpass, "getpass", lambda prompt: next(values))
    with pytest.raises(ValueError, match="no coinciden"):
        manage._password(confirm=True)

    def insecure_prompt(prompt):
        warnings.warn("echo", manage.getpass.GetPassWarning)

    monkeypatch.setattr(manage.getpass, "getpass", insecure_prompt)
    with pytest.raises(ValueError, match="terminal interactiva"):
        manage._password(confirm=False)


def test_sqlite_backup_refuses_postgres_configuration_before_prompting(tmp_path, monkeypatch):
    monkeypatch.setenv("LIFE_OS_DATABASE_URL", "postgresql://private:secret@example.invalid/cloud")
    monkeypatch.setattr(manage.getpass, "getpass", lambda prompt: pytest.fail("No debe pedir contraseña"))
    output = tmp_path / "wrong-database.lifeos-backup"
    with pytest.raises(ValueError, match="no respalda PostgreSQL"):
        manage.main(["backup", "--output", str(output)])
    assert not output.exists()
