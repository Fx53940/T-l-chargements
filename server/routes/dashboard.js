const express = require('express');
const db = require('../db');

const router = express.Router();

const RANGE_TO_MS = {
  '24h': 24 * 60 * 60 * 1000,
  '7d': 7 * 24 * 60 * 60 * 1000,
  '30d': 30 * 24 * 60 * 60 * 1000,
  '90d': 90 * 24 * 60 * 60 * 1000,
};

function sinceIso(range) {
  const ms = RANGE_TO_MS[range] || RANGE_TO_MS['24h'];
  return new Date(Date.now() - ms).toISOString();
}

// --- Connecteurs & services ---

router.get('/services', (req, res) => {
  const services = db.prepare('SELECT * FROM services ORDER BY category, name').all();
  res.json(services);
});

router.get('/services/:id/events', (req, res) => {
  const limit = Math.min(Number(req.query.limit) || 50, 500);
  const events = db.prepare(`
    SELECT * FROM service_events WHERE service_id = ?
    ORDER BY occurred_at DESC LIMIT ?
  `).all(req.params.id, limit);
  res.json(events);
});

router.get('/events', (req, res) => {
  const since = sinceIso(req.query.range);
  const events = db.prepare(`
    SELECT se.*, s.name AS service_name, s.category AS service_category
    FROM service_events se
    JOIN services s ON s.id = se.service_id
    WHERE se.occurred_at >= ?
    ORDER BY se.occurred_at DESC
    LIMIT 200
  `).all(since);
  res.json(events);
});

// --- QNAP ---

router.get('/qnap', (req, res) => {
  const since = sinceIso(req.query.range);
  const rows = db.prepare(`
    SELECT * FROM qnap_metrics WHERE recorded_at >= ? ORDER BY recorded_at ASC
  `).all(since);
  res.json(rows);
});

router.get('/qnap/latest', (req, res) => {
  const row = db.prepare('SELECT * FROM qnap_metrics ORDER BY recorded_at DESC LIMIT 1').get();
  res.json(row || null);
});

// --- VPN ---

router.get('/vpn', (req, res) => {
  const nodes = db.prepare('SELECT * FROM vpn_nodes ORDER BY name').all();
  res.json(nodes);
});

router.get('/vpn/events', (req, res) => {
  const since = sinceIso(req.query.range);
  const events = db.prepare(`
    SELECT ve.*, vn.name AS node_name
    FROM vpn_events ve
    JOIN vpn_nodes vn ON vn.id = ve.node_id
    WHERE ve.occurred_at >= ?
    ORDER BY ve.occurred_at DESC
    LIMIT 200
  `).all(since);
  res.json(events);
});

// --- Coûts tokens ---

router.get('/tokens/summary', (req, res) => {
  const since = sinceIso(req.query.range);
  const totals = db.prepare(`
    SELECT
      COALESCE(SUM(tokens_input), 0) AS tokens_input,
      COALESCE(SUM(tokens_output), 0) AS tokens_output,
      COALESCE(SUM(cost_usd), 0) AS cost_usd
    FROM token_usage WHERE recorded_at >= ?
  `).get(since);

  const byModel = db.prepare(`
    SELECT model,
      COALESCE(SUM(tokens_input), 0) AS tokens_input,
      COALESCE(SUM(tokens_output), 0) AS tokens_output,
      COALESCE(SUM(cost_usd), 0) AS cost_usd
    FROM token_usage WHERE recorded_at >= ?
    GROUP BY model ORDER BY cost_usd DESC
  `).all(since);

  const bySource = db.prepare(`
    SELECT source,
      COALESCE(SUM(tokens_input), 0) AS tokens_input,
      COALESCE(SUM(tokens_output), 0) AS tokens_output,
      COALESCE(SUM(cost_usd), 0) AS cost_usd
    FROM token_usage WHERE recorded_at >= ?
    GROUP BY source ORDER BY cost_usd DESC
  `).all(since);

  const daily = db.prepare(`
    SELECT substr(recorded_at, 1, 10) AS day,
      COALESCE(SUM(tokens_input), 0) AS tokens_input,
      COALESCE(SUM(tokens_output), 0) AS tokens_output,
      COALESCE(SUM(cost_usd), 0) AS cost_usd
    FROM token_usage WHERE recorded_at >= ?
    GROUP BY day ORDER BY day ASC
  `).all(since);

  res.json({ totals, byModel, bySource, daily });
});

module.exports = router;
