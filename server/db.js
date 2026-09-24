const path = require('node:path');
const fs = require('node:fs');
const Database = require('better-sqlite3');

const DATA_DIR = process.env.DATA_DIR || path.join(__dirname, '..', 'data');
fs.mkdirSync(DATA_DIR, { recursive: true });

const db = new Database(path.join(DATA_DIR, 'fta-ops.db'));
db.pragma('journal_mode = WAL');

db.exec(`
  CREATE TABLE IF NOT EXISTS services (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT 'connector',
    status TEXT NOT NULL DEFAULT 'unknown',
    last_status_change TEXT,
    last_seen TEXT
  );

  CREATE TABLE IF NOT EXISTS service_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    service_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    detail TEXT,
    occurred_at TEXT NOT NULL
  );
  CREATE INDEX IF NOT EXISTS idx_service_events_service ON service_events(service_id, occurred_at);

  CREATE TABLE IF NOT EXISTS qnap_metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    recorded_at TEXT NOT NULL,
    cpu_pct REAL,
    ram_pct REAL,
    disk_usage_pct REAL,
    temp_c REAL,
    raid_status TEXT,
    backup_status TEXT,
    detail TEXT
  );
  CREATE INDEX IF NOT EXISTS idx_qnap_metrics_time ON qnap_metrics(recorded_at);

  CREATE TABLE IF NOT EXISTS vpn_nodes (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'unknown',
    last_status_change TEXT,
    last_seen TEXT
  );

  CREATE TABLE IF NOT EXISTS vpn_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    node_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    detail TEXT,
    occurred_at TEXT NOT NULL
  );
  CREATE INDEX IF NOT EXISTS idx_vpn_events_node ON vpn_events(node_id, occurred_at);

  -- Tâches planifiées exécutées par des agents (ex: rapprochement factures,
  -- relance commerciale). expected_interval_hours définit la cadence attendue ;
  -- heartbeat_grace_minutes n'est renseigné que pour les jobs longs à phases,
  -- pour détecter un blocage en cours d'exécution.
  CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    expected_interval_hours REAL NOT NULL DEFAULT 24,
    grace_hours REAL NOT NULL DEFAULT 2,
    heartbeat_grace_minutes REAL
  );

  CREATE TABLE IF NOT EXISTS job_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL,
    run_key TEXT,
    status TEXT NOT NULL DEFAULT 'running',
    summary TEXT,
    detail TEXT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    last_heartbeat_at TEXT
  );
  CREATE INDEX IF NOT EXISTS idx_job_runs_job ON job_runs(job_id, started_at);
  CREATE UNIQUE INDEX IF NOT EXISTS idx_job_runs_run_key
    ON job_runs(job_id, run_key) WHERE run_key IS NOT NULL;
`);

module.exports = db;
