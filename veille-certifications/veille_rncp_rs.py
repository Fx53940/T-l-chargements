#!/usr/bin/env python3
"""Veille quotidienne RNCP / RS : import de l'export France compétences (data.gouv.fr) dans la base de
référence, détection des nouveautés, rattachement des certifications aux entreprises, export des échéances.

Python 3.9+, bibliothèque standard uniquement. Schéma : schema.sql (validé le 16/10/2026).

  python3 veille_rncp_rs.py importer   --db veille.db                  # télécharge l'export du jour
  python3 veille_rncp_rs.py importer   --db veille.db --rncp-zip a.zip --rs-zip b.zip --date 2026-10-15
  python3 veille_rncp_rs.py nouveautes --db veille.db --depuis 2026-10-01 --out nouveautes.csv
  python3 veille_rncp_rs.py echeances  --db veille.db --mois-max 24 --out echeances.csv
  python3 veille_rncp_rs.py qualification --db veille.db               # cas à trancher par une personne
  python3 veille_rncp_rs.py exclusions --db veille.db                  # règles d'exclusion et entreprises touchées
"""
import argparse, collections, csv, datetime as dt, hashlib, json, os, re, sqlite3, sys, tempfile, time, zipfile
import urllib.error
import urllib.request
from urllib.parse import urlparse
import xml.etree.ElementTree as ET

ICI = os.path.dirname(os.path.abspath(__file__))
DATASET_API = "https://www.data.gouv.fr/api/1/datasets/5eebbc067a14b6fecc9c9976/"
SCHEMA = os.path.join(ICI, "schema.sql")
EXCLUSIONS_CSV = os.path.join(ICI, "exclusions.csv")
SCHEMA_EXPORT = "v4-1"           # version du schéma XML France compétences
SUR_DEMANDE = "Enregistrement sur demande"
SEUIL_DOMAINE_DOMINANT = 0.7     # part des fiches à un seul certificateur pour retenir un domaine
PORTAILS = {"francecompetences.fr", "legifrance.gouv.fr", "onisep.fr", "linkedin.com", "facebook.com",
            "youtube.com", "google.com", "wikipedia.org", "catalogueformpro.com", "moncompteformation.gouv.fr",
            "centre-inffo.fr", "cncp.gouv.fr", "travail-emploi.gouv.fr", "service-public.fr"}
MAX_ENTREPRISES_PAR_DOMAINE = 3  # au-delà : domaine de portail ou de réseau, non attribuable à une entreprise
CHAMPS_SUIVIS = ("intitule", "etat_fiche", "actif", "type_enregistrement", "niveau",
                 "date_fin_enregistrement", "date_limite_delivrance", "duree_ans")


def log(msg):
    print(f"[{dt.datetime.now():%H:%M:%S}] {msg}", file=sys.stderr, flush=True)


def tag(el):
    return el.tag.split("}")[-1]


def iso(s):
    try:
        return dt.datetime.strptime((s or "").strip(), "%d/%m/%Y").date().isoformat()
    except ValueError:
        return None


def to_int(s):
    try:
        return int((s or "").strip())
    except ValueError:
        return None


def domaine(url):
    """Nom de domaine d'une adresse (sans www), ou None."""
    parts = (url or "").split()
    u = parts[0] if parts else ""
    if not u.startswith("http"):
        return None
    h = re.sub(r"^www\d?\.", "", urlparse(u).netloc.lower().split(":")[0])
    p = h.split(".")
    if len(p) >= 3 and p[-2] in ("asso", "gouv", "co", "com", "org", "gov") and len(p[-1]) == 2:
        return ".".join(p[-3:])
    return ".".join(p[-2:]) if len(p) >= 2 else (h or None)


def norm_nom(nom):
    return " ".join((nom or "").upper().split())


def cle_etab(siret, nom):
    s = re.sub(r"\D", "", siret or "")
    return s if len(s) == 14 else "NOM:" + norm_nom(nom)


def cle_ent(etab):
    return etab if etab.startswith("NOM:") else etab[:9]


# ---------------------------------------------------------------- téléchargement
def http_get(url, dest, retries=8, timeout=60):
    """Téléchargement avec reprise (Range) : le proxy ou le réseau peuvent couper les gros fichiers."""
    for essai in range(1, retries + 1):
        taille = os.path.getsize(dest) if os.path.exists(dest) else 0
        req = urllib.request.Request(url, headers={"User-Agent": "veille-rncp-rs/2.0"})
        if taille:
            req.add_header("Range", f"bytes={taille}-")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                if taille and r.status != 206:      # le serveur ignore Range : on repart de zéro
                    taille = 0
                attendu = int(r.headers.get("Content-Length") or -1)
                recu = 0
                with open(dest, "ab" if taille else "wb") as f:
                    while chunk := r.read(1 << 20):
                        f.write(chunk)
                        recu += len(chunk)
                if attendu >= 0 and recu < attendu:      # connexion coupée : on reprend là où on s'est arrêté
                    raise IOError(f"transfert incomplet ({recu}/{attendu} octets)")
            return
        except urllib.error.HTTPError as e:
            if e.code == 416:                        # déjà complet
                return
            err = e
        except Exception as e:                       # noqa: BLE001
            err = e
        log(f"téléchargement interrompu ({err}), essai {essai}/{retries}")
        time.sleep(min(2 ** essai, 30))
    raise RuntimeError(f"échec du téléchargement : {url}")


def trouver_exports(date=None):
    """Retourne (date, url_rncp, url_rs) du dernier export v4.1 publié sur data.gouv.fr."""
    tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
    tmp.close()
    try:
        http_get(DATASET_API, tmp.name)
        res = json.load(open(tmp.name, encoding="utf-8"))["resources"]
    finally:
        os.unlink(tmp.name)
    trouves = {}
    for r in res:
        t = r["title"]
        for rep in ("rncp", "rs"):
            pref = f"export-fiches-{rep}-{SCHEMA_EXPORT}-"
            if t.startswith(pref) and t.endswith(".zip"):
                trouves.setdefault(t[len(pref):-4], {})[rep] = r["url"]
    dates = sorted(d for d, v in trouves.items() if len(v) == 2 and (date is None or d == date))
    if not dates:
        raise RuntimeError("aucun export RNCP+RS v4.1 trouvé" + (f" pour {date}" if date else ""))
    d = dates[-1]
    return d, trouves[d]["rncp"], trouves[d]["rs"]


# ---------------------------------------------------------------- lecture XML
def lire_fiches(zip_path, repertoire):
    """Itère sur les fiches du XML contenu dans le zip (lecture en flux, mémoire constante)."""
    with zipfile.ZipFile(zip_path) as z:
        noms = [n for n in z.namelist() if n.lower().endswith(".xml")]
        if not noms:
            raise RuntimeError(f"aucun XML dans {zip_path}")
        with z.open(noms[0]) as f:
            racine = None
            for ev, el in ET.iterparse(f, events=("start", "end")):
                if ev == "start":
                    if racine is None:
                        racine = el
                    continue
                if tag(el) != "FICHE":
                    continue
                f_ = convertir(el, repertoire)
                if f_:
                    yield f_
                el.clear()
                if racine is not None:
                    racine.clear()


def convertir(el, repertoire):
    ch = {tag(c): c for c in el}

    def feuille(t):
        return (ch[t].text or "").strip() if t in ch else ""

    def sous(t, u):
        if t not in ch:
            return ""
        for c in ch[t]:
            if tag(c) == u:
                return (c.text or "").strip()
        return ""

    def liste_codes(t, item):
        out = []
        for c in ch.get(t, []):
            if tag(c) == item:
                code = next(((x.text or "").strip() for x in c if tag(x) == "CODE"), "")
                lib = next(((x.text or "").strip() for x in c if tag(x) == "LIBELLE"), "")
                if code:
                    out.append((code, lib))
        return out

    def ids(t, item, champ):
        return sorted({(x.text or "").strip() for c in ch.get(t, []) if tag(c) == item
                       for x in c if tag(x) == champ and (x.text or "").strip()})

    num = feuille("NUMERO_FICHE")
    if not num:
        return None
    nsf = liste_codes("CODES_NSF", "NSF")
    stats = []
    for s in ch.get("STATISTIQUES_PROMOTIONS", []):
        d = {tag(x): (x.text or "").strip() for x in s}
        if to_int(d.get("ANNEE")) is not None:
            stats.append(d)
    derniere = max(stats, key=lambda d: to_int(d["ANNEE"])) if stats else {}
    certs = []
    for c in ch.get("CERTIFICATEURS", []):
        x = {tag(i): (i.text or "").strip() for i in c}
        if x.get("NOM_CERTIFICATEUR") or x.get("SIRET_CERTIFICATEUR"):
            certs.append((cle_etab(x.get("SIRET_CERTIFICATEUR"), x.get("NOM_CERTIFICATEUR")),
                          re.sub(r"\D", "", x.get("SIRET_CERTIFICATEUR", "")) if len(re.sub(r"\D", "", x.get("SIRET_CERTIFICATEUR", ""))) == 14 else None,
                          " ".join(x.get("NOM_CERTIFICATEUR", "").split()), x.get("ETAT_CERTIFICATEUR", "")))
    corr = []
    for c in ch.get("CORRESPONDANCES", []):
        src = next((x for x in c if tag(x) == "SOURCE"), None)
        dst = next((x for x in c if tag(x) == "DESTINATION"), None)

        def bloc(p):
            if p is None:
                return None
            return next(((y.text or "").strip() for b in p if tag(b) == "BLOC_COMPETENCES" for y in b if tag(y) == "CODE"), None)
        fiche_dst = next(((x.text or "").strip() for x in dst if tag(x) == "NUMERO_FICHE"), None) if dst is not None else None
        corr.append((bloc(src), fiche_dst, bloc(dst)))
    lien = feuille("LIEN_URL_DESCRIPTION").split()
    return {
        "repertoire": repertoire, "numero_fiche": num,
        "intitule": feuille("INTITULE") or None, "type_diplome_code": sous("ABREGE", "CODE") or None,
        "type_diplome_libelle": sous("ABREGE", "LIBELLE") or None, "etat_fiche": feuille("ETAT_FICHE") or None,
        "actif": 1 if feuille("ACTIF") == "Oui" else 0, "type_enregistrement": feuille("TYPE_ENREGISTREMENT") or None,
        "niveau": sous("NOMENCLATURE_EUROPE", "NIVEAU") or None,
        "nsf_code": " | ".join(c for c, _ in nsf) or None, "nsf_libelle": " | ".join(l for _, l in nsf) or None,
        "date_decision": iso(feuille("DATE_DECISION")), "duree_ans": to_int(feuille("DUREE_ENREGISTREMENT")),
        "date_fin_enregistrement": iso(feuille("DATE_FIN_ENREGISTREMENT")),
        "date_limite_delivrance": iso(feuille("DATE_LIMITE_DELIVRANCE")),
        "modalites_renouvellement": feuille("MODALITES_RENOUVELLEMENT") or None,
        "objectifs_contexte": feuille("OBJECTIFS_CONTEXTE") or None, "lien_url": lien[0][:300] if lien else None,
        "nb_partenaires": sum(1 for c in ch.get("PARTENAIRES", []) if tag(c) == "PARTENAIRE"),
        "nb_certifies": to_int(derniere.get("NOMBRE_CERTIFIES")), "annee_stat": to_int(derniere.get("ANNEE")),
        "taux_insertion_6mois": to_int(derniere.get("TAUX_INSERTION_GLOBAL_6MOIS")),
        "taux_insertion_2ans": to_int(derniere.get("TAUX_INSERTION_METIER_2ANS")),
        "formacodes": liste_codes("FORMACODES", "FORMACODE"), "romes": liste_codes("CODES_ROME", "ROME"),
        "anciennes": ids("ANCIENNES_CERTIFICATIONS", "ANCIENNE_CERTIFICATION", "ID_FICHE_ANCIENNE_CERTIFICATION"),
        "nouvelles": ids("NOUVELLES_CERTIFICATIONS", "NOUVELLE_CERTIFICATION", "ID_FICHE_NOUVELLE_CERTIFICATION"),
        "correspondances": corr, "certificateurs": certs,
    }


COLONNES = ("intitule", "type_diplome_code", "type_diplome_libelle", "etat_fiche", "actif", "type_enregistrement", "niveau",
            "nsf_code", "nsf_libelle", "date_decision", "duree_ans", "date_fin_enregistrement", "date_limite_delivrance",
            "modalites_renouvellement", "objectifs_contexte", "lien_url", "nb_partenaires", "nb_certifies", "annee_stat",
            "taux_insertion_6mois", "taux_insertion_2ans")


def empreinte(f):
    corps = {k: f[k] for k in COLONNES}
    for k in ("formacodes", "romes", "anciennes", "nouvelles", "correspondances"):
        corps[k] = f[k]
    corps["cert"] = sorted((c[0], c[3]) for c in f["certificateurs"])
    return hashlib.sha1(json.dumps(corps, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


# ---------------------------------------------------------------- base
def ouvrir(db):
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    with open(SCHEMA, encoding="utf-8") as f:
        con.executescript(f.read())
    return con


def sha256_fichier(*chemins):
    h = hashlib.sha256()
    for p in chemins:
        with open(p, "rb") as f:
            while chunk := f.read(1 << 20):
                h.update(chunk)
    return h.hexdigest()


def charger_regles(con, date_import, chemin):
    """Charge les règles d'exclusion du CSV dans la base (le CSV fait foi, y compris pour le statut)."""
    if not chemin or not os.path.exists(chemin):
        return 0
    n = 0
    with open(chemin, encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f, delimiter=";"):
            typ, val = (r.get("type") or "").strip(), (r.get("valeur") or "").strip()
            if typ not in ("siren", "siret", "motif_nom") or not val:
                continue
            motif = " · ".join(x for x in ((r.get("nature") or "").strip(), (r.get("libelle") or "").strip()) if x)
            statut = "valide" if (r.get("statut") or "").strip() == "valide" else "a_valider"
            con.execute("""INSERT INTO exclusion_regle(type, valeur, motif, statut, cree_le) VALUES (?,?,?,?,?)
                           ON CONFLICT(type, valeur) DO UPDATE SET motif=excluded.motif, statut=excluded.statut""",
                        (typ, val, motif, statut, date_import))
            n += 1
    return n


def debut_validite(con, rep, num, champ, premiere_vue):
    r = con.execute("SELECT MAX(valide_au) FROM certification_historique WHERE repertoire=? AND numero_fiche=? AND champ=?",
                    (rep, num, champ)).fetchone()[0]
    return r or premiere_vue


def importer(con, date_import, rncp_zip, rs_zip, source="", exclusions_csv=EXCLUSIONS_CSV, appliquer_a_valider=False):
    if con.execute("SELECT 1 FROM import_log WHERE date_import=?", (date_import,)).fetchone():
        log(f"import du {date_import} déjà effectué : rien à faire")
        return None
    reference = con.execute("SELECT COUNT(*) FROM import_log").fetchone()[0] == 0
    ts = dt.datetime.now().isoformat(timespec="seconds")
    nb = {"fiches": 0, "nouvelle": 0, "modifiee": 0, "disparue": 0}
    with con:
        con.execute("INSERT INTO audit_log(ts, auteur, operation, objet_ref, nouvelle_valeur) VALUES (?,?,?,?,?)",
                    (ts, "script", "import_debut", date_import, source))
        anciennes = {(r["repertoire"], r["numero_fiche"]): dict(r) for r in con.execute(
            "SELECT repertoire, numero_fiche, empreinte, disparue, premiere_vue, " + ", ".join(CHAMPS_SUIVIS) + " FROM certification")}
        liens_actuels = collections.defaultdict(dict)       # fiche -> {etab_cle: (entreprise, etat, role, regle, rang)}
        for r in con.execute("SELECT repertoire, numero_fiche, etab_cle, entreprise_cle, etat_certificateur, role, regle_leader, rang_portefeuille "
                             "FROM certification_etablissement WHERE valide_au IS NULL"):
            liens_actuels[(r["repertoire"], r["numero_fiche"])][r["etab_cle"]] = (r["entreprise_cle"], r["etat_certificateur"], r["role"], r["regle_leader"], r["rang_portefeuille"])

        def evt(rep, num, typ, ancienne=None, nouvelle=None, detail=None):
            if not reference:
                con.execute("INSERT INTO evenement(date_import, repertoire, numero_fiche, type, ancienne_valeur, nouvelle_valeur, detail) VALUES (?,?,?,?,?,?,?)",
                            (date_import, rep, num, typ, ancienne, nouvelle, detail))

        # ---- phase A : fiches
        fiches = {}
        for rep, chemin in (("RNCP", rncp_zip), ("RS", rs_zip)):
            for f in lire_fiches(chemin, rep):
                cle = (rep, f["numero_fiche"])
                if cle in fiches:
                    continue
                fiches[cle] = f
                nb["fiches"] += 1
                emp = empreinte(f)
                anc = anciennes.get(cle)
                valeurs = [f[c] for c in COLONNES]
                if anc is None:
                    nb["nouvelle"] += 0 if reference else 1
                    evt(rep, f["numero_fiche"], "nouvelle", None, f["date_fin_enregistrement"],
                        "inactive" if not f["actif"] else None)
                    con.execute("INSERT INTO certification (repertoire, numero_fiche, " + ", ".join(COLONNES) +
                                ", empreinte, fiche_de_reference, premiere_vue, derniere_vue, disparue, import_id) VALUES (?,?," +
                                ",".join("?" * len(COLONNES)) + ",?,?,?,?,0,?)",
                                [rep, f["numero_fiche"]] + valeurs + [emp, 1 if reference else 0, date_import, date_import, date_import])
                else:
                    if anc["disparue"]:
                        evt(rep, f["numero_fiche"], "retour")
                    if anc["empreinte"] != emp:
                        changes = [c for c in CHAMPS_SUIVIS if anc[c] != f[c]]
                        if changes and not reference:
                            nb["modifiee"] += 1
                        for c in changes:
                            if not reference:
                                con.execute("INSERT INTO certification_historique(repertoire, numero_fiche, champ, ancienne_valeur, nouvelle_valeur, valide_du, valide_au, import_id) VALUES (?,?,?,?,?,?,?,?)",
                                            (rep, f["numero_fiche"], c, anc[c], f[c], debut_validite(con, rep, f["numero_fiche"], c, anc["premiere_vue"]), date_import, date_import))
                        if "actif" in changes:
                            evt(rep, f["numero_fiche"], "activee" if f["actif"] else "desactivee")
                        if "date_fin_enregistrement" in changes:
                            evt(rep, f["numero_fiche"], "echeance_modifiee", anc["date_fin_enregistrement"], f["date_fin_enregistrement"])
                        autres = [c for c in changes if c not in ("actif", "date_fin_enregistrement")]
                        if autres:
                            evt(rep, f["numero_fiche"], "modifiee", None, None, ", ".join(autres))
                        con.execute("UPDATE certification SET " + ", ".join(f"{c}=?" for c in COLONNES) +
                                    ", empreinte=?, derniere_vue=?, disparue=0, import_id=? WHERE repertoire=? AND numero_fiche=?",
                                    valeurs + [emp, date_import, date_import, rep, f["numero_fiche"]])
                    else:
                        con.execute("UPDATE certification SET derniere_vue=?, disparue=0, import_id=? WHERE repertoire=? AND numero_fiche=?",
                                    (date_import, date_import, rep, f["numero_fiche"]))
                if anc is None or anc["empreinte"] != emp:
                    ecrire_listes(con, rep, f)
        for cle, anc in anciennes.items():
            if cle not in fiches and not anc["disparue"]:
                nb["disparue"] += 1
                con.execute("UPDATE certification SET disparue=1 WHERE repertoire=? AND numero_fiche=?", cle)
                evt(cle[0], cle[1], "disparue")

        # ---- phase B : entreprises, exclusions, sites web, rôles
        entreprises_par_fiche = maj_entreprises(con, date_import, fiches)
        n_regles = charger_regles(con, date_import, exclusions_csv)
        n_excl, n_a_valider = appliquer_exclusions(con, appliquer_a_valider)
        sites = maj_sites_web(con, date_import, fiches, entreprises_par_fiche)
        maj_roles(con, date_import, fiches, entreprises_par_fiche, sites, liens_actuels, reference, anciennes, evt)
        con.execute("""INSERT OR IGNORE INTO file_qualification(type, objet_ref, detail, cree_le)
                       SELECT 'sans_siret', entreprise_cle, nom_usuel, ? FROM entreprise
                       WHERE a_qualifier=1 AND exclue=0 AND nb_certifications_actives>0""", (date_import,))
        con.execute("INSERT INTO import_log VALUES (?,?,?,?,?,?,?,?)",
                    (date_import, source, sha256_fichier(rncp_zip, rs_zip), nb["fiches"], nb["nouvelle"],
                     nb["modifiee"], nb["disparue"], 1 if reference else 0))
        con.execute("INSERT INTO audit_log(ts, auteur, operation, objet_ref, nouvelle_valeur) VALUES (?,?,?,?,?)",
                    (ts, "script", "import_fin", date_import,
                     json.dumps({**nb, "exclusions_appliquees": n_excl, "regles_a_valider_ignorees": n_a_valider}, ensure_ascii=False)))
    log(f"import {date_import} : {nb['fiches']} fiches, {nb['nouvelle']} nouvelles, {nb['modifiee']} modifiées, {nb['disparue']} disparues"
        + (" (premier import = base de référence, aucun événement créé)" if reference else ""))
    if n_regles and n_a_valider:
        log(f"{n_a_valider} règle(s) d'exclusion encore « à valider » : non appliquée(s) (voir la commande « exclusions »)")
    return nb


def ecrire_listes(con, rep, f):
    num = f["numero_fiche"]
    for t in ("certification_formacode", "certification_rome", "certification_filiation", "certification_correspondance"):
        con.execute(f"DELETE FROM {t} WHERE repertoire=? AND numero_fiche=?", (rep, num))
    con.executemany("INSERT OR IGNORE INTO certification_formacode VALUES (?,?,?,?)", [(rep, num, c, l) for c, l in f["formacodes"]])
    con.executemany("INSERT OR IGNORE INTO certification_rome VALUES (?,?,?,?)", [(rep, num, c, l) for c, l in f["romes"]])
    con.executemany("INSERT OR IGNORE INTO certification_filiation VALUES (?,?,?,?)",
                    [(rep, num, x, "ancienne") for x in f["anciennes"]] + [(rep, num, x, "nouvelle") for x in f["nouvelles"]])
    con.executemany("INSERT INTO certification_correspondance(repertoire, numero_fiche, bloc_source, fiche_destination, bloc_destination) VALUES (?,?,?,?,?)",
                    [(rep, num, a, b, c) for a, b, c in f["correspondances"]])


def maj_entreprises(con, date_import, fiches):
    """Crée ou met à jour entreprises et établissements ; retourne {fiche: [entreprise_cle, ...]} (dédoublonné)."""
    noms_ent = collections.defaultdict(collections.Counter)
    noms_etab = collections.defaultdict(collections.Counter)
    etab_info = {}
    par_fiche = {}
    for cle, f in fiches.items():
        vus = []
        for etab, siret, nom, etat in f["certificateurs"]:
            ent = cle_ent(etab)
            noms_ent[ent][nom or etab] += 1
            noms_etab[etab][nom or etab] += 1
            etab_info[etab] = (siret, etat, ent)
            if ent not in vus:
                vus.append(ent)
        par_fiche[cle] = vus
    existantes = {r["entreprise_cle"]: (r["noms_connus"], r["premiere_vue"]) for r in con.execute("SELECT entreprise_cle, noms_connus, premiere_vue FROM entreprise")}
    for ent, c in noms_ent.items():
        usuel = c.most_common(1)[0][0]
        connus = sorted(c)
        if ent in existantes:
            connus = sorted(set(json.loads(existantes[ent][0] or "[]")) | set(connus))
            con.execute("UPDATE entreprise SET nom_usuel=?, noms_connus=?, derniere_vue=? WHERE entreprise_cle=?",
                        (usuel, json.dumps(connus, ensure_ascii=False), date_import, ent))
        else:
            con.execute("INSERT INTO entreprise(entreprise_cle, siren, nom_usuel, noms_connus, a_qualifier, premiere_vue, derniere_vue) VALUES (?,?,?,?,?,?,?)",
                        (ent, None if ent.startswith("NOM:") else ent, usuel, json.dumps(connus, ensure_ascii=False),
                         1 if ent.startswith("NOM:") else 0, date_import, date_import))
    existants = {r[0] for r in con.execute("SELECT etab_cle FROM etablissement")}
    for etab, (siret, etat, ent) in etab_info.items():
        nom = noms_etab[etab].most_common(1)[0][0]
        if etab in existants:
            con.execute("UPDATE etablissement SET nom=?, etat=?, derniere_vue=? WHERE etab_cle=?", (nom, etat, date_import, etab))
        else:
            con.execute("INSERT INTO etablissement(etab_cle, siret, entreprise_cle, nom, etat, premiere_vue, derniere_vue) VALUES (?,?,?,?,?,?,?)",
                        (etab, siret, ent, nom, etat, date_import, date_import))
    return par_fiche


def appliquer_exclusions(con, appliquer_a_valider=False):
    con.execute("UPDATE entreprise SET exclue=0, motif_exclusion=NULL")
    regles = [dict(r) for r in con.execute("SELECT type, valeur, motif, statut FROM exclusion_regle")]
    actives = [r for r in regles if r["statut"] == "valide" or appliquer_a_valider]
    n = 0
    for r in actives:
        motif = r["motif"] or r["valeur"]
        if r["type"] == "siren":
            n += con.execute("UPDATE entreprise SET exclue=1, motif_exclusion=? WHERE siren=? AND exclue=0", (motif, r["valeur"])).rowcount
        elif r["type"] == "siret":
            n += con.execute("UPDATE entreprise SET exclue=1, motif_exclusion=? WHERE exclue=0 AND entreprise_cle IN (SELECT entreprise_cle FROM etablissement WHERE siret=?)",
                             (motif, r["valeur"])).rowcount
        else:
            rx = re.compile(r["valeur"], re.I)
            for e in con.execute("SELECT entreprise_cle, nom_usuel FROM entreprise WHERE siren IS NULL AND exclue=0").fetchall():
                if rx.search(e["nom_usuel"]):
                    con.execute("UPDATE entreprise SET exclue=1, motif_exclusion=? WHERE entreprise_cle=?", (motif or "nom", e["entreprise_cle"]))
                    n += 1
    ignorees = 0 if appliquer_a_valider else len([r for r in regles if r["statut"] != "valide"])
    return n, ignorees


def maj_sites_web(con, date_import, fiches, par_fiche):
    """Déduit le site de chaque entreprise des liens de fiches à un seul certificateur ; retourne {entreprise: domaine}."""
    cand = collections.defaultdict(collections.Counter)
    for cle, f in fiches.items():
        ents = par_fiche[cle]
        d = domaine(f["lien_url"])
        if len(ents) == 1 and d and d not in PORTAILS:
            cand[ents[0]][d] += 1
    avec_actives = {e for cle, f in fiches.items() if f["actif"] for e in par_fiche[cle]}
    partage = collections.defaultdict(set)
    for ent, c in cand.items():
        for d in c:
            partage[d].add(ent)
    courant = {r["entreprise_cle"]: dict(r) for r in con.execute(
        "SELECT entreprise_cle, url_racine, source, statut FROM entreprise_site_web WHERE valide_au IS NULL")}
    for ent, c in cand.items():
        c = collections.Counter({d: n for d, n in c.items() if len(partage[d]) <= MAX_ENTREPRISES_PAR_DOMAINE})
        if not c:
            continue
        dom, n = c.most_common(1)[0]
        cur = courant.get(ent)
        if n / sum(c.values()) < SEUIL_DOMAINE_DOMINANT:
            if ent not in avec_actives:
                continue
            con.execute("INSERT OR IGNORE INTO file_qualification(type, objet_ref, detail, cree_le) VALUES ('site_ambigu',?,?,?)",
                        (ent, json.dumps(dict(c), ensure_ascii=False), date_import))
            continue
        url = "https://" + dom
        if cur and cur["url_racine"] == url:
            continue
        if cur and not (cur["source"] == "lien_fiche_unique" and cur["statut"] == "a_valider"):
            continue                                   # valeur validée, saisie ou recherchée : on n'y touche pas
        if cur:
            con.execute("UPDATE entreprise_site_web SET valide_au=? WHERE entreprise_cle=? AND valide_au IS NULL", (date_import, ent))
        con.execute("INSERT INTO entreprise_site_web VALUES (?,?,?,?,?,?,?,NULL)",
                    (ent, url, "lien_fiche_unique", "a_valider", date_import, None, date_import))
    return {r["entreprise_cle"]: domaine(r["url_racine"]) for r in con.execute(
        "SELECT entreprise_cle, url_racine FROM entreprise_site_web WHERE valide_au IS NULL AND statut<>'invalide'")}


def maj_roles(con, date_import, fiches, par_fiche, sites, liens_actuels, reference, anciennes, evt):
    """Désigne le leader de chaque certification co-certifiée (règles validées) et synchronise les liaisons."""
    actives = collections.Counter()
    prochaine = {}
    for cle, f in fiches.items():
        if f["actif"]:
            for ent in par_fiche[cle]:
                actives[ent] += 1
                d = f["date_fin_enregistrement"]
                if d and d >= date_import and (ent not in prochaine or d < prochaine[ent]):
                    prochaine[ent] = d
    for cle, f in fiches.items():
        ents = par_fiche[cle]
        roles = {}
        if len(ents) == 1:
            roles[ents[0]] = ("unique", None)
        elif len(ents) >= 2:
            dl = domaine(f["lien_url"])
            par_domaine = [e for e in ents if dl and sites.get(e) == dl]
            leader = regle = None
            if len(par_domaine) == 1:
                leader, regle = par_domaine[0], "domaine"
            else:
                mx = max(actives[e] for e in ents)
                tete = [e for e in ents if actives[e] == mx]
                if len(tete) == 1:
                    leader, regle = tete[0], "portefeuille"
            for e in ents:
                if leader is None:
                    roles[e] = ("indetermine", None)
                else:
                    roles[e] = ("leader", regle) if e == leader else ("co", None)
        classement = sorted((actives[e] for e in ents), reverse=True)
        desire = {}
        for etab, siret, nom, etat in f["certificateurs"]:
            ent = cle_ent(etab)
            role, regle = roles[ent]
            desire[etab] = (ent, etat, role, regle, 1 + classement.index(actives[ent]))
        actuel = liens_actuels.get(cle, {})
        for etab, val in actuel.items():
            if desire.get(etab) != val:
                con.execute("UPDATE certification_etablissement SET valide_au=? WHERE repertoire=? AND numero_fiche=? AND etab_cle=? AND valide_au IS NULL",
                            (date_import, cle[0], cle[1], etab))
        for etab, val in desire.items():
            if actuel.get(etab) != val:
                con.execute("INSERT INTO certification_etablissement(repertoire, numero_fiche, etab_cle, entreprise_cle, etat_certificateur, role, regle_leader, rang_portefeuille, valide_du, import_id) VALUES (?,?,?,?,?,?,?,?,?,?)",
                            (cle[0], cle[1], etab, val[0], val[1], val[2], val[3], val[4], date_import, date_import))
        con.execute("UPDATE certification SET nb_certificateurs=? WHERE repertoire=? AND numero_fiche=?", (len(ents), cle[0], cle[1]))
        anc = anciennes.get(cle)
        if anc is not None and not reference:
            avant = sorted({v[0] for v in actuel.values()})
            apres = sorted(ents)
            if actuel and avant != apres:
                evt(cle[0], cle[1], "modifiee", " | ".join(avant), " | ".join(apres), "certificateurs")
                con.execute("INSERT INTO certification_historique(repertoire, numero_fiche, champ, ancienne_valeur, nouvelle_valeur, valide_du, valide_au, import_id) VALUES (?,?,?,?,?,?,?,?)",
                            (cle[0], cle[1], "certificateurs", " | ".join(avant), " | ".join(apres),
                             debut_validite(con, cle[0], cle[1], "certificateurs", anc["premiere_vue"]), date_import, date_import))
    con.execute("UPDATE entreprise SET nb_certifications_actives=0, prochaine_echeance=NULL")
    con.executemany("UPDATE entreprise SET nb_certifications_actives=?, prochaine_echeance=? WHERE entreprise_cle=?",
                    [(n, prochaine.get(e), e) for e, n in actives.items()])


# ---------------------------------------------------------------- exports
def ecrire_csv(chemin, lignes):
    lignes = [dict(r) for r in lignes]
    out = open(chemin, "w", newline="", encoding="utf-8-sig") if chemin else sys.stdout
    try:
        if lignes:
            w = csv.DictWriter(out, fieldnames=list(lignes[0].keys()), delimiter=";")
            w.writeheader()
            w.writerows(lignes)
    finally:
        if chemin:
            out.close()
    log(f"{len(lignes)} lignes" + (f" -> {chemin}" if chemin else ""))


def cmd_nouveautes(con, a):
    q = """SELECT e.date_import, e.type, e.repertoire, e.numero_fiche, c.intitule, c.niveau, c.type_enregistrement,
                  c.date_fin_enregistrement AS echeance, c.duree_ans, c.fiche_de_reference,
                  (SELECT group_concat(DISTINCT n.nom_usuel) FROM certification_etablissement ce JOIN entreprise n ON n.entreprise_cle=ce.entreprise_cle
                   WHERE ce.repertoire=c.repertoire AND ce.numero_fiche=c.numero_fiche AND ce.valide_au IS NULL) AS entreprises,
                  e.ancienne_valeur, e.nouvelle_valeur, e.detail
           FROM evenement e JOIN certification c USING (repertoire, numero_fiche)
           WHERE e.date_import >= ? ORDER BY e.date_import, e.type, c.intitule"""
    ecrire_csv(a.out, con.execute(q, (a.depuis,)))


def cmd_echeances(con, a):
    filtre, args = "o.delai_mois BETWEEN ? AND ?", [a.mois_min, a.mois_max]
    if a.sur_demande_seulement:
        filtre += " AND o.type_enregistrement = ?"
        args.append(SUR_DEMANDE)
    q = f"""SELECT o.echeance, o.delai_mois, o.tranche, o.entreprise_cle, o.siren, o.entreprise, w.url_racine AS site_web,
                   o.repertoire, o.numero_fiche, o.intitule, o.niveau, o.type_enregistrement, o.role, o.nb_certificateurs,
                   (SELECT group_concat(e2.nom_usuel, ' | ') FROM
                      (SELECT DISTINCT ce2.entreprise_cle AS k FROM certification_etablissement ce2
                       WHERE ce2.repertoire=o.repertoire AND ce2.numero_fiche=o.numero_fiche AND ce2.valide_au IS NULL
                         AND ce2.entreprise_cle<>o.entreprise_cle) x JOIN entreprise e2 ON e2.entreprise_cle=x.k) AS co_certificateurs,
                   o.lien_url
            FROM v_opportunites o
            LEFT JOIN entreprise_site_web w ON w.entreprise_cle=o.entreprise_cle AND w.valide_au IS NULL AND w.statut<>'invalide'
            WHERE {filtre} ORDER BY o.echeance, o.entreprise"""
    ecrire_csv(a.out, con.execute(q, args))


def cmd_qualification(con, a):
    ecrire_csv(a.out, con.execute("SELECT id, type, objet_ref, detail, cree_le FROM file_qualification WHERE resolu_le IS NULL ORDER BY type, cree_le, id"))


def cmd_exclusions(con, a):
    ecrire_csv(a.out, con.execute("""SELECT r.type, r.valeur, r.motif, r.statut,
        (SELECT COUNT(*) FROM entreprise e WHERE e.motif_exclusion=r.motif AND e.exclue=1) AS entreprises_exclues
        FROM exclusion_regle r ORDER BY r.statut, r.type, r.valeur"""))


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = p.add_subparsers(dest="cmd", required=True)
    i = sp.add_parser("importer", help="importe l'export du jour (téléchargement automatique)")
    i.add_argument("--db", required=True)
    i.add_argument("--date", help="AAAA-MM-JJ de l'export (défaut : le plus récent)")
    i.add_argument("--rncp-zip"); i.add_argument("--rs-zip")
    i.add_argument("--exclusions", default=EXCLUSIONS_CSV, help="CSV des règles d'exclusion (défaut : exclusions.csv)")
    i.add_argument("--appliquer-a-valider", action="store_true", help="applique aussi les règles encore « à valider » (test)")
    n = sp.add_parser("nouveautes", help="événements depuis une date")
    n.add_argument("--db", required=True); n.add_argument("--depuis", required=True); n.add_argument("--out")
    e = sp.add_parser("echeances", help="échéances de renouvellement dans une fenêtre de mois, par entreprise")
    e.add_argument("--db", required=True); e.add_argument("--mois-min", type=float, default=0)
    e.add_argument("--mois-max", type=float, default=24); e.add_argument("--sur-demande-seulement", action="store_true")
    e.add_argument("--out")
    q = sp.add_parser("qualification", help="cas à trancher par une personne")
    q.add_argument("--db", required=True); q.add_argument("--out")
    x = sp.add_parser("exclusions", help="règles d'exclusion et nombre d'entreprises touchées")
    x.add_argument("--db", required=True); x.add_argument("--out")
    a = p.parse_args(argv)
    con = ouvrir(a.db)
    if a.cmd == "importer":
        if a.rncp_zip and a.rs_zip:
            if not a.date:
                p.error("--date est obligatoire avec des zips locaux")
            importer(con, a.date, a.rncp_zip, a.rs_zip, "local", a.exclusions, a.appliquer_a_valider)
        else:
            d, u_rncp, u_rs = trouver_exports(a.date)
            with tempfile.TemporaryDirectory() as tmp:
                z1, z2 = os.path.join(tmp, "rncp.zip"), os.path.join(tmp, "rs.zip")
                log(f"export du {d} : téléchargement")
                http_get(u_rncp, z1); http_get(u_rs, z2)
                importer(con, d, z1, z2, u_rncp, a.exclusions, a.appliquer_a_valider)
    elif a.cmd == "nouveautes":
        cmd_nouveautes(con, a)
    elif a.cmd == "echeances":
        cmd_echeances(con, a)
    elif a.cmd == "qualification":
        cmd_qualification(con, a)
    else:
        cmd_exclusions(con, a)


if __name__ == "__main__":
    main()
