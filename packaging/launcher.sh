#!/bin/sh
# The executable inside siphon.app.
#
# Its whole job is to fail in sentences. siphon needs a Python and, to do
# anything at all, yt-dlp and ffmpeg — and a bundle that dies silently when one
# is missing is worse than no bundle: the window never appears and there is
# nowhere to look. So each prerequisite is checked here, and its absence
# becomes a dialog offering to install it.

BUNDLE="$(cd "$(dirname "$0")/../Resources" && pwd)"

say() {
  osascript -e "display dialog \"$1\" with title \"siphon\" \
    buttons {\"OK\"} default button 1 with icon caution" >/dev/null 2>&1
}

ask() {
  osascript -e "display dialog \"$1\" with title \"siphon\" \
    buttons {\"Not now\", \"Install\"} default button 2" 2>/dev/null \
    | grep -q "Install"
}

PYTHON=""
for candidate in /opt/homebrew/bin/python3 /usr/local/bin/python3 \
                 /usr/bin/python3 python3; do
  command -v "$candidate" >/dev/null 2>&1 || continue
  if "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
    PYTHON="$candidate"
    break
  fi
done

if [ -z "$PYTHON" ]; then
  say "siphon needs Python 3.10 or newer, and could not find one.\n\nInstall it from python.org, or with Homebrew:\n\n    brew install python"
  exit 1
fi

# The same check the command line does, and the same offer — except that a
# double-clicked app has no terminal to answer in, so the question is a dialog
# and the install runs in one that opens for the purpose.
if ! "$PYTHON" "$BUNDLE/tools/check_tools.py" 2>/dev/null; then
  if ask "siphon needs a couple of programs it does not ship — ffmpeg to convert, and yt-dlp to fetch.\n\nShall siphon install them with Homebrew?"; then
    osascript -e "tell application \"Terminal\"
      activate
      do script \"'$PYTHON' '$BUNDLE/bootstrap.py' --yes; echo; echo 'You can close this window.'\"
    end tell" >/dev/null 2>&1
    exit 0
  fi
  say "siphon cannot run without them. Open it again once they are installed, or run:\n\n    brew install ffmpeg yt-dlp"
  exit 1
fi

# Unbuffered, so anybody who runs the bundle from a terminal to work out why it
# did not open sees the address and the errors as they happen rather than when
# the process finally exits.
exec "$PYTHON" -u "$BUNDLE/app.py" "$@"
