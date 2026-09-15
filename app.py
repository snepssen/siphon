"""The window: a local server, and a page on 127.0.0.1 that talks to it.

The command line and this are two views of one queue. `jobs.Queue` does not
know which of them is driving, and neither view knows anything the other does
not — so a job added in the terminal appears here, and the settings saved here
are the ones the terminal reads.

Three things about it that are deliberate:

**It binds to the loopback address only.** Not a configurable host, not
0.0.0.0. This is a window, not a service, and a media downloader that answers
the local network is a media downloader somebody else is using.

**Every request carries a token** minted at startup and handed to the browser
in the URL that opens it. Loopback alone is not a boundary: any page in any
tab can make requests to 127.0.0.1, and this server writes files. The token,
plus a check on where the request claims to come from, is what stops a web
page you happen to have open from queueing downloads on your machine.

**The page is served from one file.** No build step, no bundler, no CDN. It
opens from a zipapp the same way it opens from a checkout.
"""

import json
import os
import secrets
import subprocess
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

import credentials
import formats
import jobs as jobs_module
import model
import paths
import platform_support
import sources

HOST = "127.0.0.1"
DEFAULT_PORT = 7788
VERSION = "0.1.0"

TOKEN = secrets.token_urlsafe(24)

_queue = None
_listeners = []
_listeners_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Reading the page, from a folder or from inside a zipapp
# ---------------------------------------------------------------------------

def _asset(name):
    """The bytes of a file next to this module, wherever this module lives."""
    here = Path(__file__).resolve().parent
    candidate = here / name
    try:
        return candidate.read_bytes()
    except OSError:
        pass
    # Inside a zipapp there is no file to open; the loader has the bytes.
    loader = globals().get("__loader__")
    if loader is not None and hasattr(loader, "get_data"):
        try:
            return loader.get_data(str(candidate))
        except OSError:
            pass
    raise FileNotFoundError(
        f"siphon could not find {name}. The window needs it and the install "
        f"looks incomplete."
    )


# ---------------------------------------------------------------------------
# Live updates
# ---------------------------------------------------------------------------

def _broadcast(queue):
    """Wake every open page. Slow or dead ones are dropped, not waited for."""
    payload = json.dumps({"jobs": queue.snapshot()})
    with _listeners_lock:
        listeners = list(_listeners)
    for listener in listeners:
        try:
            listener.put(payload)
        except Exception:
            _drop(listener)


def _drop(listener):
    with _listeners_lock:
        if listener in _listeners:
            _listeners.remove(listener)


class _Listener:
    """One open event stream."""

    def __init__(self):
        self.lock = threading.Condition()
        self.pending = None
        self.closed = False

    def put(self, payload):
        with self.lock:
            self.pending = payload
            self.lock.notify()

    def wait(self, timeout):
        with self.lock:
            if self.pending is None:
                self.lock.wait(timeout)
            payload, self.pending = self.pending, None
            return payload


# ---------------------------------------------------------------------------
# The server
# ---------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    server_version = f"siphon/{VERSION}"
    protocol_version = "HTTP/1.1"

    # -- plumbing --------------------------------------------------------

    def log_message(self, *args):
        pass          # the terminal belongs to the user, not to an access log

    def _authorised(self):
        """Token, and a request that did not come from another website.

        The Origin check matters more than it looks: a page on the internet
        cannot read this server's responses, but it can certainly send it
        requests, and a POST that starts a download does not need a readable
        reply to be a problem.
        """
        origin = self.headers.get("Origin")
        if origin and urlparse(origin).hostname not in {HOST, "localhost"}:
            return False
        host = (self.headers.get("Host") or "").split(":")[0]
        if host not in {HOST, "localhost"}:
            return False          # blocks DNS rebinding onto this port
        supplied = self.headers.get("X-Siphon-Token")
        if not supplied:
            supplied = parse_qs(urlparse(self.path).query).get("t", [""])[0]
        return secrets.compare_digest(supplied or "", TOKEN)

    def _send(self, status, body=b"", content_type="application/json",
              extra=None):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        # This page loads nothing from anywhere else, and nothing else should
        # be able to frame it.
        self.send_header("Content-Security-Policy",
                         "default-src 'self' 'unsafe-inline'; frame-ancestors 'none'")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _json(self, payload, status=200):
        self._send(status, json.dumps(payload), "application/json")

    def _fail(self, message, status=400):
        self._json({"error": str(message)}, status=status)

    def _body(self):
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            return {}

    # -- routes ----------------------------------------------------------

    def do_GET(self):
        route = urlparse(self.path).path

        if route in {"/", "/index.html"}:
            if not self._authorised():
                return self._send(
                    403,
                    "<h1>siphon</h1><p>Open the window from the address siphon "
                    "printed when it started. This one is missing its key.</p>",
                    "text/html; charset=utf-8",
                )
            return self._send(200, _asset("index.html"),
                              "text/html; charset=utf-8")

        if not self._authorised():
            return self._fail("Not allowed.", 403)

        if route == "/api/state":
            return self._json(self._state())
        if route == "/api/settings":
            return self._json(credentials.status())
        if route == "/api/events":
            return self._events()
        return self._fail("No such thing here.", 404)

    def do_POST(self):
        if not self._authorised():
            return self._fail("Not allowed.", 403)
        route = urlparse(self.path).path
        body = self._body()

        try:
            if route == "/api/expand":
                return self._json(self._expand(body))
            if route == "/api/add":
                return self._json(self._add(body))
            if route == "/api/cancel":
                return self._json({"ok": _queue.cancel(body.get("id"))})
            if route == "/api/retry":
                return self._json({"ok": _queue.retry(body.get("id"))})
            if route == "/api/choose":
                return self._json({"ok": _queue.choose(body.get("id"),
                                                       body.get("url"))})
            if route == "/api/skip":
                return self._json({"ok": _queue.skip(body.get("id"))})
            if route == "/api/forget":
                _queue.forget_finished()
                return self._json({"ok": True})
            if route == "/api/settings":
                return self._json(self._settings(body))
            if route == "/api/reveal":
                return self._json({"ok": _reveal(body.get("path"))})
            if route == "/api/quit":
                threading.Timer(0.3, lambda: os._exit(0)).start()
                return self._json({"ok": True})
        except (sources.NoSourceFor, sources.SourceError,
                formats.UnknownFormat) as error:
            return self._fail(error, 400)
        except Exception as error:                      # noqa: BLE001
            return self._fail(error, 500)

        return self._fail("No such thing here.", 404)

    # -- what the routes do ----------------------------------------------

    def _state(self):
        return {
            "version": VERSION,
            "jobs": _queue.snapshot(),
            "formats": [
                {"name": name, "summary": target.summary, "kind": target.kind}
                for name, target in formats.PRESETS.items()
            ],
            "default_format": formats.DEFAULT_PRESET,
            "programs": platform_support.survey(),
            "output": str(paths.output_dir()),
        }

    def _expand(self, body):
        """Look at a link without committing to it.

        Worth its own route: a playlist is forty decisions, and showing them
        before any of them start is the difference between a tool and an
        accident.
        """
        target = (body.get("url") or "").strip()
        if not target:
            return {"items": []}
        items = sources.expand(target, limit=body.get("limit"))
        for item in items:
            item.extra["collection_size"] = len(items)
        return {
            "items": [item.to_dict() for item in items],
            "collection": items[0].collection if items else None,
            "count": len(items),
            # A source that could only see part of the list says so here, and
            # the page shows it before anything is queued.
            "notice": items[0].extra.get("truncated") if items else None,
        }

    def _add(self, body):
        """Queue a link, or queue the items a preview already turned up.

        Taking items back is what stops the page expanding a playlist twice —
        once to show you what is in it, and again to fetch it. The second trip
        would be two seconds of waiting and a second round of requests at the
        site, for an answer nobody doubted.
        """
        fmt = body.get("format") or formats.DEFAULT_PRESET
        output = body.get("output") or None

        supplied = body.get("items")
        if supplied:
            items = [model.Item.from_dict(data) for data in supplied]
            batch = items[0].collection if len(items) > 1 else None
            created = _queue.add(items, fmt, output=output, batch=batch)
        else:
            target = (body.get("url") or "").strip()
            if not target:
                raise ValueError("Nothing to add.")
            created = _queue.add_url(target, fmt, output=output)
        return {"added": len(created), "jobs": [j.to_dict() for j in created]}

    def _settings(self, body):
        service = body.get("service")
        field = body.get("field")
        if not service or not field:
            raise ValueError("Which key?")
        credentials.set(service, field, body.get("value") or "")
        return credentials.status()

    def _events(self):
        """Server-sent events: one message per queue change.

        A heartbeat goes out every fifteen seconds whether anything happened
        or not, because a silent stream and a dead stream look identical from
        the other end.
        """
        listener = _Listener()
        with _listeners_lock:
            _listeners.append(listener)

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()

        try:
            self.wfile.write(
                f"data: {json.dumps({'jobs': _queue.snapshot()})}\n\n".encode()
            )
            self.wfile.flush()
            while True:
                payload = listener.wait(timeout=15)
                if payload is None:
                    self.wfile.write(b": still here\n\n")
                else:
                    self.wfile.write(f"data: {payload}\n\n".encode())
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass          # the tab was closed; that is not an error
        finally:
            _drop(listener)


def _reveal(path):
    """Show a finished file in the file manager."""
    if not path or not os.path.exists(path):
        return False
    try:
        if sys.platform == "darwin":
            subprocess.run(["open", "-R", path], check=False, timeout=10)
        elif sys.platform.startswith("win"):
            subprocess.run(["explorer", "/select,", path], check=False, timeout=10)
        else:
            subprocess.run(["xdg-open", os.path.dirname(path)], check=False,
                           timeout=10)
        return True
    except (OSError, subprocess.SubprocessError):
        return False


# ---------------------------------------------------------------------------
# Starting
# ---------------------------------------------------------------------------

def serve(port=DEFAULT_PORT, open_browser=True, workers=2):
    global _queue

    missing = platform_support.missing_required()
    for sentence in missing:
        print(sentence, file=sys.stderr)

    _queue = jobs_module.Queue(workers=workers, on_change=_broadcast)
    restored = _queue.load()
    _queue.start()

    server = None
    for candidate in [port, 0]:
        try:
            server = ThreadingHTTPServer((HOST, candidate), Handler)
            break
        except OSError:
            if candidate == 0:
                raise
            print(f"Port {candidate} is busy; picking another.")
    server.daemon_threads = True

    actual = server.server_address[1]
    url = f"http://{HOST}:{actual}/?t={TOKEN}"

    # flush=True throughout: print block-buffers whenever stdout is not a
    # terminal, and the address is the one line somebody actually needs. A
    # launcher that pipes the output would otherwise show nothing at all until
    # the server stopped.
    print(f"siphon {VERSION}", flush=True)
    if restored:
        print(f"{restored} job{'s' if restored != 1 else ''} restored from last time.",
              flush=True)
    print(f"\n  {url}\n", flush=True)
    print("Leave this running while the window is open. Ctrl-C closes both.",
          flush=True)

    if open_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nClosing.")
    finally:
        _queue.stop(wait=False)
        server.shutdown()
    return 0


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    port = DEFAULT_PORT
    open_browser = True
    if "--no-open" in argv:
        argv.remove("--no-open")
        open_browser = False
    if "--port" in argv:
        index = argv.index("--port")
        port = int(argv[index + 1])
    return serve(port=port, open_browser=open_browser)


if __name__ == "__main__":
    sys.exit(main())
