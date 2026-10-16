import csv, os, sys, tempfile, unittest, zipfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import veille_rncp_rs as v


def fiche(num, intitule="Cert", actif="Oui", fin="01/10/2027", type_enr="Enregistrement sur demande", certs=(("11111111100011", "ECOLE TEST"),),
          lien="", niveau="NIV6", nsf=("221", "Agro"), formacode=("21519", "Choco"), stat=None, nouvelles=(), partenaires=0):
    cs = "".join(f"<CERTIFICATEUR><SIRET_CERTIFICATEUR>{s}</SIRET_CERTIFICATEUR><NOM_CERTIFICATEUR>{n}</NOM_CERTIFICATEUR><ETAT_CERTIFICATEUR>Actif</ETAT_CERTIFICATEUR></CERTIFICATEUR>" for s, n in certs)
    st = "".join(f"<STATISTIQUES_PROMOTION><ANNEE>{a}</ANNEE><NOMBRE_CERTIFIES>{n}</NOMBRE_CERTIFIES><TAUX_INSERTION_GLOBAL_6MOIS>90</TAUX_INSERTION_GLOBAL_6MOIS></STATISTIQUES_PROMOTION>" for a, n in (stat or []))
    nv = "".join(f"<NOUVELLE_CERTIFICATION><ID_FICHE_NOUVELLE_CERTIFICATION>{x}</ID_FICHE_NOUVELLE_CERTIFICATION></NOUVELLE_CERTIFICATION>" for x in nouvelles)
    pt = "".join("<PARTENAIRE><NOM_PARTENAIRE>P</NOM_PARTENAIRE></PARTENAIRE>" for _ in range(partenaires))
    return f"""<FICHE><NUMERO_FICHE>{num}</NUMERO_FICHE><INTITULE>{intitule}</INTITULE><ETAT_FICHE>Publiée</ETAT_FICHE>
<ABREGE><CODE>TP</CODE><LIBELLE>Titre professionnel</LIBELLE></ABREGE>
<NOMENCLATURE_EUROPE><NIVEAU>{niveau}</NIVEAU><LIBELLE>Niveau</LIBELLE></NOMENCLATURE_EUROPE>
<CODES_NSF><NSF><CODE>{nsf[0]}</CODE><LIBELLE>{nsf[1]}</LIBELLE></NSF></CODES_NSF>
<FORMACODES><FORMACODE><CODE>{formacode[0]}</CODE><LIBELLE>{formacode[1]}</LIBELLE></FORMACODE></FORMACODES>
<ACTIF>{actif}</ACTIF><TYPE_ENREGISTREMENT>{type_enr}</TYPE_ENREGISTREMENT><DATE_DECISION>01/10/2024</DATE_DECISION>
<DUREE_ENREGISTREMENT>3</DUREE_ENREGISTREMENT><DATE_FIN_ENREGISTREMENT>{fin}</DATE_FIN_ENREGISTREMENT>
<OBJECTIFS_CONTEXTE>Objectifs de {num}</OBJECTIFS_CONTEXTE>
<LIEN_URL_DESCRIPTION>{lien}</LIEN_URL_DESCRIPTION>
<STATISTIQUES_PROMOTIONS>{st}</STATISTIQUES_PROMOTIONS><NOUVELLES_CERTIFICATIONS>{nv}</NOUVELLES_CERTIFICATIONS>
<PARTENAIRES>{pt}</PARTENAIRES>
<CERTIFICATEURS>{cs}</CERTIFICATEURS></FICHE>"""


def zipxml(path, fiches):
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("export.xml", "<?xml version='1.0'?><FICHES>" + "".join(fiches) + "</FICHES>")


class Base(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.d.name, "t.db")
        self.con = v.ouvrir(self.db)
        self.r, self.s = os.path.join(self.d.name, "r.zip"), os.path.join(self.d.name, "s.zip")
        self.excl = os.path.join(self.d.name, "excl.csv")
        self.ecrire_exclusions([])

    def tearDown(self):
        self.con.close(); self.d.cleanup()

    def ecrire_exclusions(self, lignes):
        with open(self.excl, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f, delimiter=";"); w.writerow(["type", "valeur", "libelle", "nature", "fiches_actives", "fiches_total", "statut"]); w.writerows(lignes)

    def imp(self, date, rncp, rs=(), **kw):
        zipxml(self.r, rncp); zipxml(self.s, rs)
        return v.importer(self.con, date, self.r, self.s, exclusions_csv=self.excl, **kw)

    def one(self, sql, *a):
        return self.con.execute(sql, a).fetchone()[0]

    def evts(self):
        return [(e["type"], e["numero_fiche"]) for e in self.con.execute("SELECT * FROM evenement ORDER BY id")]


class TestFiches(Base):
    def test_champs_valides_et_listes(self):
        self.imp("2026-10-14", [fiche("RNCP1", stat=[(2023, 40), (2024, 55)], nouvelles=["RNCP9"], partenaires=3, lien="https://www.ecole.fr/p  autre")])
        r = self.con.execute("SELECT * FROM certification").fetchone()
        self.assertEqual((r["niveau"], r["type_diplome_code"], r["nsf_code"], r["date_fin_enregistrement"], r["nb_partenaires"], r["nb_certifies"], r["annee_stat"], r["taux_insertion_6mois"], r["lien_url"], r["fiche_de_reference"]),
                         ("NIV6", "TP", "221", "2027-10-01", 3, 55, 2024, 90, "https://www.ecole.fr/p", 1))
        self.assertEqual(self.one("SELECT code FROM certification_formacode"), "21519")
        self.assertEqual(self.one("SELECT fiche_liee FROM certification_filiation WHERE sens='nouvelle'"), "RNCP9")

    def test_reference_puis_nouveautes_et_idempotence(self):
        self.imp("2026-10-14", [fiche("RNCP1"), fiche("RNCP2")])
        self.assertEqual(self.evts(), [])
        self.imp("2026-10-15", [fiche("RNCP1"), fiche("RNCP2"), fiche("RNCP3")], [fiche("RS9")])
        self.assertEqual(sorted(self.evts()), [("nouvelle", "RNCP3"), ("nouvelle", "RS9")])
        self.assertEqual(self.one("SELECT fiche_de_reference FROM certification WHERE numero_fiche='RNCP3'"), 0)
        self.assertIsNone(self.imp("2026-10-15", [fiche("RNCP1")]))

    def test_changements_historique_disparition_retour(self):
        self.imp("2026-10-14", [fiche("RNCP1"), fiche("RNCP2"), fiche("RNCP3")])
        self.imp("2026-10-15", [fiche("RNCP1", actif="Non"), fiche("RNCP2", fin="01/10/2028")])
        e = dict((n, t) for t, n in self.evts())
        self.assertEqual((e["RNCP1"], e["RNCP2"], e["RNCP3"]), ("desactivee", "echeance_modifiee", "disparue"))
        h = self.con.execute("SELECT * FROM certification_historique WHERE numero_fiche='RNCP2'").fetchone()
        self.assertEqual((h["champ"], h["ancienne_valeur"], h["nouvelle_valeur"], h["valide_du"], h["valide_au"]), ("date_fin_enregistrement", "2027-10-01", "2028-10-01", "2026-10-14", "2026-10-15"))
        self.imp("2026-10-16", [fiche("RNCP1", actif="Non"), fiche("RNCP2", fin="01/10/2028"), fiche("RNCP3")])
        self.assertIn(("retour", "RNCP3"), self.evts())

    def test_changement_de_certificateurs(self):
        self.imp("2026-10-14", [fiche("RNCP1")])
        self.imp("2026-10-15", [fiche("RNCP1", certs=(("11111111100011", "ECOLE TEST"), ("22222222200022", "AUTRE")))])
        self.assertIn(("modifiee", "RNCP1"), self.evts())
        self.assertEqual(self.one("SELECT champ FROM certification_historique WHERE numero_fiche='RNCP1'"), "certificateurs")
        self.assertEqual(self.one("SELECT nb_certificateurs FROM certification"), 2)


class TestEntreprises(Base):
    def test_siren_siret_nom(self):
        self.imp("2026-10-14", [fiche("RNCP1", certs=(("11111111100011", "ECOLE A"), ("11111111100029", "ECOLE A CAMPUS"), ("", "Commission paritaire X")))])
        self.assertEqual(self.one("SELECT COUNT(*) FROM entreprise"), 2)
        self.assertEqual(self.one("SELECT COUNT(*) FROM etablissement"), 3)
        self.assertEqual(self.one("SELECT siren FROM entreprise WHERE entreprise_cle='111111111'"), "111111111")
        self.assertEqual(self.one("SELECT a_qualifier FROM entreprise WHERE entreprise_cle LIKE 'NOM:%'"), 1)
        self.assertEqual(self.one("SELECT objet_ref FROM file_qualification WHERE type='sans_siret'"), "NOM:COMMISSION PARITAIRE X")
        self.assertEqual(self.one("SELECT nb_certificateurs FROM certification"), 2)       # deux entreprises, pas trois SIRET

    def test_exclusion_seulement_si_validee(self):
        rnc = [fiche("RNCP1", certs=(("11004401300040", "MINISTERE X"),)), fiche("RNCP2", certs=(("22222222200022", "ECOLE B"),))]
        self.ecrire_exclusions([["siren", "110044013", "Ministère X", "ministère", "1", "1", "a_valider"]])
        self.imp("2026-10-14", rnc)
        self.assertEqual(self.one("SELECT COUNT(*) FROM entreprise WHERE exclue=1"), 0)
        self.ecrire_exclusions([["siren", "110044013", "Ministère X", "ministère", "1", "1", "valide"]])
        self.imp("2026-10-15", rnc)
        self.assertEqual(self.one("SELECT exclue FROM entreprise WHERE siren='110044013'"), 1)
        self.assertEqual(sorted(r[0] for r in self.con.execute("SELECT entreprise_cle FROM v_opportunites")), ["222222222"])

    def test_exclusion_par_nom_sans_siret_seulement(self):
        self.ecrire_exclusions([["motif_nom", "minist[eè]re", "Repérage par le nom", "règle de nom", "", "", "valide"]])
        self.imp("2026-10-14", [fiche("RNCP1", certs=(("", "Ministère de la Mer"),)), fiche("RNCP2", certs=(("33333333300033", "Ministère privé SARL"),))])
        self.assertEqual(self.one("SELECT exclue FROM entreprise WHERE entreprise_cle='NOM:MINISTÈRE DE LA MER'"), 1)
        self.assertEqual(self.one("SELECT exclue FROM entreprise WHERE siren='333333333'"), 0)


class TestLeaderEtSite(Base):
    A, B, C = ("11111111100011", "ECOLE A"), ("22222222200022", "ECOLE B"), ("33333333300033", "ECOLE C")

    def roles(self, num):
        return {r["entreprise_cle"]: (r["role"], r["regle_leader"]) for r in self.con.execute(
            "SELECT entreprise_cle, role, regle_leader FROM certification_etablissement WHERE numero_fiche=? AND valide_au IS NULL", (num,))}

    def test_site_web_depuis_fiches_a_un_seul_certificateur(self):
        self.imp("2026-10-14", [fiche("RNCP1", certs=(self.A,), lien="https://www.ecole-a.fr/x"), fiche("RNCP2", certs=(self.A,), lien="https://ecole-a.fr/y"),
                                fiche("RNCP3", certs=(self.A, self.B), lien="https://www.autre.fr/z")])
        w = self.con.execute("SELECT * FROM entreprise_site_web WHERE valide_au IS NULL").fetchall()
        self.assertEqual([(r["entreprise_cle"], r["url_racine"], r["statut"], r["source"]) for r in w], [("111111111", "https://ecole-a.fr", "a_valider", "lien_fiche_unique")])

    def test_site_ambigu_en_file_de_qualification(self):
        self.imp("2026-10-14", [fiche("RNCP1", certs=(self.A,), lien="https://a1.fr"), fiche("RNCP2", certs=(self.A,), lien="https://a2.fr")])
        self.assertEqual(self.one("SELECT COUNT(*) FROM entreprise_site_web"), 0)
        self.assertEqual(self.one("SELECT type FROM file_qualification WHERE objet_ref='111111111'"), "site_ambigu")

    def test_leader_par_domaine_puis_portefeuille_puis_indetermine(self):
        base = [fiche("RNCP1", certs=(self.A,), lien="https://ecole-a.fr/x"), fiche("RNCP2", certs=(self.B,), lien="https://ecole-b.fr/x"),
                fiche("RNCP3", certs=(self.B,), lien="https://ecole-b.fr/y")]
        # domaine : le lien de la fiche co-certifiée pointe vers le site de B
        self.imp("2026-10-14", base + [fiche("RNCP10", certs=(self.A, self.B), lien="https://ecole-b.fr/co")])
        self.assertEqual(self.roles("RNCP10"), {"111111111": ("co", None), "222222222": ("leader", "domaine")})
        # portefeuille : pas de lien, B a 3 fiches actives contre 2 pour A
        self.imp("2026-10-15", base + [fiche("RNCP10", certs=(self.A, self.B), lien="https://ecole-b.fr/co"), fiche("RNCP11", certs=(self.A, self.B))])
        self.assertEqual(self.roles("RNCP11"), {"111111111": ("co", None), "222222222": ("leader", "portefeuille")})
        # ex æquo : C et D à égalité, aucun lien : sans leader
        self.imp("2026-10-16", [fiche("RNCP20", certs=(self.C, ("44444444400044", "ECOLE D")))])
        self.assertEqual(set(self.roles("RNCP20").values()), {("indetermine", None)})

    def test_historique_des_liaisons_quand_le_role_change(self):
        self.imp("2026-10-14", [fiche("RNCP1", certs=(self.A, self.B))])
        self.imp("2026-10-15", [fiche("RNCP1", certs=(self.A, self.B)), fiche("RNCP2", certs=(self.A,)), fiche("RNCP3", certs=(self.A,))])
        lignes = self.con.execute("SELECT entreprise_cle, role, valide_du, valide_au FROM certification_etablissement WHERE numero_fiche='RNCP1' ORDER BY entreprise_cle, valide_du").fetchall()
        self.assertEqual([(r[0], r[1], r[2], r[3]) for r in lignes],
                         [("111111111", "indetermine", "2026-10-14", "2026-10-15"), ("111111111", "leader", "2026-10-15", None),
                          ("222222222", "indetermine", "2026-10-14", "2026-10-15"), ("222222222", "co", "2026-10-15", None)])


class TestVueEtExports(Base):
    def test_vue_opportunites_hors_exclues_une_ligne_par_entreprise(self):
        self.imp("2026-10-14", [fiche("RNCP1", fin="01/01/2099", certs=(("11111111100011", "A SIEGE"), ("11111111100029", "A CAMPUS"))),
                                fiche("RNCP2", actif="Non")])
        lignes = self.con.execute("SELECT entreprise_cle, tranche FROM v_opportunites").fetchall()
        self.assertEqual([(r[0], r[1]) for r in lignes], [("111111111", "24_mois_et_plus")])

    def test_export_echeances(self):
        self.imp("2026-10-14", [fiche("RNCP1", fin="01/01/2099", certs=(("11111111100011", "A"), ("22222222200022", "B")), type_enr="Enregistrement de droit")])
        class A: mois_min, mois_max, sur_demande_seulement, out = 0, 100000, False, os.path.join(self.d.name, "o.csv")
        v.cmd_echeances(self.con, A)
        lignes = list(csv.DictReader(open(A.out, encoding="utf-8-sig"), delimiter=";"))
        self.assertEqual(sorted(l["entreprise"] for l in lignes), ["A", "B"])
        self.assertTrue(all(l["co_certificateurs"] for l in lignes))                      # les « de droit » (universités) restent inclus
        A.sur_demande_seulement = True
        v.cmd_echeances(self.con, A)
        self.assertEqual(list(csv.DictReader(open(A.out, encoding="utf-8-sig"), delimiter=";")), [])


if __name__ == "__main__":
    unittest.main()
