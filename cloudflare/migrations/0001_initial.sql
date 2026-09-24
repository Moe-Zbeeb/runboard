CREATE TABLE IF NOT EXISTS runs (
  project TEXT NOT NULL,
  run_id TEXT NOT NULL,
  name TEXT NOT NULL,
  meta TEXT NOT NULL,
  created REAL NOT NULL,
  updated REAL NOT NULL,
  PRIMARY KEY (project, run_id)
);

CREATE TABLE IF NOT EXISTS metric_batches (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  project TEXT NOT NULL,
  run_id TEXT NOT NULL,
  rows_json TEXT NOT NULL,
  row_count INTEGER NOT NULL,
  created REAL NOT NULL,
  batch_key TEXT NOT NULL UNIQUE
);

CREATE INDEX IF NOT EXISTS runs_created ON runs (created DESC);
CREATE INDEX IF NOT EXISTS metric_batches_run ON metric_batches (project, run_id, id);
