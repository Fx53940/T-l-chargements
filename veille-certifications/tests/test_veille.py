import os, sys, tempfile, unittest, zipfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import veille_rncp_rs as v


def fiche(num, intitule, actif="Oui", fin="01/10/2027", type_enr="Enregistrement sur demande", siret="11111111100011", nom="ECOLE TEST"):
    return f"""<FICHE><NUMERO_FICHE>{num}</NUMERO_FICHE><INTITULE>{intitule}</INTITULE><ETAT_FICHE>Publiée</ETAT_FICHE>
<ACTIF>{actif}</ACTIF><TYPE_ENREGISTREMENT>{type_enr}</TYPE_ENREGISTREMENT><DATE_DECISION>01/10/2024</DATE_DECISION>
<DUREE_ENREGISTREMENT>3</DUREE_ENREGISTREMENT><DATE_FIN_ENREGISTREMENT>{fin}</DATE_FIN_ENREGISTREMENT>
<LIEN_URL_DESCRIPTION>https://exemple.fr/{num}  texte</LIEN_URL_DESCRIPTION>
<CERTIFICATEURS><CERTIFICATEUR><SIRET_CERTIFICATEUR>{siret}</SIRET_CERTIFICATEUR><NOM_CERTIFICATEUR>{nom}</NOM_CERTIFICATEUR>
<ETAT_CERTIFICATEUR>Actif</ETAT_CERTIFICATEUR></CERTIFICATEUR></CERTIFICATEURS></FICHE>"""


def zipxml(path, fiches):
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("export.xml", "<?xml version='1.0'?><FICHES>" + "".join(fiches) + "</FICHES>")


class T(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        self.con = v.ouvrir(os.path.join(self.d.name, "t.db"))
        self.r, self.s = os.path.join(self.d.name, "r.zip"), os.path.join(self.d.name, "s.zip")

    def tearDown(self):
        self.con.close(); self.d.cleanup()

    def run_import(self, date, rncp, rs=()):
        zipxml(self.r, rncp); zipxml(self.s, rs)
        return v.importer(self.con, date, self.r, self.s)

    def evts(self):
        return [(e["type"], e["numero_fiche"]) for e in self.con.execute("SELECT * FROM evenement ORDER BY id")]

    def test_reference_puis_nouveautes(self):
        self.run_import("2026-10-14", [fiche("RNCP1", "A"), fiche("RNCP2", "B")])
        self.assertEqual(self.evts(), [])                    # base de référence : pas d'événements
        self.run_import("2026-10-15", [fiche("RNCP1", "A"), fiche("RNCP2", "B"), fiche("RNCP3", "C")], [fiche("RS9", "D")])
        self.assertEqual(sorted(self.evts()), [("nouvelle", "RNCP3"), ("nouvelle", "RS9")])

    def test_idempotent(self):
        self.run_import("2026-10-14", [fiche("RNCP1", "A")])
        self.assertIsNone(self.run_import("2026-10-14", [fiche("RNCP1", "A")]))

    def test_changements(self):
        self.run_import("2026-10-14", [fiche("RNCP1", "A"), fiche("RNCP2", "B"), fiche("RNCP3", "C")])
        self.run_import("2026-10-15", [fiche("RNCP1", "A", actif="Non"), fiche("RNCP2", "B", fin="01/10/2028")])
        e = dict((n, t) for t, n in self.evts())
        self.assertEqual(e["RNCP1"], "desactivee"); self.assertEqual(e["RNCP2"], "echeance_modifiee"); self.assertEqual(e["RNCP3"], "disparue")
        self.run_import("2026-10-16", [fiche("RNCP1", "A", actif="Non"), fiche("RNCP2", "B", fin="01/10/2028"), fiche("RNCP3", "C")])
        self.assertIn(("retour", "RNCP3"), self.evts())

    def test_certificateur_sans_siret_et_dates(self):
        self.run_import("2026-10-14", [fiche("RNCP1", "A", siret="", nom="Sans Siret")])
        self.assertEqual(self.con.execute("SELECT cle FROM certificateur").fetchone()[0], "NOM:SANS SIRET")
        self.assertEqual(self.con.execute("SELECT date_fin_enregistrement, lien_url FROM certification").fetchone()[:], ("2027-10-01", "https://exemple.fr/RNCP1"))

    def test_vue_echeances_actives_seulement(self):
        self.run_import("2026-10-14", [fiche("RNCP1", "A"), fiche("RNCP2", "B", actif="Non")])
        self.assertEqual([r["numero_fiche"] for r in self.con.execute("SELECT * FROM v_echeances")], ["RNCP1"])


if __name__ == "__main__":
    unittest.main()
