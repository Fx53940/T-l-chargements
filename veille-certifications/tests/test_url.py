import json, os, sys, tempfile, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import veille_rncp_rs as v


class T(unittest.TestCase):
    def test_url_autorisee(self):
        for ok in ("https://static.data.gouv.fr/resources/x/y.zip", "https://data.gouv.fr/a", "https://www.data.gouv.fr/api/1/"):
            self.assertTrue(v.url_autorisee(ok), ok)
        for ko in ("http://static.data.gouv.fr/x.zip", "https://evil.com/data.gouv.fr", "https://data.gouv.fr.evil.com/x",
                   "https://xdata.gouv.fr/x", "ftp://static.data.gouv.fr/x", "https://user@evil.com@data.gouv.fr/", "", None):
            self.assertFalse(v.url_autorisee(ko), ko)

    def _liste(self, ressources):
        def faux(url, dest, **kw):
            json.dump({"resources": ressources}, open(dest, "w", encoding="utf-8"))
        ancien, v.http_get = v.http_get, faux
        try:
            return v.trouver_exports()
        finally:
            v.http_get = ancien

    def test_trouver_exports_ignore_les_adresses_refusees(self):
        ok = "https://static.data.gouv.fr/resources/d/"
        res = [{"title": "export-fiches-rncp-v4-1-2026-10-16.zip", "url": "https://evil.com/rncp.zip"},
               {"title": "export-fiches-rs-v4-1-2026-10-16.zip", "url": ok + "rs16.zip"},
               {"title": "export-fiches-rncp-v4-1-2026-10-15.zip", "url": ok + "rncp15.zip"},
               {"title": "export-fiches-rs-v4-1-2026-10-15.zip", "url": ok + "rs15.zip"}]
        self.assertEqual(self._liste(res), ("2026-10-15", ok + "rncp15.zip", ok + "rs15.zip"))

    def test_erreur_explicite_si_tout_est_refuse(self):
        res = [{"title": "export-fiches-rncp-v4-1-2026-10-16.zip", "url": "http://static.data.gouv.fr/a.zip"},
               {"title": "export-fiches-rs-v4-1-2026-10-16.zip", "url": "http://static.data.gouv.fr/b.zip"}]
        with self.assertRaisesRegex(RuntimeError, "2 adresse"):
            self._liste(res)


if __name__ == "__main__":
    unittest.main()
