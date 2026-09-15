"""YouTube's proof-of-not-a-robot, generated locally.

A self-hosted cobalt returns an empty file for a YouTube link, and it is not
broken. YouTube wants a `poToken` — a value produced by running its own
obfuscated BotGuard code — and cobalt does not make one itself. It POSTs to
`/get_pot` on whatever `YOUTUBE_SESSION_SERVER` names, and with nothing there
the tunnel streams zero bytes.

**`/get_pot` is bgutil's endpoint.** cobalt's own documentation points at
imputnet's `yt-session-generator`, which serves `/token` instead, and whose
extraction no longer works: it drives a real Chrome, clicks the embedded
player and waits for a POST to `/youtubei/v1/player` carrying the token, and
YouTube's embed no longer makes that request. Reading cobalt's
`validateSession` settles it — it accepts `poToken` and `contentBinding`,
which are bgutil's field names, not that generator's. So this runs
[bgutil](https://github.com/Brainicism/bgutil-ytdlp-pot-provider), which is
also the provider yt-dlp's own plugin uses.

The better news is that bgutil needs no browser at all: it runs BotGuard's VM
under jsdom, in Node, which is already here for cobalt.

**cobalt and bgutil do not quite agree, so siphon sits between them.** cobalt
POSTs to `/get_pot` with no body and no headers at all; bgutil requires
`Content-Type: application/json` and answers 415 without it, which cobalt
reports as "no poToken in session response". Neither is wrong — they were
built against different providers — so `serve_bridge` below is a few lines of
`http.server` that accepts cobalt's bare POST, asks bgutil properly, and hands
the answer back unchanged. It is the whole of the incompatibility, and it is
worth knowing that is all it is.

**It has to be compiled first.** The server is TypeScript using parameter
properties, which Node's type-stripping cannot handle — `node src/main.ts`
fails with ERR_UNSUPPORTED_TYPESCRIPT_SYNTAX — and the repository ships a
`tsconfig.json` but no build script. So the install runs `tsc` and starts
`build/main.js`.
"""

import json
import os
import shutil
import signal
import subprocess
import time
import urllib.error
import urllib.request

import sys

import paths
from platform_support import locate

REPO = "https://github.com/Brainicism/bgutil-ytdlp-pot-provider.git"
DEFAULT_PORT = 4416
BRIDGE_PORT = 4417
HOST = "127.0.0.1"


def home():
    return paths.state_dir() / "pot-provider"


def server_dir():
    return home() / "server"


def _pidfile():
    return paths.state_dir() / "pot-provider.pid"


def _bridge_pidfile():
    return paths.state_dir() / "pot-bridge.pid"


def installed():
    return (server_dir() / "build" / "main.js").is_file()


def requirements():
    absent = []
    for name in ("git", "node", "npm"):
        if locate(name) is None:
            absent.append(name)
    return absent


# ---------------------------------------------------------------------------
# Getting it
# ---------------------------------------------------------------------------

def install(on_line=None, update=False):
    absent = requirements()
    if absent:
        return False, (
            f"The token provider needs {' and '.join(absent)}. "
            f"`siphon setup` will offer to get {'it' if len(absent) == 1 else 'them'}."
        )

    def say(line):
        if on_line:
            on_line(line)

    target = home()
    npm = locate("npm") or "npm"

    if (target / ".git").is_dir():
        if update:
            say("$ git pull")
            done = _run(["git", "-C", str(target), "pull", "--ff-only"], 600)
            if done.returncode != 0:
                return False, _tail(done) or "could not update the provider"
        else:
            say("the token provider is already here.")
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        say(f"$ git clone {REPO}")
        done = _run(["git", "clone", "--depth", "1", REPO, str(target)], 900)
        if done.returncode != 0:
            return False, _tail(done) or "could not fetch the provider"

    if not (server_dir() / "package.json").is_file():
        return False, ("bgutil's layout has changed — there is no "
                       "server/package.json where siphon expects one.")

    # The dev dependencies are needed, not optional: typescript is one of
    # them and nothing can be built without it.
    say("$ npm install")
    done = _run([npm, "install", "--no-audit", "--no-fund"], 1800,
                cwd=str(server_dir()))
    if done.returncode != 0:
        return False, _tail(done) or "npm install failed"

    say("$ npx tsc")
    done = _run(["npx", "--yes", "tsc"], 900, cwd=str(server_dir()))
    if not (server_dir() / "build" / "main.js").is_file():
        return False, _tail(done) or "the TypeScript build produced nothing"

    return True, "The token provider is installed."


def remove():
    stop()
    shutil.rmtree(home(), ignore_errors=True)
    return True


# ---------------------------------------------------------------------------
# Running it
# ---------------------------------------------------------------------------

def url(port=DEFAULT_PORT):
    return f"http://{HOST}:{port}"


def running(port=DEFAULT_PORT, timeout=5):
    """Whether it answers. Minting a token is a separate, slower question."""
    try:
        request = urllib.request.Request(
            url(port) + "/ping", method="GET",
            headers={"Accept": "application/json"})
        with urllib.request.urlopen(request, timeout=timeout):
            return True
    except urllib.error.HTTPError:
        return True                 # answering, even if it dislikes the path
    except (urllib.error.URLError, OSError, TimeoutError):
        return False


def token(port=DEFAULT_PORT, timeout=120):
    """Ask for a token. The first one takes a while; later ones are cached."""
    try:
        request = urllib.request.Request(
            url(port) + "/get_pot", method="POST", data=b"{}",
            headers={"Content-Type": "application/json",
                     "Accept": "application/json"})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read() or b"{}")
    except (urllib.error.URLError, OSError, ValueError, TimeoutError):
        return None


def start(port=DEFAULT_PORT, wait=150, on_line=None):
    """Launch it and wait until it has actually minted a token."""
    if running(port) and token(port, timeout=30):
        return True, f"Already running on {url(port)}."
    if not installed():
        return False, ("The token provider is not installed. Run "
                       "`siphon cobalt tokens install`.")

    node = locate("node") or "node"
    log = paths.state_dir() / "pot-provider.log"
    handle = open(log, "ab", buffering=0)
    try:
        process = subprocess.Popen(
            [node, "build/main.js", "--port", str(port), "--host", HOST],
            cwd=str(server_dir()), stdout=handle, stderr=handle,
            stdin=subprocess.DEVNULL, start_new_session=True,
        )
    except OSError as error:
        handle.close()
        return False, f"Could not start the token provider: {error}"

    _pidfile().write_text(str(process.pid))
    if on_line:
        on_line("Minting the first token — this takes a few seconds.")

    deadline = time.time() + wait
    while time.time() < deadline:
        if process.poll() is not None:
            return False, _log_tail(log) or "the provider exited immediately."
        if running(port, timeout=3):
            got = token(port, timeout=60)
            if got and (got.get("poToken") or got.get("potoken")):
                return True, f"Minting tokens on {url(port)}"
        time.sleep(2)

    return False, f"No token after {wait}s. Its output is in {log}"


def stop():
    """Stop both the provider and the bridge in front of it."""
    stopped = _stop(_pidfile())
    return _stop(_bridge_pidfile()) or stopped


def _stop(pidfile):
    try:
        pid = int(pidfile.read_text().strip())
    except (OSError, ValueError):
        return False
    try:
        os.killpg(os.getpgid(pid), signal.SIGTERM)
    except (OSError, ProcessLookupError):
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pidfile.unlink(missing_ok=True)
            return False
    pidfile.unlink(missing_ok=True)
    return True


def status(port=DEFAULT_PORT):
    return {
        "installed": installed(),
        "running": running(port),
        "bridged": bridge_running(),
        "url": url(port),
        "session_server": bridge_url(),
        "path": str(home()),
        "missing": requirements(),
    }


def ensure(on_line=None):
    """Provider and bridge, installed and up. Returns (ok, session server)."""
    if not installed():
        ok, detail = install(on_line=on_line)
        if not ok:
            return False, detail
    if not running():
        ok, detail = start(on_line=on_line)
        if not ok:
            return False, detail
    ok, detail = start_bridge(on_line=on_line)
    return (True, bridge_url()) if ok else (False, detail)


# ---------------------------------------------------------------------------
# yt-dlp
# ---------------------------------------------------------------------------

# bgutil ships a yt-dlp plugin alongside the server, so the same provider can
# serve both downloaders. yt-dlp needs no bridge: its plugin asks properly.
#
# Worth having even though yt-dlp fetches YouTube fine today. YouTube demands
# a token on some videos and not others, and the day it starts asking for all
# of them, this is already here.

_PLUGIN_ARG = "youtubepot-bgutilhttp:base_url"
_availability = {"checked": 0.0, "ok": False}
_AVAILABILITY_TTL = 30.0


def plugin_dir():
    """A directory holding just the plugin, for `--plugin-dirs`.

    `--plugin-dirs DIR` iterates DIR's *children* and looks in each for a
    `yt_dlp_plugins` package — so the argument is the parent of the plugin,
    not the plugin. Passing the package itself gets "Plugin directories: none"
    and no explanation. A directory of our own holding one symlink says what
    is meant and keeps yt-dlp from stepping through bgutil's node_modules.
    """
    directory = paths.state_dir() / "yt-dlp-plugins"
    link = directory / "bgutil"
    source = home() / "plugin"
    if not source.is_dir():
        return None
    directory.mkdir(parents=True, exist_ok=True)
    try:
        if link.is_symlink() and link.resolve() != source.resolve():
            link.unlink()
        if not link.exists():
            link.symlink_to(source, target_is_directory=True)
    except OSError:
        return None
    return directory


def available(port=DEFAULT_PORT):
    """Whether the server is up, asked at most twice a minute.

    Cached because this is consulted on every yt-dlp invocation, and a
    playlist is one invocation per track.
    """
    now = time.time()
    if now - _availability["checked"] < _AVAILABILITY_TTL:
        return _availability["ok"]
    _availability["ok"] = installed() and running(port)
    _availability["checked"] = now
    return _availability["ok"]


def ytdlp_args(port=DEFAULT_PORT):
    """Arguments that let yt-dlp use this provider, or nothing at all.

    Nothing when the server is down, so a yt-dlp run never waits on a service
    that is not there — the tokens are an improvement, not a dependency.
    """
    if not available(port):
        return []
    directory = plugin_dir()
    if directory is None:
        return []
    return ["--plugin-dirs", str(directory),
            "--extractor-args", f"{_PLUGIN_ARG}={url(port)}"]


# ---------------------------------------------------------------------------
# The bridge
# ---------------------------------------------------------------------------

def bridge_url(port=BRIDGE_PORT):
    return f"http://{HOST}:{port}"


def bridge_running(port=BRIDGE_PORT, timeout=4):
    try:
        request = urllib.request.Request(
            bridge_url(port) + "/health", method="GET")
        with urllib.request.urlopen(request, timeout=timeout):
            return True
    except urllib.error.HTTPError:
        return True
    except (urllib.error.URLError, OSError, TimeoutError):
        return False


def serve_bridge(port=BRIDGE_PORT, upstream=DEFAULT_PORT):
    """Answer cobalt's bare POST by asking bgutil properly. Blocks.

    Run as its own process by `start_bridge`. Loopback only — it exists to
    join two things on this machine and has no business being reachable.
    """
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):
            pass

        def _reply(self, status, payload):
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            self._reply(200, {"ok": True, "upstream": url(upstream)})

        def do_POST(self):
            length = int(self.headers.get("Content-Length") or 0)
            if length:
                self.rfile.read(length)     # cobalt sends none; drain any
            got = token(upstream, timeout=120)
            if not got or not (got.get("poToken") or got.get("potoken")):
                return self._reply(502, {"error": "no token from bgutil"})
            # Passed through as bgutil wrote it. cobalt maps poToken and
            # contentBinding onto its own names itself, and renaming them
            # here would be one more thing to keep in step.
            self._reply(200, got)

    server = ThreadingHTTPServer((HOST, port), Handler)
    server.daemon_threads = True
    server.serve_forever()


def start_bridge(port=BRIDGE_PORT, on_line=None):
    if bridge_running(port):
        return True, f"Already running on {bridge_url(port)}."
    log = paths.state_dir() / "pot-bridge.log"
    handle = open(log, "ab", buffering=0)
    try:
        process = subprocess.Popen(
            [sys.executable, os.path.abspath(__file__), "--bridge",
             "--port", str(port)],
            cwd=os.path.dirname(os.path.abspath(__file__)),
            stdout=handle, stderr=handle, stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError as error:
        handle.close()
        return False, f"Could not start the bridge: {error}"

    _bridge_pidfile().write_text(str(process.pid))
    deadline = time.time() + 15
    while time.time() < deadline:
        if process.poll() is not None:
            return False, _log_tail(log) or "the bridge exited immediately."
        if bridge_running(port, timeout=2):
            return True, bridge_url(port)
        time.sleep(0.4)
    return False, "the bridge did not come up."


def stop_bridge():
    return _stop(_bridge_pidfile())


# ---------------------------------------------------------------------------

def _run(argv, timeout, cwd=None):
    try:
        return subprocess.run(argv, capture_output=True, text=True,
                              timeout=timeout, cwd=cwd)
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(argv, 1, "", "timed out")
    except OSError as error:
        return subprocess.CompletedProcess(argv, 1, "", str(error))


def _tail(done):
    text = (done.stderr or done.stdout or "").strip().splitlines()
    return text[-1].strip() if text else ""


def _log_tail(path, lines=6):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            tail = handle.read().strip().splitlines()[-lines:]
    except OSError:
        return ""
    return " / ".join(line.strip() for line in tail if line.strip())


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--bridge" in argv:
        port = BRIDGE_PORT
        if "--port" in argv:
            port = int(argv[argv.index("--port") + 1])
        serve_bridge(port=port)
        return 0
    print(json.dumps(status(), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
