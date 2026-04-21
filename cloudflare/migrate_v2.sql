-- Migration v2: add product_id column to existing stats and users tables
-- Run: wrangler d1 execute asm-telemetry --file=cloudflare/migrate_v2.sql

ALTER TABLE stats ADD COLUMN product_id TEXT NOT NULL DEFAULT 'Unknown';
ALTER TABLE users ADD COLUMN product_id TEXT NOT NULL DEFAULT 'Unknown';

CREATE INDEX IF NOT EXISTS idx_product_id ON stats (product_id);
