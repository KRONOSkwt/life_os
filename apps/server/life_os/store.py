"""SQLite transactions keep record, change log and idempotency receipt together."""

import base64
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4


class OperationReused(ValueError):
    pass


class InvalidCursor(ValueError):
    pass


def canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


class Store:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1, 2):
                raise RuntimeError("Unsupported database version; no migration performed")
            if version == 0:
                migration = Path(__file__).parent / "migrations" / "001_initial.sql"
                for statement in migration.read_text(encoding="utf-8").split(";"):
                    if statement.strip():
                        db.execute(statement)
                db.execute(
                    "INSERT INTO metadata VALUES ('database_id', ?)", (str(uuid4()),)
                )
            if version < 2:
                migration = Path(__file__).parent / "migrations" / "002_devices.sql"
                for statement in migration.read_text(encoding="utf-8").split(";"):
                    if statement.strip():
                        db.execute(statement)
            self.database_id = db.execute(
                "SELECT value FROM metadata WHERE key = 'database_id'"
            ).fetchone()[0]
            db.commit()
            db.execute("PRAGMA journal_mode = WAL")

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        try:
            db.execute("PRAGMA foreign_keys = ON")
            db.execute("PRAGMA synchronous = FULL")
            yield db
        except BaseException:
            if db.in_transaction:
                db.rollback()
            raise
        finally:
            db.close()

    def apply(self, device_id: str, operation: dict) -> dict:
        request = canonical(operation)
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            previous = db.execute(
                "SELECT request, result FROM operations WHERE device_id = ? AND operation_id = ?",
                (device_id, operation["operation_id"]),
            ).fetchone()
            if previous:
                if previous[0] != request:
                    raise OperationReused("operation_id already used with different content")
                return json.loads(previous[1])

            key = (operation["entity_type"], operation["entity_id"])
            row = db.execute(
                "SELECT payload FROM records WHERE entity_type = ? AND entity_id = ?", key
            ).fetchone()
            current = json.loads(row[0]) if row else None
            version = current["version"] if current else 0
            conflict = operation["base_version"] != version or bool(
                current and current["deleted"]
            )
            if conflict:
                result = {
                    "operation_id": operation["operation_id"],
                    "status": "conflict", "record": current,
                }
            else:
                record = {
                    "entity_type": key[0], "entity_id": key[1],
                    "version": version + 1,
                    "deleted": operation["action"] == "delete",
                    "data": operation["data"],
                }
                serialized = canonical(record)
                db.execute(
                    "INSERT INTO records VALUES (?, ?, ?, ?, ?) "
                    "ON CONFLICT(entity_type, entity_id) DO UPDATE SET "
                    "version = excluded.version, deleted = excluded.deleted, payload = excluded.payload",
                    (*key, record["version"], int(record["deleted"]), serialized),
                )
                db.execute("INSERT INTO changes(record) VALUES (?)", (serialized,))
                result = {
                    "operation_id": operation["operation_id"],
                    "status": "applied", "record": record,
                }
            db.execute(
                "INSERT INTO operations VALUES (?, ?, ?, ?)",
                (device_id, operation["operation_id"], request, canonical(result)),
            )
            db.commit()
            return result

    def pull(self, cursor: str | None, limit: int) -> dict:
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        position = self._decode_cursor(cursor) if cursor is not None else 0
        with self.connection() as db:
            db.execute("BEGIN")
            maximum = db.execute("SELECT COALESCE(MAX(sequence), 0) FROM changes").fetchone()[0]
            if position > maximum:
                raise InvalidCursor("Cursor is ahead of this database; resynchronization required")
            rows = db.execute(
                "SELECT sequence, record FROM changes WHERE sequence > ? ORDER BY sequence LIMIT ?",
                (position, limit + 1),
            ).fetchall()
            page = rows[:limit]
            next_position = page[-1][0] if page else position
            return {
                "protocol_version": 1,
                "changes": [json.loads(row[1]) for row in page],
                "next_cursor": self._encode_cursor(next_position),
                "has_more": len(rows) > limit,
            }

    def _encode_cursor(self, position: int) -> str:
        raw = canonical({"database": self.database_id, "position": position}).encode()
        return base64.urlsafe_b64encode(raw).decode()

    def _decode_cursor(self, cursor: str) -> int:
        try:
            raw = base64.b64decode(cursor, altchars=b"-_", validate=True)
            value = json.loads(raw)
            if not isinstance(value, dict) or set(value) != {"database", "position"}:
                raise ValueError
            if value["database"] != self.database_id:
                raise ValueError
            position = value["position"]
            if type(position) is not int or not 0 <= position <= 9223372036854775807:
                raise ValueError
            return position
        except (ValueError, UnicodeDecodeError) as error:
            raise InvalidCursor("Invalid cursor or cursor belongs to another database") from error
