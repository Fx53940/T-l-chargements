# Portail d'exploitation FTA

Tableau de bord unique pour l'exploitation French Touch Attitude : état des
connecteurs MCP, VPN Tailscale, serveur QNAP, historique des incidents et
coût des tokens LLM. Remplace le mail récapitulatif quotidien par une
consultation à la demande, avec historique.

Auto-hébergé, pensé pour tourner sur le QNAP derrière le réseau Tailscale
existant — aucune dépendance à un service cloud tiers, aucun CDN requis
pour afficher le dashboard.

## Architecture

- **Backend** : Node.js + Express, base SQLite locale (`better-sqlite3`),
  aucun serveur de base de données externe à gérer.
- **Frontend** : une page statique (HTML/CSS/JS vanilla), servie par le
  même serveur Express. Graphiques SVG faits maison, pas de CDN.
- **Modèle** : ce site ne *collecte* rien lui-même — il **reçoit** des
  données via une API d'ingestion. Ce sont vos scripts de supervision
  existants (ceux qui produisent le mail actuel) qui doivent les pousser.

```
scripts de supervision (QNAP/serveurs)  --POST-->  API /api/ingest/*  -->  SQLite  -->  dashboard web
```

## Démarrage rapide (Docker, sur le QNAP)

1. Copier `.env.example` vers `.env` et renseigner :
   - `INGEST_API_KEY` : clé secrète longue (`openssl rand -hex 32`), à
     donner uniquement aux scripts de supervision.
   - `DASHBOARD_USER` / `DASHBOARD_PASS` : optionnel, protège la
     consultation du site (recommandé même derrière Tailscale).
2. Sur le QNAP (Container Station), ou via SSH avec Docker Compose installé :

   ```bash
   docker compose up -d --build
   ```

3. Le site est disponible sur `http://<ip-qnap>:3000` (à consulter via
   Tailscale si le port n'est pas exposé publiquement).

Les données persistent dans le volume Docker `fta-ops-data` (fichier
SQLite). Aucune donnée n'est perdue lors d'un redémarrage du conteneur.

## Sans Docker

```bash
npm install
cp .env.example .env   # puis éditer .env
npm start
```

## Référence API d'ingestion

Toutes les routes `/api/ingest/*` nécessitent l'en-tête :
`Authorization: Bearer <INGEST_API_KEY>`

### État d'un connecteur ou d'un service

```bash
curl -X POST http://<host>:3000/api/ingest/service-status \
  -H "Authorization: Bearer $INGEST_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "id": "digiforma",
    "name": "digiforma",
    "category": "connector",
    "status": "fail",
    "detail": "HTTP 000 — redémarrage du sidecar fta-digiforma-tailscale"
  }'
```

`category` : `connector` (défaut) | `infra` | `other`.
`status` : `ok` | `fail`. Un événement (échec/rétabli) n'est journalisé
que lorsque le statut change réellement — pas besoin de dédupliquer côté
script, envoyez l'état à chaque vérification.

### Métriques QNAP

```bash
curl -X POST http://<host>:3000/api/ingest/qnap-metrics \
  -H "Authorization: Bearer $INGEST_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "cpu_pct": 23.5,
    "ram_pct": 61.2,
    "disk_usage_pct": 74.0,
    "temp_c": 42,
    "raid_status": "sain",
    "backup_status": "OK — 2026-09-21 03:00"
  }'
```

### État d'un nœud VPN Tailscale

```bash
curl -X POST http://<host>:3000/api/ingest/vpn-status \
  -H "Authorization: Bearer $INGEST_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "id": "fta-brevo-tailscale",
    "name": "fta-brevo-mcp.tailfa9c06.ts.net",
    "status": "fail",
    "detail": "Échec DNS — cause externe probable"
  }'
```

### Coût des tokens LLM

```bash
curl -X POST http://<host>:3000/api/ingest/token-usage \
  -H "Authorization: Bearer $INGEST_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "source": "claude-code-session-infra",
    "model": "claude-sonnet-5",
    "tokens_input": 12000,
    "tokens_output": 3400,
    "cost_usd": 0.87
  }'
```

## Référence API — tâches planifiées (agents)

Deux façons de déclarer une exécution, selon la forme du job.

### Job court : un seul appel en fin d'exécution

Adapté à un agent qui fait un travail ponctuel et rapporte le résultat une
fois (ex. rapprochement bancaire/factures, une fois par jour).

```bash
curl -X POST http://<host>:3000/api/ingest/job-runs \
  -H "Authorization: Bearer $INGEST_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "job_id": "rapprochement-factures",
    "job_name": "Rapprochement transactions / factures",
    "status": "success",
    "summary": "312 transactions rapprochées, 4 écarts détectés",
    "expected_interval_hours": 24
  }'
```

`status` : `success` | `failure`. `expected_interval_hours` (défaut 24) ne
doit être envoyé qu'une fois (ou à chaque appel, sans risque : c'est un
upsert) — c'est ce qui permet au dashboard de savoir qu'une absence de
rapport pendant plus de `expected_interval_hours + grace_hours` (2h par
défaut) doit être signalée comme **EN RETARD**, même si l'agent ne se
manifeste plus du tout.

### Job long à phases : démarrage / battement / fin

Adapté à un agent qui s'étale sur la journée (ex. relance commerciale par
phases), où il faut aussi détecter un blocage en cours de route, pas
seulement l'absence de rapport final.

```bash
# 1. Au démarrage
RUN_KEY=$(curl -fsS -X POST "http://<host>:3000/api/ingest/job-runs/relance-commerciale/start" \
  -H "Authorization: Bearer $INGEST_API_KEY" -H "Content-Type: application/json" \
  -d '{
    "job_name": "Relance commerciale prospects",
    "expected_interval_hours": 24,
    "heartbeat_grace_minutes": 180
  }' | python3 -c "import sys,json;print(json.load(sys.stdin)['run_key'])")

# 2. À chaque phase, un signal de vie (optionnel mais recommandé)
curl -X POST "http://<host>:3000/api/ingest/job-runs/relance-commerciale/heartbeat" \
  -H "Authorization: Bearer $INGEST_API_KEY" -H "Content-Type: application/json" \
  -d "{\"run_key\":\"$RUN_KEY\",\"detail\":\"Phase 2/4 — relances email envoyées\"}"

# 3. À la fin
curl -X POST "http://<host>:3000/api/ingest/job-runs/relance-commerciale/finish" \
  -H "Authorization: Bearer $INGEST_API_KEY" -H "Content-Type: application/json" \
  -d "{\"run_key\":\"$RUN_KEY\",\"status\":\"success\",\"summary\":\"28 prospects relancés sur 4 phases\"}"
```

`heartbeat_grace_minutes` : si aucun battement n'arrive pendant plus de
cette durée alors qu'une exécution est en cours, le job passe à
**BLOQUÉ** — utile pour distinguer "il n'a pas encore fini aujourd'hui"
(normal pour un job à phases) de "il s'est arrêté en silence au milieu".
Ne pas renseigner ce champ pour un job court : sans lui, un job "en cours"
reste simplement affiché comme tel, sans alerte de blocage.

États affichés sur le dashboard : **OK**, **EN RETARD** (pas de succès
récent), **BLOQUÉ** (en cours mais plus de battement), **ÉCHEC**, **EN
COURS**, **JAMAIS EXÉCUTÉ**.

## Intégration avec les scripts de supervision existants

> Section destinée à la session Claude qui a la main sur l'infrastructure
> (QNAP, sidecars Tailscale, scripts de healthcheck actuels).

Le script qui génère le mail quotidien vérifie déjà chaque connecteur en
boucle et sait détecter les échecs/rétablissements. Il suffit d'ajouter un
appel HTTP à chaque vérification, en plus (pas à la place, dans un premier
temps) de la logique d'email existante :

```bash
# À placer en haut du script de supervision
FTA_DASHBOARD_URL="http://<ip-qnap>:3000"
FTA_INGEST_KEY="<valeur de INGEST_API_KEY>"

report_connector_status() {
  local id="$1" status="$2" detail="$3"
  curl -fsS -X POST "$FTA_DASHBOARD_URL/api/ingest/service-status" \
    -H "Authorization: Bearer $FTA_INGEST_KEY" \
    -H "Content-Type: application/json" \
    -d "$(printf '{"id":"%s","name":"%s","category":"connector","status":"%s","detail":"%s"}' \
      "$id" "$id" "$status" "$detail")" \
    >/dev/null 2>&1 || echo "WARN: échec de remontée vers le dashboard pour $id"
}

# Exemple d'appel après chaque test de connecteur :
# report_connector_status "digiforma" "fail" "HTTP 000 — redémarrage du sidecar"
# report_connector_status "digiforma" "ok" ""
```

Le même principe s'applique aux sidecars Tailscale (`report_vpn_status`,
même forme d'appel vers `/api/ingest/vpn-status`) et à un script cron
séparé pour les métriques QNAP (`/api/ingest/qnap-metrics`, par exemple
toutes les 5 à 15 minutes).

Une fois ces appels en place et vérifiés (les connecteurs apparaissent
sur le dashboard), le mail quotidien peut être réduit à une alerte de
secours uniquement en cas d'indisponibilité prolongée, ou supprimé.

### Suivi des coûts tokens

Si vos sessions Claude (Claude Code, agents, etc.) journalisent déjà leur
consommation (usage API), un petit script cron peut agréger ces logs et
pousser un résumé quotidien ou horaire vers `/api/ingest/token-usage` —
un enregistrement par (source, modèle, période) suffit, pas besoin de
remonter chaque appel individuel.

## Sécurité

- L'API d'ingestion est protégée par clé secrète (`INGEST_API_KEY`).
- Le dashboard peut être protégé par identifiants (`DASHBOARD_USER` /
  `DASHBOARD_PASS`) — recommandé même si l'accès réseau est déjà limité
  par Tailscale, en défense en profondeur.
- Aucune donnée ne quitte votre infrastructure : pas d'appel à un service
  tiers, pas de CDN chargé au runtime.

## Roadmap possible

- Alerte proactive (Slack/email) uniquement si un incident dépasse un
  seuil de durée, au lieu d'un rapport quotidien systématique.
- Export CSV de l'historique pour analyse.
- Authentification plus robuste (SSO) si le site est ouvert à plusieurs
  personnes.
