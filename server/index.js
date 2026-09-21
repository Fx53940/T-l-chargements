require('dotenv').config();
const path = require('node:path');
const express = require('express');
const { requireIngestKey, requireDashboardAuth } = require('./auth');
const ingestRoutes = require('./routes/ingest');
const dashboardRoutes = require('./routes/dashboard');

const app = express();
app.use(express.json({ limit: '1mb' }));

app.get('/api/health', (req, res) => res.json({ ok: true }));

app.use('/api/ingest', requireIngestKey, ingestRoutes);
app.use('/api/dashboard', requireDashboardAuth, dashboardRoutes);

app.use(requireDashboardAuth, express.static(path.join(__dirname, '..', 'public')));

const PORT = process.env.PORT || 3000;
app.listen(PORT, () => {
  console.log(`FTA Ops Dashboard à l'écoute sur le port ${PORT}`);
});
