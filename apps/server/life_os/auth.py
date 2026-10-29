"""Opaque device credentials and one-time pairing, administered on the host PC."""

import hashlib
import secrets
import time
from uuid import uuid4

from .store import Store


class PairingDenied(ValueError):
    pass


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("ascii")).hexdigest()


class Credentials:
    def __init__(self, store: Store, clock=time.time):
        self.store = store
        self.clock = clock

    def issue_pairing(self, ttl: int = 300) -> dict:
        if not 1 <= ttl <= 600:
            raise ValueError("Pairing lifetime must be between 1 and 600 seconds")
        alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
        raw = "".join(secrets.choice(alphabet) for _ in range(20))
        expires = int(self.clock()) + ttl
        with self.store.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("DELETE FROM pairing")
            db.execute("INSERT INTO pairing VALUES (1, ?, ?, 0)", (digest(raw), expires))
            db.commit()
        return {"pairing_code": "-".join(raw[index:index + 5] for index in range(0, 20, 5)),
                "expires_at": expires}

    def exchange(self, code: str, name: str) -> dict:
        normalized = code.replace("-", "").upper()
        if len(normalized) != 20 or any(char not in "ABCDEFGHJKLMNPQRSTUVWXYZ23456789" for char in normalized):
            raise PairingDenied("Invalid or expired pairing code")
        name = name.strip()
        if not 1 <= len(name) <= 80:
            raise ValueError("Device name must contain 1 to 80 characters")
        now = int(self.clock())
        granted = None
        with self.store.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT code_hash, expires_at, attempts FROM pairing WHERE singleton = 1").fetchone()
            if row and row[1] > now and row[2] < 5:
                if secrets.compare_digest(row[0], digest(normalized)):
                    device = str(uuid4())
                    token = "los_" + secrets.token_urlsafe(32)
                    db.execute("INSERT INTO devices VALUES (?, ?, ?, ?, NULL)",
                               (device, name, digest(token), now))
                    db.execute("DELETE FROM pairing")
                    granted = {"protocol_version": 1, "server_id": self.store.database_id,
                               "device_id": device, "access_token": token, "token_type": "Bearer"}
                else:
                    db.execute("UPDATE pairing SET attempts = attempts + 1 WHERE singleton = 1")
            db.commit()
        if granted is None:
            raise PairingDenied("Invalid or expired pairing code")
        return granted

    def authenticate(self, token: str) -> str | None:
        if not token.isascii() or not token.startswith("los_") or len(token) != 47:
            return None
        with self.store.connection() as db:
            row = db.execute("SELECT id FROM devices WHERE token_hash = ? AND revoked_at IS NULL",
                             (digest(token),)).fetchone()
        return row[0] if row else None

    def devices(self) -> list[dict]:
        with self.store.connection() as db:
            rows = db.execute("SELECT id, name, created_at, revoked_at FROM devices ORDER BY created_at, id").fetchall()
        return [dict(zip(("device_id", "name", "created_at", "revoked_at"), row)) for row in rows]

    def revoke(self, device_id: str) -> bool:
        with self.store.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            changed = db.execute("UPDATE devices SET revoked_at = ? WHERE id = ? AND revoked_at IS NULL",
                                 (int(self.clock()), device_id)).rowcount
            db.commit()
        return bool(changed)
