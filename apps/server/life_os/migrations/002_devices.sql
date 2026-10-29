CREATE TABLE devices (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    token_hash TEXT NOT NULL UNIQUE,
    created_at INTEGER NOT NULL,
    revoked_at INTEGER
);

CREATE TABLE pairing (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    code_hash TEXT NOT NULL,
    expires_at INTEGER NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0)
);

PRAGMA user_version = 2;
