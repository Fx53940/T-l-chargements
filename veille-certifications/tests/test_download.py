import os, sys, tempfile, threading, unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import veille_rncp_rs as v

DATA = os.urandom(3_000_000)


class H(BaseHTTPRequestHandler):
    coupures = 0

    def log_message(self, *a):
        pass

    def do_GET(self):
        debut = 0
        rng = self.headers.get("Range")
        if rng:
            debut = int(rng.split("=")[1].split("-")[0])
        self.send_response(206 if rng else 200)
        self.send_header("Content-Length", str(len(DATA) - debut))
        self.end_headers()
        if H.coupures < 2:                       # coupe 2 fois en plein transfert
            H.coupures += 1
            self.wfile.write(DATA[debut:debut + 700_000])
            self.wfile.flush()
            self.connection.close()
            return
        self.wfile.write(DATA[debut:])


class T(unittest.TestCase):
    def test_reprise(self):
        srv = HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        with tempfile.TemporaryDirectory() as d:
            dest = os.path.join(d, "f.bin")
            v.time.sleep = lambda s: None
            v.http_get(f"http://127.0.0.1:{srv.server_port}/f", dest)
            self.assertEqual(open(dest, "rb").read(), DATA)
        srv.shutdown()


if __name__ == "__main__":
    unittest.main()
