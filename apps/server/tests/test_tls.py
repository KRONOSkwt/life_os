import json
from datetime import datetime, timedelta, timezone

import pytest

from life_os.manage import main
from life_os.auth import Credentials
from life_os.store import Store
from life_os.tls import covers_host, create_identity, identity_info, load_identity


def test_identity_covers_only_expected_names_and_is_not_overwritten(tmp_path):
    folder = tmp_path / "tls"
    info = create_identity(folder, ["192.168.1.10", "life-os.local"])
    certificate = load_identity(folder)
    assert info == identity_info(certificate)
    assert len(info["certificate_sha256"]) == 64
    assert covers_host(certificate, "127.0.0.1")
    assert covers_host(certificate, "192.168.1.10")
    assert covers_host(certificate, "LIFE-OS.LOCAL")
    assert not covers_host(certificate, "192.168.1.11")
    key = (folder / "server-key.pem").read_bytes()
    with pytest.raises(FileExistsError):
        create_identity(folder, ["another.local"])
    assert (folder / "server-key.pem").read_bytes() == key


@pytest.mark.parametrize("days", [-366, 1])
def test_expired_or_future_identity_is_rejected(tmp_path, days):
    folder = tmp_path / "tls"
    create_identity(folder, [], now=datetime.now(timezone.utc) + timedelta(days=days))
    with pytest.raises(ValueError, match="expired or not yet valid"):
        load_identity(folder)


def test_certificate_key_mismatch_is_rejected(tmp_path):
    first, second = tmp_path / "first", tmp_path / "second"
    create_identity(first, [])
    create_identity(second, [])
    (first / "server-key.pem").write_bytes((second / "server-key.pem").read_bytes())
    with pytest.raises(ValueError, match="do not match"):
        load_identity(first)


def test_cli_pair_bundle_and_administration_do_not_expose_private_key(tmp_path, capsys):
    directory, database = tmp_path / "tls", tmp_path / "cli.sqlite3"
    main(["tls-init", "--tls-dir", str(directory), "--host", "192.168.1.10"])
    info = json.loads(capsys.readouterr().out)
    main(["pair", "--tls-dir", str(directory), "--database", str(database), "--url", "https://192.168.1.10:8443"])
    raw = capsys.readouterr().out
    bundle = json.loads(raw)
    assert bundle["certificate_sha256"] == info["certificate_sha256"]
    assert bundle["server_url"] == "https://192.168.1.10:8443"
    assert bundle["pairing_code"] and bundle["server_id"]
    assert "PRIVATE KEY" not in raw
    main(["devices", "--database", str(database)])
    assert json.loads(capsys.readouterr().out) == []
    access = Credentials(Store(database))
    grant = access.exchange(bundle["pairing_code"], "Phone")
    main(["devices", "--database", str(database)])
    device_list = json.loads(capsys.readouterr().out)
    assert device_list[0]["device_id"] == grant["device_id"]
    assert "access_token" not in device_list[0] and "token_hash" not in device_list[0]
    main(["revoke", grant["device_id"], "--database", str(database)])
    assert json.loads(capsys.readouterr().out) == {"revoked": True}
    assert access.authenticate(grant["access_token"]) is None
    with pytest.raises(ValueError, match="absent"):
        main(["pair", "--tls-dir", str(directory), "--database", str(database), "--url", "https://192.168.1.11:8443"])


@pytest.mark.parametrize("host", ["https://localhost", "localhost:8443", "*.local", "host/name"])
def test_invalid_certificate_hosts_are_rejected_before_creating_files(tmp_path, host):
    with pytest.raises((ValueError, UnicodeError)):
        create_identity(tmp_path / "tls", [host])
    assert not (tmp_path / "tls").exists()
