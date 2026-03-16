-- OpenBrain self-contained Postgres schema (trimmed)
-- Core inbox and vectors tables plus indexes, based on old/table_v2.sql.

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

CREATE TABLE IF NOT EXISTS ob_inbox (
  id               UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
  raw_text         TEXT,
  source           TEXT,
  type             TEXT,
  fields           JSONB,
  status           TEXT        DEFAULT 'pending',
  confidence       FLOAT,
  session_id       TEXT,
  filename         TEXT,
  filetype         TEXT,
  verified         BOOLEAN     DEFAULT FALSE,
  istemplate       BOOLEAN     DEFAULT FALSE,
  isgenerated      BOOLEAN     DEFAULT FALSE,
  vectorised       BOOLEAN     DEFAULT FALSE,
  vectorised_at    TIMESTAMPTZ,
  retrieval_count  INT         DEFAULT 0,
  version          INT         DEFAULT 0,
  last_surfaced    TIMESTAMPTZ,
  created_at       TIMESTAMPTZ DEFAULT NOW(),
  updated_at       TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS ob_vectors (
  id               UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
  inbox_id         UUID        REFERENCES ob_inbox(id) ON DELETE CASCADE,
  chunk_index      INT,
  chunk_text       TEXT,
  retrieval_count  INT         DEFAULT 0,
  last_surfaced    TIMESTAMPTZ,
  created_at       TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_inbox_type         ON ob_inbox(type);
CREATE INDEX IF NOT EXISTS idx_inbox_status       ON ob_inbox(status);
CREATE INDEX IF NOT EXISTS idx_inbox_source       ON ob_inbox(source);
CREATE INDEX IF NOT EXISTS idx_inbox_session      ON ob_inbox(session_id);
CREATE INDEX IF NOT EXISTS idx_inbox_last_surf    ON ob_inbox(last_surfaced);
CREATE INDEX IF NOT EXISTS idx_inbox_filename     ON ob_inbox(filename);

CREATE INDEX IF NOT EXISTS idx_vectors_inbox_id   ON ob_vectors(inbox_id);
CREATE INDEX IF NOT EXISTS idx_vectors_last_surf  ON ob_vectors(last_surfaced);

