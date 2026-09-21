const express = require('express');
const db = require('../db');

const router = express.Router();

const ALLOWED_CATEGORIES = new Set(['connector', 'infra', 'other']);
const ALLOWED_STATUS = new Set(['ok', 'fail', 'unknown']);

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

module.exports = router;
