"""Exercise the actual deployment entrypoint without touching a cloud database."""

import runpy

import pytest
from fastapi.testclient import TestClient

from life_os.api import ROOT
from life_os.auth import Credentials
from life_os.store import Store


def test_cloud_requires_postgres_before_starting(monkeypatch):
    monkeypatch.delenv("LIFE_OS_DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(RuntimeError, match="Configure LIFE_OS_DATABASE_URL"):
        runpy.run_path(str(ROOT / "app.py"))


def test_cloud_entrypoint_uses_device_auth_and_requires_https(tmp_path, monkeypatch):
    store = Store(tmp_path / "cloud.sqlite3")
    access = Credentials(store)
    grant = access.exchange(access.issue_pairing()["pairing_code"], "Cloud browser")
    seen = []

    def open_store(path, url):
        seen.append(url)
        return store

    monkeypatch.setattr("life_os.api.open_store", open_store)
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused.invalid/test")
    monkeypatch.delenv("LIFE_OS_DATABASE_URL", raising=False)
    monkeypatch.setenv("LIFE_OS_MODE", "development")
    monkeypatch.setenv("LIFE_OS_DEV_TOKEN", "local-token-" + "x" * 40)
    app = runpy.run_path(str(ROOT / "app.py"))["app"]
    assert seen == ["postgresql://unused.invalid/test"]
    with TestClient(app, base_url="https://testserver") as client:
        assert client.get("/health").json()["mode"] == "paired-https"
        assert client.get("/api/v1/sync/pull").status_code == 401
        assert client.get("/api/v1/sync/pull", headers={
            "Authorization": "Bearer local-token-" + "x" * 40,
        }).status_code == 401
        response = client.get("/api/v1/sync/pull", headers={
            "Authorization": "Bearer " + grant["access_token"],
        })
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
    with TestClient(app, base_url="http://testserver") as client:
        assert client.get("/api/v1/sync/pull", headers={
            "X-Forwarded-Proto": "https",
            "Authorization": "Bearer " + grant["access_token"],
        }).status_code == 400
