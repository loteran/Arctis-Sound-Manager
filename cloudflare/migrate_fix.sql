-- Fix migration: stats exists (no product_id), users missing entirely
ALTER TABLE stats ADD COLUMN product_id TEXT NOT NULL DEFAULT 'Unknown';

CREATE INDEX IF NOT EXISTS idx_product_id ON stats (product_id);

CREATE TABLE IF NOT EXISTS users (
    distro      TEXT    NOT NULL DEFAULT 'Unknown',
    headset     TEXT    NOT NULL DEFAULT 'Unknown',
    product_id  TEXT    NOT NULL DEFAULT 'Unknown',
    version     TEXT    NOT NULL DEFAULT 'Unknown',
    first_seen  INTEGER NOT NULL,
    last_seen   INTEGER NOT NULL,
    PRIMARY KEY (distro, headset)
);

CREATE INDEX IF NOT EXISTS idx_users_version ON users (version);
