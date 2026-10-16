#!/usr/bin/env python3
"""Veille quotidienne RNCP / RS : import de l'export France compétences (data.gouv.fr),
détection des nouveautés et export des échéances à relancer.

Python 3.9+, bibliothèque standard uniquement.

  python3 veille_rncp_rs.py importer  --db veille.db                 # télécharge l'export du jour
  python3 veille_rncp_rs.py importer  --db veille.db --rncp-zip a.zip --rs-zip b.zip --date 2026-10-15
  python3 veille_rncp_rs.py nouveautes --db veille.db --depuis 2026-10-01 --out nouveautes.csv
  python3 veille_rncp_rs.py echeances --db veille.db --mois-min 0 --mois-max 24 --out echeances.csv
"""
import argparse, csv, datetime as dt, hashlib, json, os, sqlite3, sys, tempfile, time, zipfile
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET

DATASET_API = "https://www.data.gouv.fr/api/1/datasets/5eebbc067a14b6fecc9c9976/"
SCHEMA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "schema.sql")
SCHEMA_EXPORT = "v4-1"           # version du schéma XML France compétences
SUR_DEMANDE = "Enregistrement sur demande"


def log(msg):
    print(f"[{dt.datetime.now():%H:%M:%S}] {msg}", file=sys.stderr, flush=True)


def tag(el):
    return el.tag.split("}")[-1]


def iso(s):
    s = (s or "").strip()
    try:
        return dt.datetime.strptime(s, "%d/%m/%Y").date().isoformat()
    except ValueError:
        return None


def to_int(s):
    try:
        return int((s or "").strip())
    except ValueError:
        return None


# ---------------------------------------------------------------- téléchargement
def http_get(url, dest, retries=8, timeout=60):
    """Téléchargement avec reprise (Range) : le proxy ou le réseau peut couper les gros fichiers."""
    for essai in range(1, retries + 1):
        taille = os.path.getsize(dest) if os.path.exists(dest) else 0
        req = urllib.request.Request(url, headers={"User-Agent": "veille-rncp-rs/1.0"})
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
CHAMPS_SUIVIS = ("INTITULE", "ETAT_FICHE", "ACTIF", "TYPE_ENREGISTREMENT", "DATE_DECISION",
                 "DUREE_ENREGISTREMENT", "DATE_FIN_ENREGISTREMENT", "DATE_LIMITE_DELIVRANCE")


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
                d, certs = {}, []
                for c in el:
                    t = tag(c)
                    if t == "CERTIFICATEURS":
                        for cc in c:
                            x = {tag(i): (i.text or "").strip() for i in cc}
                            nom = x.get("NOM_CERTIFICATEUR", "")
                            if nom or x.get("SIRET_CERTIFICATEUR"):
                                certs.append(x)
                    elif len(c) == 0:
                        d[t] = (c.text or "").strip()
                num = d.get("NUMERO_FICHE")
                if num:
                    yield {
                        "repertoire": repertoire, "numero_fiche": num,
                        "intitule": d.get("INTITULE"), "etat_fiche": d.get("ETAT_FICHE"),
                        "actif": 1 if d.get("ACTIF") == "Oui" else 0,
                        "type_enregistrement": d.get("TYPE_ENREGISTREMENT"),
                        "date_decision": iso(d.get("DATE_DECISION")),
                        "duree_ans": to_int(d.get("DUREE_ENREGISTREMENT")),
                        "date_fin_enregistrement": iso(d.get("DATE_FIN_ENREGISTREMENT")),
                        "date_limite_delivrance": iso(d.get("DATE_LIMITE_DELIVRANCE")),
                        "lien_url": (d.get("LIEN_URL_DESCRIPTION") or "").split()[0][:300] if d.get("LIEN_URL_DESCRIPTION") else None,
                        "certificateurs": certs,
                    }
                el.clear()
                if racine is not None:
                    racine.clear()


def empreinte(f):
    cles = sorted(
        (c.get("SIRET_CERTIFICATEUR", "") + "|" + c.get("NOM_CERTIFICATEUR", "") + "|" + c.get("ETAT_CERTIFICATEUR", ""))
        for c in f["certificateurs"])
    base = "§".join(str(f[k] if f[k] is not None else "") for k in
                    ("intitule", "etat_fiche", "actif", "type_enregistrement", "date_decision",
                     "duree_ans", "date_fin_enregistrement", "date_limite_delivrance")) + "§" + ";".join(cles)
    return hashlib.sha1(base.encode("utf-8")).hexdigest()


def cle_certificateur(c):
    siret = (c.get("SIRET_CERTIFICATEUR") or "").strip()
    return siret if siret else "NOM:" + (c.get("NOM_CERTIFICATEUR") or "").strip().upper()


# ---------------------------------------------------------------- import
def ouvrir(db):
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    con.executescript(open(SCHEMA, encoding="utf-8").read())
    return con


def sha256_fichier(*chemins):
    h = hashlib.sha256()
    for p in chemins:
        with open(p, "rb") as f:
            while chunk := f.read(1 << 20):
                h.update(chunk)
    return h.hexdigest()


def importer(con, date_import, rncp_zip, rs_zip, source=""):
    deja = con.execute("SELECT 1 FROM import_log WHERE date_import=?", (date_import,)).fetchone()
    if deja:
        log(f"import du {date_import} déjà effectué : rien à faire")
        return None
    reference = con.execute("SELECT COUNT(*) FROM import_log").fetchone()[0] == 0
    existantes = {(r["repertoire"], r["numero_fiche"]): (r["empreinte"], r["actif"], r["date_fin_enregistrement"], r["disparue"])
                  for r in con.execute("SELECT repertoire, numero_fiche, empreinte, actif, date_fin_enregistrement, disparue FROM certification")}
    vues, nb = set(), {"nouvelle": 0, "modifiee": 0, "disparue": 0, "fiches": 0}

    def evt(rep, num, typ, detail=None):
        if not reference:
            con.execute("INSERT INTO evenement(date_import, repertoire, numero_fiche, type, detail) VALUES (?,?,?,?,?)",
                        (date_import, rep, num, typ, detail))

    with con:
        for rep, chemin in (("RNCP", rncp_zip), ("RS", rs_zip)):
            for f in lire_fiches(chemin, rep):
                cle = (rep, f["numero_fiche"])
                if cle in vues:        # doublon dans le fichier
                    continue
                vues.add(cle)
                nb["fiches"] += 1
                emp = empreinte(f)
                ancien = existantes.get(cle)
                if ancien is None:
                    nb["nouvelle"] += 0 if reference else 1
                    evt(rep, f["numero_fiche"], "nouvelle",
                        f"échéance {f['date_fin_enregistrement']}" if f["actif"] else "inactive")
                    con.execute("""INSERT INTO certification VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,0)""",
                        (rep, f["numero_fiche"], f["intitule"], f["etat_fiche"], f["actif"], f["type_enregistrement"],
                         f["date_decision"], f["duree_ans"], f["date_fin_enregistrement"], f["date_limite_delivrance"],
                         f["lien_url"], emp, date_import, date_import))
                else:
                    emp_old, actif_old, fin_old, disp_old = ancien
                    if disp_old:
                        evt(rep, f["numero_fiche"], "retour")
                    if emp_old != emp:
                        nb["modifiee"] += 0 if reference else 1
                        if actif_old != f["actif"]:
                            evt(rep, f["numero_fiche"], "activee" if f["actif"] else "desactivee")
                        if fin_old != f["date_fin_enregistrement"]:
                            evt(rep, f["numero_fiche"], "echeance_modifiee", f"{fin_old} -> {f['date_fin_enregistrement']}")
                        if actif_old == f["actif"] and fin_old == f["date_fin_enregistrement"]:
                            evt(rep, f["numero_fiche"], "modifiee")
                    con.execute("""UPDATE certification SET intitule=?, etat_fiche=?, actif=?, type_enregistrement=?, date_decision=?,
                                   duree_ans=?, date_fin_enregistrement=?, date_limite_delivrance=?, lien_url=?, empreinte=?,
                                   derniere_vue=?, disparue=0 WHERE repertoire=? AND numero_fiche=?""",
                        (f["intitule"], f["etat_fiche"], f["actif"], f["type_enregistrement"], f["date_decision"],
                         f["duree_ans"], f["date_fin_enregistrement"], f["date_limite_delivrance"], f["lien_url"], emp,
                         date_import, rep, f["numero_fiche"]))
                    con.execute("DELETE FROM certification_certificateur WHERE repertoire=? AND numero_fiche=?", (rep, f["numero_fiche"]))
                for c in f["certificateurs"]:
                    k = cle_certificateur(c)
                    con.execute("INSERT OR IGNORE INTO certificateur(cle, siret, nom) VALUES (?,?,?)",
                                (k, (c.get("SIRET_CERTIFICATEUR") or None), c.get("NOM_CERTIFICATEUR") or k))
                    con.execute("INSERT OR REPLACE INTO certification_certificateur VALUES (?,?,?,?)",
                                (rep, f["numero_fiche"], k, c.get("ETAT_CERTIFICATEUR")))
        # fiches absentes de l'export du jour
        for cle, (_, _, _, disp) in existantes.items():
            if cle not in vues and not disp:
                nb["disparue"] += 1
                con.execute("UPDATE certification SET disparue=1 WHERE repertoire=? AND numero_fiche=?", cle)
                evt(cle[0], cle[1], "disparue")
        con.execute("INSERT INTO import_log VALUES (?,?,?,?,?,?,?,?)",
                    (date_import, source, sha256_fichier(rncp_zip, rs_zip), nb["fiches"], nb["nouvelle"],
                     nb["modifiee"], nb["disparue"], 1 if reference else 0))
    log(f"import {date_import} : {nb['fiches']} fiches, {nb['nouvelle']} nouvelles, {nb['modifiee']} modifiées, "
        f"{nb['disparue']} disparues" + (" (premier import = base de référence, aucun événement créé)" if reference else ""))
    return nb


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
    q = """SELECT e.date_import, e.type, e.repertoire, e.numero_fiche, c.intitule, c.type_enregistrement,
                  c.date_fin_enregistrement AS echeance, c.duree_ans,
                  (SELECT group_concat(k.nom, ' | ') FROM certification_certificateur cc JOIN certificateur k ON k.cle=cc.cle
                   WHERE cc.repertoire=c.repertoire AND cc.numero_fiche=c.numero_fiche) AS certificateurs,
                  e.detail
           FROM evenement e JOIN certification c USING (repertoire, numero_fiche)
           WHERE e.date_import >= ? ORDER BY e.date_import, e.type, c.intitule"""
    ecrire_csv(a.out, con.execute(q, (a.depuis,)))


def cmd_echeances(con, a):
    cond = "delai_mois BETWEEN ? AND ?"
    args = [a.mois_min, a.mois_max]
    if not a.inclure_de_droit:
        cond += " AND type_enregistrement = ?"
        args.append(SUR_DEMANDE)
    q = f"SELECT * FROM v_echeances WHERE {cond} ORDER BY echeance, certificateur"
    ecrire_csv(a.out, con.execute(q, args))


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = p.add_subparsers(dest="cmd", required=True)
    i = sp.add_parser("importer", help="importe l'export du jour (téléchargement automatique)")
    i.add_argument("--db", required=True)
    i.add_argument("--date", help="AAAA-MM-JJ de l'export (défaut : le plus récent)")
    i.add_argument("--rncp-zip"); i.add_argument("--rs-zip")
    n = sp.add_parser("nouveautes", help="événements depuis une date (nouvelles certifications, changements)")
    n.add_argument("--db", required=True); n.add_argument("--depuis", required=True); n.add_argument("--out")
    e = sp.add_parser("echeances", help="échéances de renouvellement dans une fenêtre de mois")
    e.add_argument("--db", required=True); e.add_argument("--mois-min", type=float, default=0)
    e.add_argument("--mois-max", type=float, default=24); e.add_argument("--inclure-de-droit", action="store_true")
    e.add_argument("--out")
    a = p.parse_args(argv)
    con = ouvrir(a.db)
    if a.cmd == "importer":
        if a.rncp_zip and a.rs_zip:
            if not a.date:
                p.error("--date est obligatoire avec des zips locaux")
            importer(con, a.date, a.rncp_zip, a.rs_zip, "local")
        else:
            d, u_rncp, u_rs = trouver_exports(a.date)
            with tempfile.TemporaryDirectory() as tmp:
                z1, z2 = os.path.join(tmp, "rncp.zip"), os.path.join(tmp, "rs.zip")
                log(f"export du {d} : téléchargement")
                http_get(u_rncp, z1); http_get(u_rs, z2)
                importer(con, d, z1, z2, u_rncp)
    elif a.cmd == "nouveautes":
        cmd_nouveautes(con, a)
    else:
        cmd_echeances(con, a)


if __name__ == "__main__":
    main()
