CREATE TABLE IF NOT EXISTS run_sessions (
  project TEXT NOT NULL,
  run_id TEXT NOT NULL,
  sid TEXT NOT NULL,
  last_seq INTEGER NOT NULL,
  PRIMARY KEY (project, run_id, sid)
);
