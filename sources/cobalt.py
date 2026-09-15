"""A self-hosted cobalt instance, as a second way to fetch.

siphon exists because the public cobalt stopped being able to reach YouTube,
and it uses yt-dlp instead. This module is the other direction: if you run
your own cobalt — and running one is a docker compose file — siphon can hand
it a URL and take back whatever it produces.

**Why have both.** They fail differently. cobalt and yt-dlp are separate
implementations of the same awkward job, written by different people against
the same moving targets, so a site that has broken for one is quite often
fine on the other. Having the second backend one setting away turns "this is
broken today" into "try the other one".

**It is off unless configured.** `handles` returns False with no instance
address set, so yt-dlp keeps everything by default and nothing changes for
anybody who has not asked for this. The address goes in Settings.

cobalt's API is a POST with a JSON body, answering one of a few statuses:
a direct link, a redirect to the origin, a list to pick from, or an error.
`_interpret` turns each into either a url or a sentence.
"""

import json
import urllib.parse

import credentials
import net
from model import Item
from sources import SourceError

TIMEOUT = 45


def instance():
    """The configured address, normalised, or None if there is not one."""
    address = (credentials.get("cobalt", "instance_url") or "").strip()
    if not address:
        return None
    if "://" not in address:
        address = "http://" + address
    return address.rstrip("/")


def handles(target):
    """Only ever claims a link when explicitly asked to.

    The `cobalt:` prefix is how somebody says "fetch this one the other way";
    an ordinary link never reaches here, because yt-dlp is asked first.

    It claims the prefix even with no instance configured. Declining would
    leave the registry to answer "nothing here knows what to do with
    cobalt:…", which is both true and useless — the person plainly meant
    cobalt, and what they need told is how to point siphon at one.
    """
    return target.lower().startswith("cobalt:")


def expand(url, **_options):
    """A cobalt: link into one fetchable item.

    cobalt has no notion of a playlist — one URL, one file — so this always
    produces exactly one item, already fetchable, which means `resolve` has
    nothing to do and the fetch stage takes it straight from the direct link.
    """
    target = url.split(":", 1)[1].strip()
    if not target:
        raise SourceError("A cobalt: link needs an address after the colon.")
    if not target.startswith(("http://", "https://")):
        target = "https://" + target

    address = instance()
    if address is None:
        raise SourceError(
            "No cobalt instance is configured. Put its address in Settings — "
            "http://localhost:9000 if you followed cobalt's own docker guide."
        )

    payload = _ask(address, target)
    direct, filename = _interpret(payload, target)

    return [Item(
        origin="cobalt",
        url=direct,
        kind="video",
        title=filename or target,
        webpage_url=target,
        extra={"via": "cobalt", "instance": address, "source_url": target},
    )]


def _ask(address, target, extra=None):
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    key = credentials.get("cobalt", "api_key")
    if key:
        headers["Authorization"] = f"Api-Key {key}"

    body = {"url": target}
    body.update(extra or {})
    try:
        return net.get_json(f"{address}/", headers=headers, timeout=TIMEOUT,
                            retries=2, data=json.dumps(body).encode("utf-8"))
    except net.HttpError as error:
        raise SourceError(
            f"Could not reach your cobalt instance at {address}. {error} "
            f"Is it running?"
        ) from error


def _interpret(payload, target):
    """cobalt's answer, as either a url or a sentence."""
    if not isinstance(payload, dict):
        raise SourceError("That cobalt instance answered with something odd.")

    status = payload.get("status")

    if status in {"stream", "redirect", "tunnel"}:
        url = payload.get("url")
        if not url:
            raise SourceError("cobalt said yes and gave no address.")
        return url, payload.get("filename")

    if status == "picker":
        # A picker is several files — a photo carousel, usually. siphon takes
        # the first rather than inventing a second choice-and-confirm flow for
        # a case the resolver's one does not fit.
        items = payload.get("picker") or []
        first = next((i for i in items if i.get("url")), None)
        if first is None:
            raise SourceError("cobalt offered a picker with nothing in it.")
        return first["url"], payload.get("filename")

    if status in {"error", "rate-limit"}:
        text = payload.get("error") or {}
        code = text.get("code") if isinstance(text, dict) else str(text)
        raise SourceError(f"cobalt could not fetch {target}: {code or 'no reason given'}")

    raise SourceError(f"cobalt answered with a status siphon does not know: {status}")


def reachable():
    """Whether the configured instance answers at all. For Settings."""
    address = instance()
    if address is None:
        return False, "No address set."
    try:
        payload = net.get_json(f"{address}/", headers={"Accept": "application/json"},
                               timeout=10, retries=1)
    except net.HttpError as error:
        return False, str(error)
    version = (payload.get("cobalt") or {}).get("version") or payload.get("version")
    return True, f"Answering{f' — cobalt {version}' if version else ''}."
