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
import urllib.error
import urllib.parse
import urllib.request

USER_AGENT = "siphon/0.1 (+https://github.com/snepssen/siphon)"
TIMEOUT = 20
RETRIES = 3


def get_json(url, headers=None, timeout=TIMEOUT, retries=RETRIES, data=None):
    """A JSON body, or a sentence about why not.

    `data` makes it a POST, which is how both token endpoints here are asked
    for a client-credentials grant.
    """
    body = get_bytes(url, headers=headers, timeout=timeout, retries=retries,
                     data=data)
    try:
        return json.loads(body)
    except ValueError as error:
        raise HttpError(f"{_host(url)} answered with something that was not JSON.") from error


def get_bytes(url, headers=None, timeout=TIMEOUT, retries=RETRIES, data=None):
    """Raw bytes. Retries the failures that are worth retrying, and no others."""
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
                raise HttpError(_explain(error, url)) from error
            last = error
        except (urllib.error.URLError, socket.timeout, TimeoutError, OSError) as error:
            last = error
        if attempt < retries - 1:
            time.sleep(0.6 * (2 ** attempt))
    raise HttpError(_explain(last, url))


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
