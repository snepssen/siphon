"""The window's front door.

`app._authorised` is the only thing standing between a program that writes
files to somebody's disk and every other page they have open. Loopback is not
a boundary: any site in any tab can POST to 127.0.0.1, and a request that
starts a download does not need a readable reply to be a problem. So the
server wants a token minted at startup, and it checks where the request claims
to have come from.

These run against a real server on a real socket rather than against a stubbed
handler. A security boundary tested through a mock is a boundary tested
against one's own assumptions about the mock, and the interesting failures
here — a header the handler never reads, a route registered before the check —
are exactly the ones a mock would hide.
"""

import json
import sys
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app                       # noqa: E402
import jobs as jobs_module       # noqa: E402


class Server:
    """app's own handler, on a real port."""

    def __init__(self):
        app._queue = jobs_module.Queue(workers=1)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
        self.server.daemon_threads = True
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def stop(self):
        self.server.shutdown()
        self.server.server_close()

    def request(self, path, token=None, in_query=False, origin=None,
                host=None, method="GET", body=None):
        url = f"http://127.0.0.1:{self.port}{path}"
        if in_query and token:
            url += ("&" if "?" in url else "?") + f"t={token}"
        request = urllib.request.Request(url, method=method, data=body)
        if token and not in_query:
            request.add_header("X-Siphon-Token", token)
        if origin:
            request.add_header("Origin", origin)
        if host:
            request.add_header("Host", host)
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as error:
            return error.code, error.read()


class TheDoorIsShut(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = Server()
        cls.token = app.TOKEN

    @classmethod
    def tearDownClass(cls):
        cls.server.stop()
        app._queue.stop(wait=False)

    # -- the token ------------------------------------------------------

    def test_no_token_is_refused(self):
        status, _ = self.server.request("/api/state")
        self.assertEqual(status, 403)

    def test_a_wrong_token_is_refused(self):
        status, _ = self.server.request("/api/state", token="not-the-token")
        self.assertEqual(status, 403)

    def test_a_token_that_is_a_prefix_is_refused(self):
        """compare_digest, not startswith."""
        status, _ = self.server.request("/api/state", token=self.token[:-1])
        self.assertEqual(status, 403)

    def test_the_right_token_is_let_in(self):
        status, body = self.server.request("/api/state", token=self.token)
        self.assertEqual(status, 200)
        self.assertIn("formats", json.loads(body))

    def test_the_token_may_come_in_the_query(self):
        """Which is how the page itself is first opened."""
        status, _ = self.server.request("/api/state", token=self.token,
                                        in_query=True)
        self.assertEqual(status, 200)

    # -- where the request came from ------------------------------------

    def test_another_site_is_refused_even_with_the_token(self):
        """A page cannot read this server's replies, but it can send it
        requests, and a download that starts does not need a readable reply
        to be a problem."""
        for origin in ("https://evil.example", "http://attacker.test",
                       "https://www.youtube.com"):
            with self.subTest(origin=origin):
                status, _ = self.server.request(
                    "/api/state", token=self.token, origin=origin)
                self.assertEqual(status, 403, origin)

    def test_the_pages_own_origin_is_allowed(self):
        for origin in (f"http://127.0.0.1:{self.server.port}",
                       f"http://localhost:{self.server.port}"):
            with self.subTest(origin=origin):
                status, _ = self.server.request(
                    "/api/state", token=self.token, origin=origin)
                self.assertEqual(status, 200, origin)

    def test_a_rebound_hostname_is_refused(self):
        """DNS rebinding: a name that resolves to 127.0.0.1 but is not it."""
        status, _ = self.server.request("/api/state", token=self.token,
                                        host="siphon.attacker.test")
        self.assertEqual(status, 403)

    # -- the routes that do things --------------------------------------

    def test_every_route_that_acts_is_behind_the_check(self):
        acting = ["/api/add", "/api/cancel", "/api/retry", "/api/forget",
                  "/api/choose", "/api/skip", "/api/settings", "/api/reveal",
                  "/api/install", "/api/cobalt", "/api/quit"]
        for route in acting:
            with self.subTest(route=route):
                status, _ = self.server.request(route, method="POST",
                                                body=b"{}")
                self.assertEqual(status, 403, f"{route} answered unauthenticated")

    def test_reading_routes_are_behind_it_too(self):
        for route in ("/api/state", "/api/settings", "/api/cobalt",
                      "/api/events"):
            with self.subTest(route=route):
                status, _ = self.server.request(route)
                self.assertEqual(status, 403, route)

    def test_the_page_itself_explains_rather_than_serving_the_app(self):
        status, body = self.server.request("/")
        self.assertEqual(status, 403)
        text = body.decode("utf-8", "replace")
        self.assertIn("siphon", text)
        self.assertNotIn("<script>", text, "the app was served without a token")

    def test_an_unknown_route_is_not_a_way_in(self):
        status, _ = self.server.request("/../app.py", token=self.token)
        self.assertIn(status, (400, 403, 404))

    # -- what it tells the browser --------------------------------------

    def test_the_replies_carry_the_headers_that_matter(self):
        url = f"http://127.0.0.1:{self.server.port}/api/state"
        request = urllib.request.Request(url)
        request.add_header("X-Siphon-Token", self.token)
        with urllib.request.urlopen(request, timeout=10) as response:
            headers = {k.lower(): v for k, v in response.headers.items()}
        self.assertIn("frame-ancestors 'none'", headers.get("content-security-policy", ""))
        self.assertEqual(headers.get("x-content-type-options"), "nosniff")
        self.assertEqual(headers.get("referrer-policy"), "no-referrer")


class TheTokenItself(unittest.TestCase):
    def test_it_is_long_enough_to_be_worth_having(self):
        self.assertGreaterEqual(len(app.TOKEN), 32)

    def test_it_is_not_a_fixed_string(self):
        """Minted per run: a constant would be a password everyone knows."""
        import importlib
        first = app.TOKEN
        reloaded = importlib.reload(app)
        self.assertNotEqual(first, reloaded.TOKEN)
        # Put the module back as the rest of the suite found it.
        importlib.reload(app)

    def test_the_server_binds_loopback_only(self):
        self.assertEqual(app.HOST, "127.0.0.1")


if __name__ == "__main__":
    unittest.main(verbosity=2)
