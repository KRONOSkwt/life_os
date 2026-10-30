"""One-time, transactional SQLite-to-Postgres cutover; never overwrite cloud history."""

import hashlib
import json
import sqlite3
from pathlib import Path
from uuid import UUID

from .postgres import PostgresStore
from .store import canonical


COLUMNS = {
    'records': 'entity_type,entity_id,version,deleted,payload',
    'operations': 'device_id,operation_id,request,result',
    'changes': 'sequence,record',
    'devices': 'id,name,token_hash,created_at,revoked_at',
}
ORDER = {'records': 'entity_type,entity_id', 'operations': 'device_id,operation_id',
         'changes': 'sequence', 'devices': 'id'}
RECEIPT = 'sqlite_import_sha256'


def snapshot(source: Path):
    source = source.resolve(strict=True)
    db = sqlite3.connect(source.as_uri() + '?mode=ro', uri=True)
    try:
        db.execute('PRAGMA query_only=ON')
        db.execute('BEGIN')
        if db.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
            raise ValueError('SQLite failed integrity validation; nothing imported.')
        if db.execute('PRAGMA user_version').fetchone()[0] != 2:
            raise ValueError('SQLite schema must be version 2 before migration.')
        identity = str(UUID(db.execute("SELECT value FROM metadata WHERE key='database_id'").fetchone()[0]))
        rows = {table: db.execute(f'SELECT {columns} FROM {table} ORDER BY {ORDER[table]}').fetchall()
                for table, columns in COLUMNS.items()}
        # Decode every persisted JSON before starting a cloud transaction.
        for row in rows['records']:
            record = json.loads(row[4])
            if (record['entity_type'], record['entity_id'], record['version'], int(record['deleted'])) != row[:4]:
                raise ValueError('SQLite record metadata does not match its payload.')
        for _, record in rows['changes']:
            json.loads(record)
        for _, _, request, result in rows['operations']:
            json.loads(request)
            json.loads(result)
        digest = hashlib.sha256(canonical({'identity': identity, 'rows': rows}).encode()).hexdigest()
        return identity, rows, digest
    finally:
        db.close()


def migrate_sqlite(source: Path, target_url: str, *, dry_run=False):
    identity, rows, digest = snapshot(source)
    target = PostgresStore(target_url)
    report = {'server_id': identity, 'counts': {t: len(r) for t, r in rows.items()},
              'sha256': digest, 'dry_run': dry_run}
    with target.connection() as db:
        db.execute('BEGIN IMMEDIATE')
        imported = db.execute('SELECT value FROM metadata WHERE key = ?', (RECEIPT,)).fetchone()
        if imported:
            if imported[0] != digest:
                raise ValueError('A different SQLite snapshot was already imported; nothing changed.')
            return {**report, 'already_imported': True}
        if any(db.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]
               for table in ('records', 'operations', 'changes', 'pairing')):
            raise ValueError('Cloud history or pairing is not empty; refusing to overwrite it.')
        for row in rows['devices']:
            previous = db.execute('SELECT '+COLUMNS['devices']+' FROM devices WHERE id = ?', (row[0],)).fetchone()
            if previous and tuple(previous) != row:
                raise ValueError('A cloud device conflicts with a source device; nothing changed.')
            if not previous:
                db.execute('INSERT INTO devices VALUES (?,?,?,?,?)', row)
        for table in ('records', 'operations', 'changes'):
            columns = COLUMNS[table]
            placeholders = ','.join('?' for _ in columns.split(','))
            for row in rows[table]:
                db.execute(f'INSERT INTO {table} ({columns}) VALUES ({placeholders})', row)
            actual = db.execute(f'SELECT {columns} FROM {table} ORDER BY {ORDER[table]}').fetchall()
            if [tuple(row) for row in actual] != rows[table]:
                raise RuntimeError('Imported content differs from SQLite; transaction rolled back.')
        maximum = max((row[0] for row in rows['changes']), default=0)
        # ALTER SEQUENCE is transactional, unlike setval: dry runs and failures leave it unchanged.
        db.execute(f'ALTER SEQUENCE public.changes_sequence_seq RESTART WITH {maximum + 1}')
        db.execute("UPDATE metadata SET value = ? WHERE key = 'database_id'", (identity,))
        db.execute('INSERT INTO metadata VALUES (?,?)', (RECEIPT, digest))
        if dry_run:
            db.rollback()
        return {**report, 'already_imported': False}
