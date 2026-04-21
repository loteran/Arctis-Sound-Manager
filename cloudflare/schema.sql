-- ASM Telemetry — D1 schema
-- Apply with: wrangler d1 execute asm-telemetry --file=schema.sql

CREATE TABLE IF NOT EXISTS stats (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    distro  TEXT    NOT NULL DEFAULT 'Unknown',
    headset TEXT    NOT NULL DEFAULT 'Unknown',
    version TEXT    NOT NULL DEFAULT 'Unknown',
    ts      INTEGER NOT NULL  -- Unix timestamp in ms
);

-- Indexes for fast GROUP BY queries
CREATE INDEX IF NOT EXISTS idx_distro  ON stats (distro);
CREATE INDEX IF NOT EXISTS idx_headset ON stats (headset);
CREATE INDEX IF NOT EXISTS idx_version ON stats (version);

-- Unique users table — one row per (distro, headset) pair
-- version and last_seen are updated on each submission
CREATE TABLE IF NOT EXISTS users (
    distro      TEXT    NOT NULL DEFAULT 'Unknown',
    headset     TEXT    NOT NULL DEFAULT 'Unknown',
    version     TEXT    NOT NULL DEFAULT 'Unknown',
    first_seen  INTEGER NOT NULL,
    last_seen   INTEGER NOT NULL,
    PRIMARY KEY (distro, headset)
);

CREATE INDEX IF NOT EXISTS idx_users_version ON users (version);
