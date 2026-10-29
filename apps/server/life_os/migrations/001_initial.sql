CREATE TABLE metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE records (
    entity_type TEXT NOT NULL CHECK (entity_type IN ('routine', 'session')),
    entity_id TEXT NOT NULL,
    version INTEGER NOT NULL CHECK (version > 0),
    deleted INTEGER NOT NULL CHECK (deleted IN (0, 1)),
    payload TEXT NOT NULL,
    PRIMARY KEY (entity_type, entity_id)
);

CREATE TABLE operations (
    device_id TEXT NOT NULL,
    operation_id TEXT NOT NULL,
    request TEXT NOT NULL,
    result TEXT NOT NULL,
    PRIMARY KEY (device_id, operation_id)
);

CREATE TABLE changes (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    record TEXT NOT NULL
);

PRAGMA user_version = 1;
