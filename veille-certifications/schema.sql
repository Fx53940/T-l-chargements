-- Base de référence des certifications RNCP / RS (v2, conforme au schéma validé le 16/10/2026)
-- Source : export quotidien France compétences (data.gouv.fr). Les contacts, relances et commentaires vivent dans Pipedrive.
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- Une ligne par fiche RNCP ou RS ----------------------------------------------------------
CREATE TABLE IF NOT EXISTS certification (
  repertoire              TEXT NOT NULL CHECK (repertoire IN ('RNCP','RS')),
  numero_fiche            TEXT NOT NULL,
  intitule                TEXT,
  type_diplome_code       TEXT,                     -- ABREGE (CAP, BTS…), RNCP
  type_diplome_libelle    TEXT,
  etat_fiche              TEXT,
  actif                   INTEGER NOT NULL,         -- 1 = active
  type_enregistrement     TEXT,                     -- « Enregistrement sur demande » / « de droit »
  niveau                  TEXT,                     -- NIV3 à NIV8, RNCP uniquement
  nsf_code                TEXT,                     -- plusieurs valeurs séparées par « | »
  nsf_libelle             TEXT,
  date_decision           TEXT,                     -- AAAA-MM-JJ
  duree_ans               INTEGER,
  date_fin_enregistrement TEXT,                     -- AAAA-MM-JJ = échéance de renouvellement
  date_limite_delivrance  TEXT,
  modalites_renouvellement TEXT,                    -- RS
  objectifs_contexte      TEXT,
  lien_url                TEXT,
  nb_certificateurs       INTEGER NOT NULL DEFAULT 0,
  nb_partenaires          INTEGER NOT NULL DEFAULT 0,
  nb_certifies            INTEGER,                  -- dernière année de statistique disponible
  annee_stat              INTEGER,
  taux_insertion_6mois    INTEGER,
  taux_insertion_2ans     INTEGER,
  empreinte               TEXT NOT NULL,            -- hash des champs stockés : détection des changements
  fiche_de_reference      INTEGER NOT NULL DEFAULT 0, -- 1 = vue dès le premier import : pas de vraie date d'apparition
  premiere_vue            TEXT NOT NULL,            -- date d'import de la première observation
  derniere_vue            TEXT NOT NULL,
  disparue                INTEGER NOT NULL DEFAULT 0,
  import_id               TEXT NOT NULL,            -- date de l'export d'où vient la dernière version
  PRIMARY KEY (repertoire, numero_fiche)
);
CREATE INDEX IF NOT EXISTS ix_cert_echeance ON certification(actif, date_fin_enregistrement);

-- Historique des valeurs suivies : l'ancienne valeur est conservée avec sa période de validité
CREATE TABLE IF NOT EXISTS certification_historique (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  repertoire     TEXT NOT NULL,
  numero_fiche   TEXT NOT NULL,
  champ          TEXT NOT NULL,
  ancienne_valeur TEXT,
  nouvelle_valeur TEXT,
  valide_du      TEXT NOT NULL,                     -- début de validité de l'ancienne valeur
  valide_au      TEXT NOT NULL,                     -- date d'import où le changement a été vu
  import_id      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_hist_fiche ON certification_historique(repertoire, numero_fiche);

CREATE TABLE IF NOT EXISTS certification_formacode (
  repertoire TEXT NOT NULL, numero_fiche TEXT NOT NULL, code TEXT NOT NULL, libelle TEXT,
  PRIMARY KEY (repertoire, numero_fiche, code)
);
CREATE TABLE IF NOT EXISTS certification_rome (
  repertoire TEXT NOT NULL, numero_fiche TEXT NOT NULL, code TEXT NOT NULL, libelle TEXT,
  PRIMARY KEY (repertoire, numero_fiche, code)
);
CREATE TABLE IF NOT EXISTS certification_filiation (
  repertoire TEXT NOT NULL, numero_fiche TEXT NOT NULL, fiche_liee TEXT NOT NULL,
  sens TEXT NOT NULL CHECK (sens IN ('ancienne','nouvelle')),
  PRIMARY KEY (repertoire, numero_fiche, fiche_liee, sens)
);
CREATE TABLE IF NOT EXISTS certification_correspondance (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  repertoire TEXT NOT NULL, numero_fiche TEXT NOT NULL,
  bloc_source TEXT, fiche_destination TEXT, bloc_destination TEXT
);
CREATE INDEX IF NOT EXISTS ix_corr_fiche ON certification_correspondance(repertoire, numero_fiche);

-- Entreprises (clé = SIREN, ou « NOM:… » quand le SIRET manque) ---------------------------
CREATE TABLE IF NOT EXISTS entreprise (
  entreprise_cle            TEXT PRIMARY KEY,
  siren                     TEXT,
  nom_usuel                 TEXT NOT NULL,
  noms_connus               TEXT,                   -- JSON : variantes de nom observées
  exclue                    INTEGER NOT NULL DEFAULT 0,
  motif_exclusion           TEXT,
  a_qualifier               INTEGER NOT NULL DEFAULT 0, -- 1 = pas de SIRET
  nb_certifications_actives INTEGER NOT NULL DEFAULT 0,
  prochaine_echeance        TEXT,
  pipedrive_org_id          INTEGER,
  premiere_vue              TEXT NOT NULL,
  derniere_vue              TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS etablissement (
  etab_cle       TEXT PRIMARY KEY,                  -- SIRET, ou « NOM:… »
  siret          TEXT,
  entreprise_cle TEXT NOT NULL REFERENCES entreprise(entreprise_cle),
  nom            TEXT NOT NULL,
  est_siege      INTEGER,                           -- vide : l'export ne l'indique pas
  etat           TEXT,
  premiere_vue   TEXT NOT NULL,
  derniere_vue   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_etab_ent ON etablissement(entreprise_cle);

-- Liaison certification × établissement, historisée (valide_au NULL = ligne en vigueur)
CREATE TABLE IF NOT EXISTS certification_etablissement (
  repertoire        TEXT NOT NULL,
  numero_fiche      TEXT NOT NULL,
  etab_cle          TEXT NOT NULL REFERENCES etablissement(etab_cle),
  entreprise_cle    TEXT NOT NULL REFERENCES entreprise(entreprise_cle),
  etat_certificateur TEXT,
  role              TEXT NOT NULL CHECK (role IN ('unique','leader','co','indetermine')),
  regle_leader      TEXT,                           -- domaine | portefeuille
  rang_portefeuille INTEGER,                        -- 1 = entreprise au plus gros portefeuille actif de la fiche
  valide_du         TEXT NOT NULL,
  valide_au         TEXT,
  import_id         TEXT NOT NULL,
  PRIMARY KEY (repertoire, numero_fiche, etab_cle, valide_du)
);
CREATE INDEX IF NOT EXISTS ix_ce_ent ON certification_etablissement(entreprise_cle, valide_au);
CREATE INDEX IF NOT EXISTS ix_ce_fiche ON certification_etablissement(repertoire, numero_fiche, valide_au);

-- Site web de l'entreprise, avec provenance, historisé
CREATE TABLE IF NOT EXISTS entreprise_site_web (
  entreprise_cle    TEXT NOT NULL REFERENCES entreprise(entreprise_cle),
  url_racine        TEXT NOT NULL,
  source            TEXT NOT NULL CHECK (source IN ('lien_fiche_unique','recherche','manuel')),
  statut            TEXT NOT NULL CHECK (statut IN ('a_valider','valide','invalide')),
  date_observation  TEXT NOT NULL,
  date_verification TEXT,
  valide_du         TEXT NOT NULL,
  valide_au         TEXT,
  PRIMARY KEY (entreprise_cle, valide_du)
);

-- Journaux ----------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS evenement (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  date_import    TEXT NOT NULL,
  repertoire     TEXT NOT NULL,
  numero_fiche   TEXT NOT NULL,
  type           TEXT NOT NULL CHECK (type IN ('nouvelle','activee','desactivee','echeance_modifiee','modifiee','disparue','retour')),
  ancienne_valeur TEXT,
  nouvelle_valeur TEXT,
  detail         TEXT
);
CREATE INDEX IF NOT EXISTS ix_evt_date ON evenement(date_import);

CREATE TABLE IF NOT EXISTS import_log (
  date_import  TEXT PRIMARY KEY,
  source_url   TEXT,
  sha256       TEXT,
  nb_fiches    INTEGER,
  nb_nouvelles INTEGER,
  nb_modifiees INTEGER,
  nb_disparues INTEGER,
  reference    INTEGER NOT NULL DEFAULT 0           -- 1 = premier import (base de référence, aucun événement créé)
);

CREATE TABLE IF NOT EXISTS file_qualification (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  type       TEXT NOT NULL CHECK (type IN ('sans_siret','site_ambigu','doublon_probable')),
  objet_ref  TEXT NOT NULL,
  detail     TEXT,
  cree_le    TEXT NOT NULL,
  resolu_le  TEXT,
  resolu_par TEXT,
  resolution TEXT,
  UNIQUE (type, objet_ref)
);

CREATE TABLE IF NOT EXISTS exclusion_regle (
  id       INTEGER PRIMARY KEY AUTOINCREMENT,
  type     TEXT NOT NULL CHECK (type IN ('siren','siret','motif_nom')),
  valeur   TEXT NOT NULL,
  motif    TEXT,
  statut   TEXT NOT NULL DEFAULT 'a_valider' CHECK (statut IN ('a_valider','valide')),
  cree_le  TEXT NOT NULL,
  UNIQUE (type, valeur)
);

-- Réservé à la phase Pipedrive (non alimenté par l'import)
CREATE TABLE IF NOT EXISTS pipedrive_lien (
  id               INTEGER PRIMARY KEY AUTOINCREMENT,
  entreprise_cle   TEXT NOT NULL REFERENCES entreprise(entreprise_cle),
  pipedrive_org_id INTEGER,
  repertoire       TEXT,
  numero_fiche     TEXT,
  pipedrive_deal_id INTEGER,
  cree_le          TEXT NOT NULL,
  derniere_sync    TEXT,
  statut_sync      TEXT
);

CREATE TABLE IF NOT EXISTS audit_log (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  ts             TEXT NOT NULL,
  auteur         TEXT NOT NULL CHECK (auteur IN ('script','ia','personne')),
  operation      TEXT NOT NULL,
  objet_ref      TEXT,
  ancienne_valeur TEXT,
  nouvelle_valeur TEXT,
  ok             INTEGER NOT NULL DEFAULT 1
);

-- Vue de travail : une ligne par certification et par entreprise, hors entreprises exclues --
DROP VIEW IF EXISTS v_opportunites;
CREATE VIEW v_opportunites AS
SELECT c.repertoire, c.numero_fiche, c.intitule, c.type_enregistrement, c.niveau,
       c.date_fin_enregistrement AS echeance,
       CAST(ROUND((julianday(c.date_fin_enregistrement) - julianday('now')) / 30.44, 1) AS REAL) AS delai_mois,
       CASE WHEN julianday(c.date_fin_enregistrement) - julianday('now') < 365.25 THEN 'moins_12_mois'
            WHEN julianday(c.date_fin_enregistrement) - julianday('now') < 730.5 THEN '12_24_mois'
            ELSE '24_mois_et_plus' END AS tranche,
       e.entreprise_cle, e.siren, e.nom_usuel AS entreprise,
       CASE WHEN SUM(ce.role = 'leader') > 0 THEN 'leader'
            WHEN SUM(ce.role = 'unique') > 0 THEN 'unique'
            WHEN SUM(ce.role = 'co') > 0 THEN 'co'
            ELSE 'indetermine' END AS role,
       c.nb_certificateurs, c.lien_url
FROM certification c
JOIN certification_etablissement ce
  ON ce.repertoire = c.repertoire AND ce.numero_fiche = c.numero_fiche AND ce.valide_au IS NULL
JOIN entreprise e ON e.entreprise_cle = ce.entreprise_cle
WHERE c.actif = 1 AND c.disparue = 0 AND c.date_fin_enregistrement IS NOT NULL AND e.exclue = 0
GROUP BY c.repertoire, c.numero_fiche, e.entreprise_cle;
