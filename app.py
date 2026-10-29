"""Cloud entrypoint: persistent Postgres and existing device authentication only."""

import os
from pathlib import Path

from life_os.api import Settings, create_app


def cloud_app():
    url = os.environ.get("LIFE_OS_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("Configure LIFE_OS_DATABASE_URL or DATABASE_URL for the backend.")
    return create_app(Settings(
        database=Path("/tmp/life-os-unused.sqlite3"),
        database_url=url,
        mode="paired",
        contract=Path(__file__).resolve().parent / "contracts" / "sync-v1.schema.json",
        web_directory=Path("/tmp/life-os-no-static-files"),
    ))


app = cloud_app()
