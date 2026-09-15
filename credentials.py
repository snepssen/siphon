"""Keys and tokens, where to get them, and where they are kept.

Most of what siphon reaches for needs nothing at all. Deezer answers anyone,
and yt-dlp handles YouTube, SoundCloud and Bandcamp without being told who
you are. Two services do need something, and rather than failing at them with
a stack trace this module knows what they want, what it is called on their own
website, and the steps to go and find it — so the settings page can be
generated from this file rather than written twice.

**Nothing here is ever sent anywhere except to the service it belongs to.**
`status()` is what the interface gets: whether a thing is set and its last four
characters, never the value.

Storage is the system keychain where there is one, and a file with owner-only
permissions where there is not. The file path is printed by `where()` so that
deleting the lot is always one obvious command.
"""

import json
import os
import subprocess
import sys
from dataclasses import dataclass, field

from paths import config_dir

SERVICE_NAME = "siphon"


@dataclass(frozen=True)
class Field:
    key: str
    label: str
    secret: bool = True
    placeholder: str = ""
    note: str = ""


@dataclass(frozen=True)
class Service:
    key: str
    label: str
    why: str                      # what stops working without it
    fields: tuple
    url: str = ""                 # where to go
    steps: tuple = field(default_factory=tuple)
    optional: bool = True


SERVICES = {
    "spotify": Service(
        key="spotify",
        label="Spotify",
        why="Reading Spotify playlists, albums and tracks. siphon tries the "
            "public embed first and only needs these when that fails — which "
            "it does periodically, and without warning.",
        url="https://developer.spotify.com/dashboard",
        steps=(
            "Sign in at developer.spotify.com/dashboard with your ordinary "
            "Spotify account — there is nothing to pay and no review.",
            "Click Create app. The name and description are only ever shown "
            "to you; 'siphon' and 'personal use' are fine.",
            "For Redirect URI put http://127.0.0.1:7777/callback. siphon does "
            "not use it, but the form will not submit without one.",
            "Tick Web API, agree to the terms, and create it.",
            "Open the app, then Settings. The Client ID is on that page, and "
            "the Client secret is behind 'View client secret'.",
        ),
        fields=(
            Field("client_id", "Client ID", secret=False,
                  placeholder="32 hex characters"),
            Field("client_secret", "Client secret",
                  placeholder="32 hex characters"),
        ),
    ),
    "tidal": Service(
        key="tidal",
        label="Tidal",
        why="Reading Tidal playlists and albums. Without it, Tidal links are "
            "the one kind siphon has to turn down.",
        url="https://developer.tidal.com/dashboard",
        steps=(
            "Sign in at developer.tidal.com/dashboard. A free Tidal account "
            "is enough; a subscription is not required to read catalogue "
            "metadata.",
            "Create an app — again, the details are only shown to you.",
            "Copy the Client ID and Client Secret from the app's page.",
        ),
        fields=(
            Field("client_id", "Client ID", secret=False),
            Field("client_secret", "Client secret"),
        ),
    ),
    "cobalt": Service(
        key="cobalt",
        label="cobalt instance",
        why="Using your own cobalt instance as a second fetch backend. siphon "
            "works without one; this is for when you want cobalt's exact "
            "behaviour for a particular site.",
        url="https://github.com/imputnet/cobalt/blob/main/docs/run-an-instance.md",
        steps=(
            "Run an instance by following cobalt's own docker compose guide.",
            "The address is whatever you set API_URL to — http://localhost:9000 "
            "if you followed the guide unchanged.",
            "An API key is only needed if you set one up; leave it empty "
            "otherwise.",
        ),
        fields=(
            Field("instance_url", "Instance address", secret=False,
                  placeholder="http://localhost:9000"),
            Field("api_key", "API key", note="Only if your instance requires one."),
        ),
    ),
}

# Services that work with nothing, listed so the settings page can say so
# rather than leaving somebody wondering what they forgot.
NO_CREDENTIALS_NEEDED = {
    "deezer": "Deezer's public API answers anyone. Nothing to set up.",
    "youtube": "Handled by yt-dlp. Nothing to set up.",
    "soundcloud": "Handled by yt-dlp. Nothing to set up.",
    "bandcamp": "Handled by yt-dlp. Nothing to set up.",
}


def _slot(service, key):
    return f"{service}.{key}"


# ---------------------------------------------------------------------------
# Backends
# ---------------------------------------------------------------------------

class _FileStore:
    """JSON in the config directory, owner-readable only."""

    def __init__(self):
        self.path = config_dir() / "credentials.json"

    def _read(self):
        try:
            with open(self.path, "r", encoding="utf-8") as handle:
                return json.load(handle)
        except FileNotFoundError:
            return {}
        except (OSError, ValueError):
            return {}

    def _write(self, data):
        tmp = self.path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, sort_keys=True)
        os.chmod(tmp, 0o600)
        os.replace(tmp, self.path)

    def get(self, slot):
        return self._read().get(slot)

    def set(self, slot, value):
        data = self._read()
        data[slot] = value
        self._write(data)

    def delete(self, slot):
        data = self._read()
        if data.pop(slot, None) is not None:
            self._write(data)

    def describe(self):
        return f"a file at {self.path}, readable only by you"


class _KeychainStore:
    """The macOS keychain, via the `security` command.

    Falls back to the file store for any slot the keychain refuses, so a
    locked or unusual keychain degrades to the old behaviour instead of
    losing somebody's keys.
    """

    def __init__(self):
        self.fallback = _FileStore()

    def _run(self, args):
        return subprocess.run(
            ["security", *args], capture_output=True, text=True, timeout=15,
        )

    def get(self, slot):
        try:
            out = self._run(
                ["find-generic-password", "-s", SERVICE_NAME, "-a", slot, "-w"]
            )
        except (OSError, subprocess.SubprocessError):
            return self.fallback.get(slot)
        if out.returncode == 0:
            return out.stdout.rstrip("\n") or None
        return self.fallback.get(slot)

    def set(self, slot, value):
        try:
            out = self._run([
                "add-generic-password", "-U", "-s", SERVICE_NAME,
                "-a", slot, "-w", value,
                "-l", f"{SERVICE_NAME}: {slot}",
                "-D", "siphon credential",
            ])
        except (OSError, subprocess.SubprocessError):
            return self.fallback.set(slot, value)
        if out.returncode != 0:
            self.fallback.set(slot, value)

    def delete(self, slot):
        try:
            self._run(["delete-generic-password", "-s", SERVICE_NAME, "-a", slot])
        except (OSError, subprocess.SubprocessError):
            pass
        self.fallback.delete(slot)

    def describe(self):
        return "your login keychain"


def _store():
    global _cached_store
    if _cached_store is None:
        if sys.platform == "darwin" and _has_security():
            _cached_store = _KeychainStore()
        else:
            _cached_store = _FileStore()
    return _cached_store


_cached_store = None


def _has_security():
    from shutil import which
    return which("security") is not None


# ---------------------------------------------------------------------------
# The interface everything else uses
# ---------------------------------------------------------------------------

def get(service, key):
    """A credential, or None. Environment wins, so CI never needs a keychain."""
    env = os.environ.get(f"SIPHON_{service.upper()}_{key.upper()}")
    if env:
        return env
    return _store().get(_slot(service, key))


def set(service, key, value):
    """Store a credential. An empty value deletes it."""
    _check(service, key)
    value = (value or "").strip()
    if not value:
        return delete(service, key)
    _store().set(_slot(service, key), value)


def delete(service, key):
    _check(service, key)
    _store().delete(_slot(service, key))


def have(service):
    """True when every field of a service is filled in."""
    definition = SERVICES.get(service)
    if definition is None:
        return False
    return all(
        get(service, f.key) for f in definition.fields if f.secret or not f.note
    )


def status():
    """What the settings page shows. Values never leave this module.

    Each field reports whether it is set and, when it is, the last four
    characters — enough to tell two keys apart, useless to anyone reading over
    a shoulder.
    """
    report = {"storage": _store().describe(), "file": str(where()), "services": []}
    for key, service in SERVICES.items():
        entry = {
            "key": key,
            "label": service.label,
            "why": service.why,
            "url": service.url,
            "steps": list(service.steps),
            "complete": have(key),
            "fields": [],
        }
        for f in service.fields:
            value = get(key, f.key)
            entry["fields"].append({
                "key": f.key,
                "label": f.label,
                "secret": f.secret,
                "placeholder": f.placeholder,
                "note": f.note,
                "set": bool(value),
                "hint": _redact(value),
            })
        report["services"].append(entry)
    report["no_setup_needed"] = dict(NO_CREDENTIALS_NEEDED)
    return report


def where():
    """The file, so that 'delete everything' is always one visible path."""
    return config_dir() / "credentials.json"


def _redact(value):
    if not value:
        return ""
    if len(value) <= 4:
        return "•" * len(value)
    return "•" * max(4, len(value) - 4) + value[-4:]


def _check(service, key):
    definition = SERVICES.get(service)
    if definition is None:
        raise KeyError(f"No service called {service!r}")
    if key not in {f.key for f in definition.fields}:
        raise KeyError(f"{definition.label} has no field called {key!r}")
