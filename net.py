"""The small amount of HTTP siphon does for itself.

yt-dlp does the fetching that matters. This is for the catalogue APIs — asking
Deezer what is in a playlist — and for pulling down cover art. Both are small
JSON or image requests to a handful of known hosts, which is why this is forty
lines of urllib rather than a dependency.

Everything here has a timeout. A tool that hangs forever on a service having a
bad afternoon is worse than one that says it could not reach it.
"""

import gzip
import json
import socket
import time
from pathlib import Path
import urllib.error
import urllib.parse
import urllib.request

USER_AGENT = "siphon/0.1 (+https://github.com/snepssen/siphon)"
TIMEOUT = 20
RETRIES = 3


def get_json(url, headers=None, timeout=TIMEOUT, retries=RETRIES, data=None,
             accept_errors=False):
    """A JSON body, or a sentence about why not.

    `data` makes it a POST, which is how both token endpoints here are asked
    for a client-credentials grant.

    `accept_errors` returns the body even when the status is a failure. Some
    APIs — cobalt among them — say what went wrong in a JSON body attached to
    a 400, and treating that as an unreachable host throws away the only
    useful sentence in the exchange.
    """
    body = get_bytes(url, headers=headers, timeout=timeout, retries=retries,
                     data=data, accept_errors=accept_errors)
    try:
        return json.loads(body)
    except ValueError as error:
        raise HttpError(f"{_host(url)} answered with something that was not JSON.") from error


def get_bytes(url, headers=None, timeout=TIMEOUT, retries=RETRIES, data=None,
              accept_errors=False):
    """Raw bytes. Retries the failures that are worth retrying, and no others.

    `accept_errors` hands back an error response's body instead of raising,
    for APIs that explain themselves in it.
    """
    request = urllib.request.Request(url, data=data)
    request.add_header("User-Agent", USER_AGENT)
    request.add_header("Accept-Encoding", "gzip")
    for key, value in (headers or {}).items():
        request.add_header(key, value)

    last = None
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read()
                if response.headers.get("Content-Encoding") == "gzip":
                    raw = gzip.decompress(raw)
                return raw
        except urllib.error.HTTPError as error:
            # 4xx means the request was wrong and will be wrong again. Only a
            # rate limit or a server fault is worth a second go.
            if error.code not in {429, 500, 502, 503, 504}:
                if accept_errors:
                    try:
                        return error.read()
                    except OSError:
                        pass
                raise HttpError(_explain(error, url)) from error
            last = error
        except (urllib.error.URLError, socket.timeout, TimeoutError, OSError) as error:
            last = error
        if attempt < retries - 1:
            time.sleep(0.6 * (2 ** attempt))
    raise HttpError(_explain(last, url))


def download(url, destination, headers=None, on_progress=None,
             should_cancel=None, timeout=TIMEOUT):
    """Stream a URL to a file, reporting progress. Returns the path written.

    For sources that have already done the hard part and handed back a plain
    address — cobalt does exactly this — where putting it back through yt-dlp
    would be asking an extractor to extract a file. Its generic handler tries,
    and on a one-shot tunnel gives up with "Did not get any data blocks".
    """
    import shutil as _shutil

    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(destination.name + ".part")

    request = urllib.request.Request(url)
    request.add_header("User-Agent", USER_AGENT)
    for key, value in (headers or {}).items():
        request.add_header(key, value)

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            total = response.headers.get("Content-Length")
            total = int(total) if total and total.isdigit() else None
            done = 0
            started = time.time()
            with open(partial, "wb") as handle:
                while True:
                    if should_cancel and should_cancel():
                        raise Cancelled("Stopped before it finished.")
                    chunk = response.read(262144)
                    if not chunk:
                        break
                    handle.write(chunk)
                    done += len(chunk)
                    if on_progress:
                        elapsed = max(time.time() - started, 0.001)
                        update = {"status": "downloading", "bytes_done": done,
                                  "bytes_total": total, "speed": done / elapsed}
                        if total:
                            update["progress"] = min(1.0, done / total)
                            remaining = total - done
                            update["eta"] = remaining / max(done / elapsed, 1)
                        on_progress(update)
    except Cancelled:
        partial.unlink(missing_ok=True)
        raise
    except (urllib.error.HTTPError, urllib.error.URLError, OSError) as error:
        partial.unlink(missing_ok=True)
        raise HttpError(_explain(error, url)) from error

    partial.replace(destination)
    if on_progress:
        on_progress({"status": "finished", "progress": 1.0})
    return str(destination)


class Cancelled(Exception):
    """The download was stopped on purpose."""


def follow(url, timeout=TIMEOUT):
    """Where a short link actually goes."""
    request = urllib.request.Request(url, method="HEAD")
    request.add_header("User-Agent", USER_AGENT)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.geturl()
    except (urllib.error.HTTPError, urllib.error.URLError, OSError):
        return url


def _host(url):
    try:
        return urllib.parse.urlparse(url).hostname or url
    except ValueError:
        return url


def _explain(error, url):
    host = _host(url)
    code = getattr(error, "code", None)
    if code == 404:
        return f"{host} has nothing at that address — check the link."
    if code == 401 or code == 403:
        return f"{host} refused the request. It may need a key; see Settings."
    if code == 429:
        return f"{host} is rate-limiting this machine. Leave it a minute."
    if code:
        return f"{host} answered {code}."
    return f"Could not reach {host}. {error}"


class HttpError(RuntimeError):
    """A request failed. The message is a sentence for the user."""
