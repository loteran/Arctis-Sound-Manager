-- Migration v3: anonymous installation_id for exact unique-user tracking
-- Apply with: wrangler d1 execute asm-telemetry --file=cloudflare/migrate_v3.sql

ALTER TABLE stats ADD COLUMN installation_id TEXT NOT NULL DEFAULT 'Unknown';
CREATE INDEX IF NOT EXISTS idx_installation_id ON stats (installation_id);

-- Recreate users table with installation_id as primary key.
-- Existing rows are anonymous and few — safe to reset.
DROP TABLE IF EXISTS users;
CREATE TABLE users (
    installation_id TEXT    PRIMARY KEY,
    distro          TEXT    NOT NULL DEFAULT 'Unknown',
    headset         TEXT    NOT NULL DEFAULT 'Unknown',
    product_id      TEXT    NOT NULL DEFAULT 'Unknown',
    version         TEXT    NOT NULL DEFAULT 'Unknown',
    first_seen      INTEGER NOT NULL,
    last_seen       INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_users_version ON users (version);
