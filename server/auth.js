function timingSafeEqual(a, b) {
  if (typeof a !== 'string' || typeof b !== 'string' || a.length !== b.length) {
    return false;
  }
  let mismatch = 0;
  for (let i = 0; i < a.length; i++) {
    mismatch |= a.charCodeAt(i) ^ b.charCodeAt(i);
  }
  return mismatch === 0;
}

// Protège les routes d'ingestion : les scripts de supervision envoient
// `Authorization: Bearer <INGEST_API_KEY>`.
function requireIngestKey(req, res, next) {
  const expected = process.env.INGEST_API_KEY;
  if (!expected) {
    return res.status(500).json({ error: 'INGEST_API_KEY non configurée côté serveur' });
  }
  const header = req.get('authorization') || '';
  const token = header.startsWith('Bearer ') ? header.slice(7) : '';
  if (!timingSafeEqual(token, expected)) {
    return res.status(401).json({ error: 'Clé API invalide ou manquante' });
  }
  next();
}

// Protège la consultation du dashboard (basic auth), utile même derrière Tailscale.
function requireDashboardAuth(req, res, next) {
  const user = process.env.DASHBOARD_USER;
  const pass = process.env.DASHBOARD_PASS;
  if (!user || !pass) {
    return next(); // pas configuré = pas de restriction (réseau Tailscale déjà fermé)
  }
  const header = req.get('authorization') || '';
  if (header.startsWith('Basic ')) {
    const decoded = Buffer.from(header.slice(6), 'base64').toString('utf8');
    const sep = decoded.indexOf(':');
    const reqUser = decoded.slice(0, sep);
    const reqPass = decoded.slice(sep + 1);
    if (timingSafeEqual(reqUser, user) && timingSafeEqual(reqPass, pass)) {
      return next();
    }
  }
  res.set('WWW-Authenticate', 'Basic realm="FTA Ops Dashboard"');
  return res.status(401).send('Authentification requise');
}

module.exports = { requireIngestKey, requireDashboardAuth };
