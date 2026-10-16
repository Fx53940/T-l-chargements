-- Table de référence des certifications RNCP / RS (source : export quotidien France compétences, data.gouv.fr)
PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS certification (
  repertoire              TEXT NOT NULL CHECK (repertoire IN ('RNCP','RS')),
  numero_fiche            TEXT NOT NULL,            -- ex. RNCP41998, RS6012
  intitule                TEXT,
  etat_fiche              TEXT,
  actif                   INTEGER NOT NULL,         -- 1 = active, 0 = inactive
  type_enregistrement     TEXT,                     -- « Enregistrement sur demande » / « de droit »
  date_decision           TEXT,                     -- AAAA-MM-JJ
  duree_ans               INTEGER,
  date_fin_enregistrement TEXT,                     -- AAAA-MM-JJ = échéance de renouvellement
  date_limite_delivrance  TEXT,
  lien_url                TEXT,
  empreinte               TEXT NOT NULL,            -- hash des champs suivis (détection des changements)
  premiere_vue            TEXT NOT NULL,            -- date d'import où la fiche est apparue
  derniere_vue            TEXT NOT NULL,
  disparue                INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (repertoire, numero_fiche)
);

CREATE TABLE IF NOT EXISTS certificateur (
  cle   TEXT PRIMARY KEY,                           -- SIRET, ou « NOM:<nom> » si SIRET absent
  siret TEXT,
  nom   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS certification_certificateur (
  repertoire   TEXT NOT NULL,
  numero_fiche TEXT NOT NULL,
  cle          TEXT NOT NULL REFERENCES certificateur(cle),
  etat         TEXT,
  PRIMARY KEY (repertoire, numero_fiche, cle)
);
CREATE INDEX IF NOT EXISTS ix_cc_cle ON certification_certificateur(cle);

CREATE TABLE IF NOT EXISTS evenement (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  date_import  TEXT NOT NULL,
  repertoire   TEXT NOT NULL,
  numero_fiche TEXT NOT NULL,
  type         TEXT NOT NULL CHECK (type IN ('nouvelle','activee','desactivee','echeance_modifiee','modifiee','disparue','retour')),
  detail       TEXT
);
CREATE INDEX IF NOT EXISTS ix_evt_date ON evenement(date_import);

CREATE TABLE IF NOT EXISTS import_log (
  date_import TEXT PRIMARY KEY,
  source      TEXT,
  sha256      TEXT,
  nb_fiches   INTEGER,
  nb_nouvelles INTEGER,
  nb_modifiees INTEGER,
  nb_disparues INTEGER,
  reference   INTEGER NOT NULL DEFAULT 0            -- 1 = premier import (base de référence, aucun événement créé)
);

-- Réservé à la phase relance (non alimenté par le script d'import)
CREATE TABLE IF NOT EXISTS suivi_certificateur (
  cle          TEXT PRIMARY KEY REFERENCES certificateur(cle),
  statut       TEXT NOT NULL DEFAULT 'a_contacter',
  dernier_contact TEXT,
  prochaine_action TEXT,
  note         TEXT
);

DROP VIEW IF EXISTS v_echeances;
CREATE VIEW v_echeances AS
SELECT c.repertoire, c.numero_fiche, c.intitule, c.type_enregistrement,
       c.date_decision, c.duree_ans, c.date_fin_enregistrement AS echeance,
       CAST(ROUND((julianday(c.date_fin_enregistrement) - julianday('now')) / 30.44, 1) AS REAL) AS delai_mois,
       k.cle AS certificateur_cle, k.siret, k.nom AS certificateur, c.lien_url
FROM certification c
JOIN certification_certificateur cc ON cc.repertoire = c.repertoire AND cc.numero_fiche = c.numero_fiche
JOIN certificateur k ON k.cle = cc.cle
WHERE c.actif = 1 AND c.disparue = 0 AND c.date_fin_enregistrement IS NOT NULL;
