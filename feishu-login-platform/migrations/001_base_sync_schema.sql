CREATE TABLE IF NOT EXISTS base_records (
  id BIGSERIAL PRIMARY KEY,
  feishu_record_id TEXT NOT NULL UNIQUE,
  base_token TEXT NOT NULL,
  table_id TEXT NOT NULL,
  view_id TEXT,
  row_no INTEGER,
  description TEXT,
  priority TEXT,
  status TEXT,
  remark TEXT,
  planner_name TEXT,
  proposer_name TEXT,
  like_count INTEGER DEFAULT 0,
  feishu_created_time TEXT,
  like_users TEXT,
  rating TEXT,
  screenshot_count INTEGER DEFAULT 0,
  problem_image_count INTEGER DEFAULT 0,
  attachment_count INTEGER DEFAULT 0,
  raw_fields JSONB NOT NULL DEFAULT '{}'::jsonb,
  field_hash TEXT NOT NULL DEFAULT '',
  sync_source TEXT NOT NULL DEFAULT 'import',
  last_sync_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  deleted_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_base_records_priority ON base_records(priority);
CREATE INDEX IF NOT EXISTS idx_base_records_status ON base_records(status);
CREATE INDEX IF NOT EXISTS idx_base_records_planner_name ON base_records(planner_name);
CREATE INDEX IF NOT EXISTS idx_base_records_updated_at ON base_records(updated_at);
CREATE INDEX IF NOT EXISTS idx_base_records_raw_fields ON base_records USING GIN(raw_fields);

CREATE TABLE IF NOT EXISTS base_attachments (
  id BIGSERIAL PRIMARY KEY,
  base_record_id BIGINT REFERENCES base_records(id) ON DELETE CASCADE,
  feishu_record_id TEXT NOT NULL,
  row_no INTEGER,
  field_id TEXT NOT NULL,
  field_name TEXT NOT NULL,
  file_token TEXT NOT NULL UNIQUE,
  original_name TEXT,
  size_bytes BIGINT,
  mime_type TEXT,
  storage_provider TEXT NOT NULL DEFAULT 'local',
  storage_bucket TEXT,
  storage_key TEXT,
  local_path TEXT,
  public_url TEXT,
  checksum TEXT,
  download_status TEXT NOT NULL DEFAULT 'pending',
  download_error TEXT NOT NULL DEFAULT '',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_base_attachments_record ON base_attachments(feishu_record_id);
CREATE INDEX IF NOT EXISTS idx_base_attachments_status ON base_attachments(download_status);

CREATE TABLE IF NOT EXISTS sync_inbox (
  id BIGSERIAL PRIMARY KEY,
  event_id TEXT NOT NULL UNIQUE,
  event_type TEXT NOT NULL,
  base_token TEXT,
  table_id TEXT,
  feishu_record_id TEXT,
  payload JSONB NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',
  retry_count INTEGER NOT NULL DEFAULT 0,
  error TEXT NOT NULL DEFAULT '',
  received_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  processed_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_sync_inbox_status ON sync_inbox(status, received_at);

CREATE TABLE IF NOT EXISTS sync_outbox (
  id BIGSERIAL PRIMARY KEY,
  base_record_id BIGINT REFERENCES base_records(id) ON DELETE SET NULL,
  feishu_record_id TEXT NOT NULL,
  operation TEXT NOT NULL,
  patch_fields JSONB NOT NULL DEFAULT '{}'::jsonb,
  status TEXT NOT NULL DEFAULT 'pending',
  retry_count INTEGER NOT NULL DEFAULT 0,
  next_retry_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  error TEXT NOT NULL DEFAULT '',
  created_by TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  pushed_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_sync_outbox_status ON sync_outbox(status, next_retry_at);
