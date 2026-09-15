"""Running cobalt yourself, without Docker.

cobalt's own guide reaches for Docker, and Docker is a heavy thing to require
of somebody who wanted to download a video. The API is a plain Node program —
nineteen dependencies and `node src/cobalt` — so siphon fetches the source,
installs it, and runs it, the same way it offers to install ffmpeg. One less
daemon on the machine and one less thing to remember to start.

**It does not necessarily run on your Node.** cobalt depends on
`isolated-vm`, a native module compiled against V8's internals, which lags new
Node majors by months — it will not build against Node 26 at all. So siphon
looks for an LTS Node beside the current one and uses that just for cobalt,
without touching what `node` means anywhere else. On Homebrew `node@22` is
keg-only, which is exactly the right shape for this: installed, available by
path, invisible to everything that did not ask for it.

Three more decisions worth knowing:

**It listens on the loopback address only.** cobalt defaults to `0.0.0.0`,
which is right for the thing it usually is — a service other people use — and
wrong for the thing it is here, which is a helper for one person on one
machine. `API_LISTEN_ADDRESS` is pinned to 127.0.0.1 and nothing on the
network can reach it.

**The checkout is siphon's, not yours.** It lives in siphon's state directory
rather than anywhere somebody might be working, so `remove()` can delete it
without having to wonder what else is in there.

**Updating is a fetch, not a reinstall.** cobalt moves quickly and the whole
point of having it is that it fails differently from yt-dlp — a copy pinned
at whatever version was current on the day it was installed is worth much
less.
"""

import json
import os
import shutil
import signal
import subprocess
import time
import urllib.error
import urllib.request

import paths
from platform_support import find, locate

REPO = "https://github.com/imputnet/cobalt.git"
DEFAULT_PORT = 9000
HOST = "127.0.0.1"

# Node versions cobalt's native dependency is known to build against, newest
# first, at the paths Homebrew puts keg-only formulae. The current `node` is
# tried last: it is the most likely to be too new.
NODE_KEGS = (
    "/opt/homebrew/opt/node@22/bin",
    "/usr/local/opt/node@22/bin",
    "/opt/homebrew/opt/node@20/bin",
    "/usr/local/opt/node@20/bin",
)


def node_bin():
    """The directory holding a Node that cobalt can use, or None.

    `SIPHON_COBALT_NODE` overrides it, for anyone managing Node themselves.
    """
    override = os.environ.get("SIPHON_COBALT_NODE")
    if override and os.path.isfile(os.path.join(override, "node")):
        return override
    for directory in NODE_KEGS:
        if os.path.isfile(os.path.join(directory, "node")):
            return directory
    found = locate("node")
    return os.path.dirname(found) if found else None


def _environment(extra=None):
    """A PATH with cobalt's Node in front, so npm/pnpm/node-gyp all agree.

    They must agree. A native module compiled by one Node and loaded by
    another fails with "No native build was found", which reads like a missing
    download rather than a version mismatch.
    """
    env = dict(os.environ)
    directory = node_bin()
    if directory:
        env["PATH"] = directory + os.pathsep + env.get("PATH", "")
    env.update(extra or {})
    return env


def node_version(directory=None):
    directory = directory or node_bin()
    if not directory:
        return None
    try:
        out = subprocess.run([os.path.join(directory, "node"), "--version"],
                             capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    return (out.stdout or "").strip() or None


def home():
    return paths.state_dir() / "cobalt"


def api_dir():
    return home() / "api"


def _pidfile():
    return paths.state_dir() / "cobalt.pid"


# ---------------------------------------------------------------------------
# Getting it
# ---------------------------------------------------------------------------

def installed():
    return (api_dir() / "node_modules").is_dir() and (api_dir() / "src").is_dir()


def requirements():
    """What is missing before cobalt can be installed at all.

    pnpm rather than npm is not a preference. cobalt is a pnpm workspace and
    its api depends on a sibling package as `workspace:^`, a protocol npm
    refuses outright with EUNSUPPORTEDPROTOCOL. Node ships corepack, which
    would solve this, but it is not on the PATH of a current Node install, so
    pnpm is simply another thing siphon offers to fetch.
    """
    absent = []
    for key in ("git", "node", "pnpm"):
        if find(key) is None:
            absent.append(key)
    # node-gyp is not a system package and siphon's bootstrap does not manage
    # it, but the install genuinely needs it: isolated-vm has no macOS
    # prebuild and compiles from source, and its script shells out to
    # `node-gyp rebuild`. Without it the failure is "node-gyp: command not
    # found" from inside a package manager's output, which is a long way from
    # anything actionable.
    if locate("node-gyp") is None:
        absent.append("node-gyp")
    return absent


def _run_in_env(argv, timeout, cwd=None):
    try:
        return subprocess.run(argv, capture_output=True, text=True,
                              timeout=timeout, cwd=cwd, env=_environment())
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(argv, 1, "", "timed out")
    except OSError as error:
        return subprocess.CompletedProcess(argv, 1, "", str(error))


def install(on_line=None, update=False):
    """Fetch cobalt's source and install its dependencies.

    Returns (ok, detail). Safe to call again: an existing checkout is updated
    rather than re-cloned.
    """
    absent = requirements()
    if absent:
        remedy = "`siphon setup` will offer to get them."
        if absent == ["node-gyp"]:
            remedy = ("It comes from npm rather than your package manager: "
                      "`npm install -g node-gyp`.")
        elif "node-gyp" in absent:
            remedy += " node-gyp comes from npm: `npm install -g node-gyp`."
        return False, (
            f"cobalt needs {' and '.join(absent)}, which "
            f"{'is' if len(absent) == 1 else 'are'} not installed. {remedy}"
        )

    def say(line):
        if on_line:
            on_line(line)

    target = home()
    pnpm = locate("pnpm") or "pnpm"

    if (target / ".git").is_dir():
        if not update:
            say("cobalt is already here.")
        else:
            say("$ git pull")
            done = _run(["git", "-C", str(target), "pull", "--ff-only"], 600)
            if done.returncode != 0:
                return False, _tail(done) or "could not update cobalt's source"
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        say(f"$ git clone {REPO}")
        # --depth 1: this is a thing to run, not a history to read.
        done = _run(["git", "clone", "--depth", "1", REPO, str(target)], 900)
        if done.returncode != 0:
            return False, _tail(done) or "could not fetch cobalt's source"

    if not (api_dir() / "package.json").is_file():
        return False, ("cobalt's source does not look the way siphon expects — "
                       "there is no api/package.json in it.")

    # Filtered to the api and what it depends on. A plain install would also
    # build the web frontend, which is a whole second application nobody here
    # is going to open.
    # Install scripts run. They have to: cobalt depends on isolated-vm, a
    # native module that is compiled at install time, and --ignore-scripts
    # leaves a node_modules that looks complete and fails at the first import
    # with "No native build was found".
    version = node_version()
    say(f"$ pnpm install --prod --filter @imput/cobalt-api...  (node {version})")
    done = _run_in_env([pnpm, "install", "--prod",
                        "--filter", "@imput/cobalt-api..."],
                       1800, cwd=str(target))
    if done.returncode != 0 or not (api_dir() / "node_modules").is_dir():
        detail = _tail(done) or "pnpm install failed"
        if "node-gyp" in (done.stdout or "") + (done.stderr or ""):
            detail += (" — cobalt's isolated-vm has to be compiled, and it "
                       "does not build against the newest Node. "
                       "`brew install node@22` gives siphon one it can use, "
                       "without changing what `node` means anywhere else.")
        return False, detail

    return True, "cobalt is installed."


def remove():
    """Delete the checkout. Everything here is siphon's own."""
    stop()
    shutil.rmtree(home(), ignore_errors=True)
    return True


# ---------------------------------------------------------------------------
# Running it
# ---------------------------------------------------------------------------

def url(port=DEFAULT_PORT):
    return f"http://{HOST}:{port}"


def running(port=DEFAULT_PORT, timeout=3):
    """Whether something at that address answers as cobalt."""
    try:
        request = urllib.request.Request(
            url(port) + "/", headers={"Accept": "application/json"})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read() or b"{}")
    except (urllib.error.URLError, OSError, ValueError, TimeoutError):
        return False, None
    version = (payload.get("cobalt") or {}).get("version") or payload.get("version")
    return True, version


def start(port=DEFAULT_PORT, wait=40, on_line=None):
    """Launch cobalt in the background. Returns (ok, detail)."""
    alive, version = running(port)
    if alive:
        return True, f"Already running{f' — cobalt {version}' if version else ''}."
    if not installed():
        return False, "cobalt is not installed yet. Run `siphon cobalt install`."

    # If the token provider is up, tell cobalt where it is. Without this a
    # YouTube link tunnels zero bytes — cobalt does not mint poTokens itself.
    session_server = {}
    try:
        import pot_provider
        if pot_provider.bridge_running():
            session_server["YOUTUBE_SESSION_SERVER"] = pot_provider.bridge_url()
            # The token is a *web* BotGuard token, so the innertube client
            # asking with it has to be a web one. cobalt's own documentation
            # says WEB_EMBEDDED; with a mismatched client YouTube accepts the
            # request, returns no error, and streams nothing — which looks
            # exactly like the token not working.
            session_server["YOUTUBE_SESSION_INNERTUBE_CLIENT"] = os.environ.get(
                "SIPHON_INNERTUBE_CLIENT", "WEB_EMBEDDED")
    except Exception:
        pass

    directory = node_bin()
    node = os.path.join(directory, "node") if directory else "node"
    environment = _environment({
        "API_URL": url(port) + "/",
        "API_PORT": str(port),
        # cobalt binds every interface by default. Here it is a helper for one
        # machine, so it gets the loopback address and nothing else.
        "API_LISTEN_ADDRESS": HOST,
        "API_NAME": "siphon-local",
        **session_server,
    })

    log = paths.state_dir() / "cobalt.log"
    handle = open(log, "ab", buffering=0)
    try:
        process = subprocess.Popen(
            [node, "src/cobalt"], cwd=str(api_dir()), env=environment,
            stdout=handle, stderr=handle, stdin=subprocess.DEVNULL,
            start_new_session=True,      # survives siphon closing
        )
    except OSError as error:
        handle.close()
        return False, f"Could not start cobalt: {error}"

    _pidfile().write_text(str(process.pid))

    deadline = time.time() + wait
    while time.time() < deadline:
        if process.poll() is not None:
            detail = _log_tail(log) or "cobalt exited immediately."
            if "No native build was found" in detail:
                detail = (
                    f"cobalt's isolated-vm was not built for node "
                    f"{node_version() or '?'}. Reinstall it with "
                    f"`siphon cobalt install --update`, or install node@22 "
                    f"so siphon has a Node that module supports."
                )
            return False, detail
        alive, version = running(port, timeout=2)
        if alive:
            return True, f"cobalt {version or ''} on {url(port)}".strip()
        time.sleep(0.6)

    return False, (f"cobalt did not answer within {wait}s. Its output is in "
                   f"{log}")


def stop():
    """Stop a cobalt siphon started. Returns True if something was stopped."""
    try:
        pid = int(_pidfile().read_text().strip())
    except (OSError, ValueError):
        return False
    try:
        os.killpg(os.getpgid(pid), signal.SIGTERM)
    except (OSError, ProcessLookupError):
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            _pidfile().unlink(missing_ok=True)
            return False
    _pidfile().unlink(missing_ok=True)
    return True


def status(port=DEFAULT_PORT):
    alive, version = running(port)
    return {
        "installed": installed(),
        "running": alive,
        "version": version,
        "url": url(port),
        "path": str(home()),
        "missing": requirements(),
        "node": node_version(),
        "tokens": _token_status(),
    }


def _token_status():
    try:
        import pot_provider
        return pot_provider.status()
    except Exception:                       # noqa: BLE001
        return {"installed": False, "running": False}


def ensure(port=DEFAULT_PORT, on_line=None, tokens=True):
    """Install if needed, start if needed, and hand back the address.

    The token provider comes up first when `tokens` is set, because cobalt
    reads YOUTUBE_SESSION_SERVER at startup — starting it afterwards leaves a
    cobalt that cannot do YouTube until it is restarted.
    """
    if tokens:
        try:
            import pot_provider
            if not pot_provider.bridge_running():
                if not pot_provider.installed():
                    ok, detail = pot_provider.install(on_line=on_line)
                    if not ok and on_line:
                        on_line(f"token provider: {detail}")
                if pot_provider.installed():
                    ok, detail = pot_provider.ensure(on_line=on_line)
                    if not ok and on_line:
                        on_line(f"token provider: {detail}")
        except Exception as error:          # noqa: BLE001
            if on_line:
                on_line(f"token provider: {error}")

    alive, _ = running(port)
    if alive:
        return True, url(port)
    if not installed():
        ok, detail = install(on_line=on_line)
        if not ok:
            return False, detail
    ok, detail = start(port, on_line=on_line)
    return (True, url(port)) if ok else (False, detail)


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
