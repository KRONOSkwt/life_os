import sqlite3
from pathlib import Path

import pytest

from life_os.auth import Credentials
from life_os.migrate import migrate_sqlite, snapshot
from life_os.postgres import PostgresStore, _Connection
from life_os.store import Store
from test_postgres import database_url
from test_sync import operation


def source(tmp_path):
    store = Store(tmp_path / 'source.sqlite3')
    access = Credentials(store)
    grant = access.exchange(access.issue_pairing()['pairing_code'], 'Source phone')
    item = operation()
    receipt = store.apply(grant['device_id'], item)
    return store, grant, item, receipt


def test_import_preserves_identity_cursors_receipts_and_existing_web_access(tmp_path, database_url):
    sqlite, phone, item, receipt = source(tmp_path)
    original = snapshot(sqlite.path)
    cursor = sqlite.pull(None, 100)['next_cursor']
    cloud = PostgresStore.initialize(database_url)
    web_access = Credentials(cloud)
    web = web_access.exchange(web_access.issue_pairing()['pairing_code'], 'Existing web browser')
    result = migrate_sqlite(sqlite.path, database_url)
    assert result['counts']['records'] == 1
    cloud = PostgresStore(database_url)
    assert cloud.database_id == sqlite.database_id
    assert cloud.pull(cursor, 100)['changes'] == []
    assert Credentials(cloud).authenticate(web['access_token']) == web['device_id']
    assert Credentials(cloud).authenticate(phone['access_token']) == phone['device_id']
    assert cloud.apply(phone['device_id'], item) == receipt
    assert len(cloud.pull(None, 100)['changes']) == 1
    cloud.apply(phone['device_id'], operation())
    assert len(cloud.pull(cursor, 100)['changes']) == 1
    assert migrate_sqlite(sqlite.path, database_url)['already_imported']
    assert len(cloud.pull(None, 100)['changes']) == 2
    assert snapshot(sqlite.path) == original


def test_import_refuses_cloud_history_and_changed_snapshot(tmp_path, database_url):
    sqlite, _, _, _ = source(tmp_path)
    cloud = PostgresStore.initialize(database_url)
    cloud.apply('existing-web', operation())
    with pytest.raises(ValueError, match='not empty'):
        migrate_sqlite(sqlite.path, database_url)
    assert PostgresStore(database_url).database_id == cloud.database_id
    assert len(cloud.pull(None, 100)['changes']) == 1


def test_dry_run_rolls_back_devices_identity_history_and_sequence(tmp_path, database_url):
    sqlite, _, _, _ = source(tmp_path)
    cloud = PostgresStore.initialize(database_url)
    with cloud.connection() as c:
        before = c.execute('SELECT last_value,is_called FROM changes_sequence_seq').fetchone()
    assert migrate_sqlite(sqlite.path, database_url, dry_run=True)['dry_run']
    with cloud.connection() as c:
        assert c.execute('SELECT last_value,is_called FROM changes_sequence_seq').fetchone() == before
        for table in ('devices','records','operations','changes'):
            assert c.execute('SELECT COUNT(*) FROM '+table).fetchone()[0] == 0
    assert PostgresStore(database_url).database_id == cloud.database_id
    assert not migrate_sqlite(sqlite.path, database_url)['already_imported']
    sqlite.apply('new-source-change', operation())
    with pytest.raises(ValueError, match='different SQLite snapshot'):
        migrate_sqlite(sqlite.path, database_url)


def test_failure_after_insert_rolls_back_import(tmp_path, database_url, monkeypatch):
    sqlite, _, _, _ = source(tmp_path)
    cloud = PostgresStore.initialize(database_url)
    execute = _Connection.execute

    def fail(self, statement, parameters=None):
        if statement.startswith('INSERT INTO operations'):
            raise RuntimeError('Simulated mid-import failure')
        return execute(self, statement, parameters)

    monkeypatch.setattr(_Connection, 'execute', fail)
    with pytest.raises(RuntimeError, match='mid-import'):
        migrate_sqlite(sqlite.path, database_url)
    with cloud.connection() as c:
        for table in ('devices','records','operations','changes'):
            assert c.execute('SELECT COUNT(*) FROM '+table).fetchone()[0] == 0
    assert PostgresStore(database_url).database_id == cloud.database_id


def test_missing_source_is_never_created(tmp_path):
    missing = tmp_path / 'missing.sqlite3'
    with pytest.raises(FileNotFoundError):
        snapshot(missing)
    assert not missing.exists()
