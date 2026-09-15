"""The token provider, and the bridge that joins it to cobalt.

Nothing here starts a service. What is tested is the join: siphon sits between
two programs that disagree about how to ask a question, and that disagreement
is a fact worth pinning down, because it is invisible from either side.
cobalt POSTs `/get_pot` with no body and no headers; bgutil answers 415 without
`Content-Type: application/json`, and cobalt reports that as "no poToken in
session response" — a sentence that points at the token rather than at the
request.
"""

import json
import sys
import threading
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pot_provider as pot           # noqa: E402

SAMPLE = {"poToken": "M" * 200, "contentBinding": "Cgt" + "x" * 40,
          "expiresAt": "2026-09-16T00:00:00.000Z"}


class FakeBgutil:
    """bgutil as it actually behaves, including the 415."""

    def __init__(self):
        self.calls = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                if length:
                    self.rfile.read(length)
                outer.calls.append(self.headers.get("Content-Type"))
                if self.headers.get("Content-Type") != "application/json":
                    body = json.dumps(
                        {"error": "Content-Type must be application/json"}
                    ).encode()
                    self.send_response(415)
                else:
                    body = json.dumps(SAMPLE).encode()
                    self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       daemon=True)
        self.thread.start()

    def stop(self):
        self.server.shutdown()
        self.server.server_close()


class TheBridge(unittest.TestCase):
    def setUp(self):
        self.upstream = FakeBgutil()
        self.bridge = ThreadingHTTPServer(("127.0.0.1", 0), _bridge_handler(
            self.upstream.port))
        self.bridge.daemon_threads = True
        self.port = self.bridge.server_address[1]
        threading.Thread(target=self.bridge.serve_forever, daemon=True).start()

    def tearDown(self):
        self.bridge.shutdown()
        self.bridge.server_close()
        self.upstream.stop()

    def _post(self, body=None, headers=None):
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/get_pot", method="POST", data=body)
        for key, value in (headers or {}).items():
            request.add_header(key, value)
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read() or b"{}")

    def test_a_bare_post_is_answered(self):
        """Exactly what cobalt sends: no body, no content type."""
        status, payload = self._post()
        self.assertEqual(status, 200)
        self.assertEqual(payload["poToken"], SAMPLE["poToken"])

    def test_the_upstream_was_asked_properly(self):
        self._post()
        self.assertEqual(self.upstream.calls, ["application/json"],
                         "bgutil must be asked with a JSON content type")

    def test_the_field_names_reach_cobalt_unchanged(self):
        """cobalt maps poToken and contentBinding itself; renaming would
        be one more thing to keep in step with two moving projects."""
        _, payload = self._post()
        self.assertIn("poToken", payload)
        self.assertIn("contentBinding", payload)

    def test_the_answer_satisfies_cobalts_validator(self):
        """A transcription of cobalt's validateSession, so the shape is checked
        against what actually consumes it rather than against itself."""
        _, payload = self._post()
        visitor_data = payload.get("visitor_data") or payload.get("contentBinding")
        potoken = payload.get("potoken") or payload.get("poToken")
        self.assertTrue(potoken, "no poToken in session response")
        self.assertTrue(visitor_data, "no visitor_data in session response")
        self.assertGreaterEqual(len(potoken), 160,
                                "cobalt warns below 160 characters")

    def test_a_body_from_a_curious_caller_is_drained_not_forwarded(self):
        status, _ = self._post(b'{"hello": true}',
                               {"Content-Type": "application/json"})
        self.assertEqual(status, 200)


class WhereThingsLive(unittest.TestCase):
    def test_both_ports_are_loopback_only(self):
        self.assertTrue(pot.url().startswith("http://127.0.0.1"))
        self.assertTrue(pot.bridge_url().startswith("http://127.0.0.1"))

    def test_the_checkout_is_siphons_own(self):
        import paths
        self.assertTrue(str(pot.home()).startswith(str(paths.state_dir())))

    def test_it_needs_no_browser(self):
        """The reason bgutil was chosen over the browser-driven generator."""
        self.assertNotIn("chrome", pot.requirements())
        self.assertEqual(set(pot.requirements()) - {"git", "node", "npm"}, set())


def _bridge_handler(upstream_port):
    """The bridge's handler, pointed at a test upstream."""
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass

        def do_POST(self):
            length = int(self.headers.get("Content-Length") or 0)
            if length:
                self.rfile.read(length)
            got = pot.token(upstream_port, timeout=10)
            body = json.dumps(got or {"error": "no token"}).encode()
            self.send_response(200 if got else 502)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return Handler


if __name__ == "__main__":
    unittest.main(verbosity=2)
