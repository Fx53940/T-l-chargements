const express = require('express');
const crypto = require('node:crypto');
const db = require('../db');

const router = express.Router();

const ALLOWED_CATEGORIES = new Set(['connector', 'infra', 'other']);
const ALLOWED_STATUS = new Set(['ok', 'fail', 'unknown']);
const ALLOWED_RUN_STATUS = new Set(['success', 'failure']);

function nowIso() {
  return new Date().toISOString();
}

function isNonEmptyString(v) {
  return typeof v === 'string' && v.trim().length > 0;
}

// POST /api/ingest/service-status
// Body: { id, name, category?, status: 'ok'|'fail', detail?, occurred_at? }
// Upsert de l'état courant + journalisation d'un événement si le statut change.
router.post('/service-status', (req, res) => {
  const { id, name, category = 'connector', status, detail = null } = req.body || {};
  const occurredAt = req.body?.occurred_at || nowIso();

  if (!isNonEmptyString(id) || !isNonEmptyString(name) || !ALLOWED_STATUS.has(status)) {
    return res.status(400).json({ error: 'Champs requis: id, name, status (ok|fail)' });
  }
  if (!ALLOWED_CATEGORIES.has(category)) {
    return res.status(400).json({ error: `category doit être l'une de: ${[...ALLOWED_CATEGORIES].join(', ')}` });
  }

  const existing = db.prepare('SELECT * FROM services WHERE id = ?').get(id);
  // Pas d'événement "RÉTABLI" fantôme au tout premier contact d'un service ok,
  // mais un premier contact en échec reste un incident à journaliser.
  const statusChanged = existing ? existing.status !== status : status === 'fail';

  db.prepare(`
    INSERT INTO services (id, name, category, status, last_status_change, last_seen)
    VALUES (@id, @name, @category, @status, @changeTs, @seenTs)
    ON CONFLICT(id) DO UPDATE SET
      name = excluded.name,
      category = excluded.category,
      status = excluded.status,
      last_status_change = CASE WHEN services.status != excluded.status THEN excluded.last_status_change ELSE services.last_status_change END,
      last_seen = excluded.last_seen
  `).run({
    id, name, category, status,
    changeTs: occurredAt,
    seenTs: nowIso(),
  });

  if (statusChanged) {
    db.prepare(`
      INSERT INTO service_events (service_id, event_type, detail, occurred_at)
      VALUES (?, ?, ?, ?)
    `).run(id, status === 'ok' ? 'recovered' : 'fail', detail, occurredAt);
  }

  res.status(201).json({ ok: true, statusChanged });
});

// POST /api/ingest/qnap-metrics
// Body: { cpu_pct?, ram_pct?, disk_usage_pct?, temp_c?, raid_status?, backup_status?, detail?, recorded_at? }
router.post('/qnap-metrics', (req, res) => {
  const {
    cpu_pct = null, ram_pct = null, disk_usage_pct = null, temp_c = null,
    raid_status = null, backup_status = null, detail = null,
  } = req.body || {};
  const recordedAt = req.body?.recorded_at || nowIso();

  db.prepare(`
    INSERT INTO qnap_metrics (recorded_at, cpu_pct, ram_pct, disk_usage_pct, temp_c, raid_status, backup_status, detail)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
  `).run(recordedAt, cpu_pct, ram_pct, disk_usage_pct, temp_c, raid_status, backup_status, detail);

  res.status(201).json({ ok: true });
});

// POST /api/ingest/vpn-status
// Body: { id, name, status: 'ok'|'fail', detail?, occurred_at? }
router.post('/vpn-status', (req, res) => {
  const { id, name, status, detail = null } = req.body || {};
  const occurredAt = req.body?.occurred_at || nowIso();

  if (!isNonEmptyString(id) || !isNonEmptyString(name) || !ALLOWED_STATUS.has(status)) {
    return res.status(400).json({ error: 'Champs requis: id, name, status (ok|fail)' });
  }

  const existing = db.prepare('SELECT * FROM vpn_nodes WHERE id = ?').get(id);
  const statusChanged = existing ? existing.status !== status : status === 'fail';

  db.prepare(`
    INSERT INTO vpn_nodes (id, name, status, last_status_change, last_seen)
    VALUES (@id, @name, @status, @changeTs, @seenTs)
    ON CONFLICT(id) DO UPDATE SET
      name = excluded.name,
      status = excluded.status,
      last_status_change = CASE WHEN vpn_nodes.status != excluded.status THEN excluded.last_status_change ELSE vpn_nodes.last_status_change END,
      last_seen = excluded.last_seen
  `).run({
    id, name, status,
    changeTs: occurredAt,
    seenTs: nowIso(),
  });

  if (statusChanged) {
    db.prepare(`
      INSERT INTO vpn_events (node_id, event_type, detail, occurred_at)
      VALUES (?, ?, ?, ?)
    `).run(id, status === 'ok' ? 'recovered' : 'fail', detail, occurredAt);
  }

  res.status(201).json({ ok: true, statusChanged });
});

// POST /api/ingest/token-usage
// Body: { source, model, tokens_input?, tokens_output?, cost_usd?, recorded_at? }
router.post('/token-usage', (req, res) => {
  const {
    source, model, tokens_input = 0, tokens_output = 0, cost_usd = 0,
  } = req.body || {};
  const recordedAt = req.body?.recorded_at || nowIso();

  if (!isNonEmptyString(source) || !isNonEmptyString(model)) {
    return res.status(400).json({ error: 'Champs requis: source, model' });
  }

  db.prepare(`
    INSERT INTO token_usage (recorded_at, source, model, tokens_input, tokens_output, cost_usd)
    VALUES (?, ?, ?, ?, ?, ?)
  `).run(recordedAt, source, model, tokens_input, tokens_output, cost_usd);

  res.status(201).json({ ok: true });
});

// --- Tâches planifiées (agents) ---

function upsertJob(id, name, opts = {}) {
  const expectedIntervalHours = opts.expected_interval_hours ?? 24;
  const graceHours = opts.grace_hours ?? 2;
  const heartbeatGraceMinutes = opts.heartbeat_grace_minutes ?? null;

  db.prepare(`
    INSERT INTO jobs (id, name, expected_interval_hours, grace_hours, heartbeat_grace_minutes)
    VALUES (@id, @name, @expectedIntervalHours, @graceHours, @heartbeatGraceMinutes)
    ON CONFLICT(id) DO UPDATE SET
      name = excluded.name,
      expected_interval_hours = excluded.expected_interval_hours,
      grace_hours = excluded.grace_hours,
      heartbeat_grace_minutes = excluded.heartbeat_grace_minutes
  `).run({ id, name, expectedIntervalHours, graceHours, heartbeatGraceMinutes });
}

// POST /api/ingest/job-runs
// Rapport en un seul appel, pour un job court sans suivi de phases.
// Body: { job_id, job_name, status: 'success'|'failure', summary?, detail?,
//         started_at?, finished_at?, expected_interval_hours?, grace_hours? }
router.post('/job-runs', (req, res) => {
  const {
    job_id, job_name, status, summary = null, detail = null,
    expected_interval_hours, grace_hours,
  } = req.body || {};

  if (!isNonEmptyString(job_id) || !isNonEmptyString(job_name) || !ALLOWED_RUN_STATUS.has(status)) {
    return res.status(400).json({ error: 'Champs requis: job_id, job_name, status (success|failure)' });
  }

  upsertJob(job_id, job_name, { expected_interval_hours, grace_hours });

  const finishedAt = req.body?.finished_at || nowIso();
  const startedAt = req.body?.started_at || finishedAt;

  db.prepare(`
    INSERT INTO job_runs (job_id, status, summary, detail, started_at, finished_at)
    VALUES (?, ?, ?, ?, ?, ?)
  `).run(job_id, status, summary, detail, startedAt, finishedAt);

  res.status(201).json({ ok: true });
});

// POST /api/ingest/job-runs/:jobId/start
// Démarre le suivi d'un job long à phases (ex: relance commerciale).
// Body: { job_name, run_key?, started_at?, expected_interval_hours?, grace_hours?, heartbeat_grace_minutes? }
router.post('/job-runs/:jobId/start', (req, res) => {
  const jobId = req.params.jobId;
  const {
    job_name, expected_interval_hours, grace_hours, heartbeat_grace_minutes,
  } = req.body || {};

  if (!isNonEmptyString(job_name)) {
    return res.status(400).json({ error: 'Champ requis: job_name' });
  }

  upsertJob(jobId, job_name, { expected_interval_hours, grace_hours, heartbeat_grace_minutes });

  const runKey = req.body?.run_key || crypto.randomUUID();
  const startedAt = req.body?.started_at || nowIso();

  db.prepare(`
    INSERT INTO job_runs (job_id, run_key, status, started_at, last_heartbeat_at)
    VALUES (?, ?, 'running', ?, ?)
  `).run(jobId, runKey, startedAt, startedAt);

  res.status(201).json({ ok: true, run_key: runKey });
});

// POST /api/ingest/job-runs/:jobId/heartbeat
// Signal de vie pendant l'exécution d'un job long, entre le début et la fin.
// Body: { run_key, detail? }
router.post('/job-runs/:jobId/heartbeat', (req, res) => {
  const jobId = req.params.jobId;
  const { run_key, detail } = req.body || {};

  if (!isNonEmptyString(run_key)) {
    return res.status(400).json({ error: 'Champ requis: run_key' });
  }

  const result = db.prepare(`
    UPDATE job_runs SET last_heartbeat_at = ?, detail = COALESCE(?, detail)
    WHERE job_id = ? AND run_key = ? AND status = 'running'
  `).run(nowIso(), detail ?? null, jobId, run_key);

  if (result.changes === 0) {
    return res.status(404).json({ error: "Aucune exécution en cours pour ce job_id/run_key. Appeler /start d'abord." });
  }
  res.json({ ok: true });
});

// POST /api/ingest/job-runs/:jobId/finish
// Body: { run_key, status: 'success'|'failure', summary?, detail?, finished_at? }
router.post('/job-runs/:jobId/finish', (req, res) => {
  const jobId = req.params.jobId;
  const { run_key, status, summary = null, detail = null } = req.body || {};

  if (!isNonEmptyString(run_key) || !ALLOWED_RUN_STATUS.has(status)) {
    return res.status(400).json({ error: 'Champs requis: run_key, status (success|failure)' });
  }

  const finishedAt = req.body?.finished_at || nowIso();

  const result = db.prepare(`
    UPDATE job_runs SET status = ?, summary = ?, detail = COALESCE(?, detail), finished_at = ?
    WHERE job_id = ? AND run_key = ? AND status = 'running'
  `).run(status, summary, detail, finishedAt, jobId, run_key);

  if (result.changes === 0) {
    // Pas de /start correspondant retrouvé (script interrompu avant, etc.) :
    // on journalise quand même le résultat plutôt que de le perdre.
    db.prepare(`
      INSERT INTO job_runs (job_id, run_key, status, summary, detail, started_at, finished_at)
      VALUES (?, ?, ?, ?, ?, ?, ?)
    `).run(jobId, run_key, status, summary, detail, finishedAt, finishedAt);
  }

  res.json({ ok: true });
});

module.exports = router;
