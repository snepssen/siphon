#!/usr/bin/env python3
"""Take the screenshots the project page uses, from the real interface.

    python3 tools/shoot.py <window url with token> <name> [out dir] [WxH]

whatever the window is showing right now is what gets photographed, under
`name`, in both themes. Drive the window to the state worth showing, then run
this.

The window keeps an open event-stream, which is what makes it feel alive and
also what stops a headless browser ever deciding the page has finished
loading — `--virtual-time-budget` waits on a connection that by design never
closes.

So the page is photographed with its own data but nothing live. This fetches
the page and the state the server would have sent it, then puts a shim in
front of the application: `fetch` answers from that captured state and
`EventSource` is a stub. Not one line of the application is changed or
copied — it runs exactly as it does in the window, draws exactly what the
window would draw, and then sits still long enough to be photographed.

Two consequences worth keeping. The picture can never drift from the
interface, because it *is* the interface. And a screenshot can be taken of a
state that is awkward to hold still by hand — a track waiting to be chosen,
say — by capturing the moment it happens.
"""

import json
import re
import subprocess
import sys
import urllib.parse
import urllib.request
from pathlib import Path

CHROME_CANDIDATES = (
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium",
)

DEFAULT_SIZE = (960, 780)


def chrome():
    for path in CHROME_CANDIDATES:
        if Path(path).is_file():
            return path
    raise SystemExit("No Chrome or Chromium to take screenshots with.")


def get(url, token, path):
    request = urllib.request.Request(
        urllib.parse.urljoin(url, path), headers={"X-Siphon-Token": token})
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read()


def build(url, token, theme):
    """The real page, with its real data, and nothing that wants a network."""
    page = get(url, token, "/").decode("utf-8")

    captured = {
        "/api/state": json.loads(get(url, token, "/api/state")),
        "/api/settings": json.loads(get(url, token, "/api/settings")),
        "/api/cobalt": json.loads(get(url, token, "/api/cobalt")),
    }

    shim = """<script>
// Put in front of the application by tools/shoot.py. The application below is
// untouched: it calls fetch and opens an EventSource exactly as it always
// does, and gets the captured answers instead of a live server.
(function () {
  var captured = %s;
  window.fetch = function (path) {
    var key = String(path).split("?")[0];
    var body = captured[key] || {};
    return Promise.resolve({ ok: true, json: function () {
      return Promise.resolve(body);
    } });
  };
  window.EventSource = function () {
    return { close: function () {}, onmessage: null, onerror: null };
  };
})();
</script>
""" % json.dumps(captured)

    # The theme is stamped rather than chosen, so one page gives both pictures.
    page = re.sub(r"<!doctype html>", "", page, flags=re.IGNORECASE).lstrip()
    return (f'<!doctype html><html lang="en" data-theme="{theme}">'
            f"{shim}{page}</html>")


def shoot(html_path, out_path, width, height, scale=2):
    subprocess.run(
        [chrome(), "--headless=new", "--disable-gpu", "--hide-scrollbars",
         f"--window-size={width},{height}",
         f"--force-device-scale-factor={scale}",
         f"--screenshot={out_path}", f"file://{html_path}"],
        capture_output=True, timeout=180,
    )
    return Path(out_path).is_file() and Path(out_path).stat().st_size > 0


def main(argv):
    if not argv:
        print(__doc__)
        return 2
    url = argv[0]
    name = argv[1] if len(argv) > 1 else "window"
    out = Path(argv[2] if len(argv) > 2 else "docs/screens")
    width, height = DEFAULT_SIZE
    if len(argv) > 3 and "x" in argv[3]:
        width, height = (int(n) for n in argv[3].split("x"))
    out.mkdir(parents=True, exist_ok=True)

    token = urllib.parse.parse_qs(urllib.parse.urlparse(url).query).get("t", [""])[0]
    if not token:
        print("The url needs its ?t= token — siphon prints it at startup.",
              file=sys.stderr)
        return 2

    scratch = out / ".page"
    scratch.mkdir(exist_ok=True)
    made = []
    for theme in ("light", "dark"):
        page = scratch / f"{theme}.html"
        page.write_text(build(url, token, theme), encoding="utf-8")
        target = out / f"{name}-{theme}.png"
        if shoot(page.resolve(), target.resolve(), width, height):
            made.append(f"{target.name}  {target.stat().st_size // 1024} KB")
        else:
            print(f"  failed: {target.name}", file=sys.stderr)
    for line in made:
        print("  " + line)
    return 0 if made else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
