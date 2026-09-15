#!/bin/sh
# Starts siphon, checking first for the two programs it needs and does not
# ship, so that a missing ffmpeg is a sentence rather than a traceback.
cd "$(dirname "$0")" || exit 1

for candidate in python3 python; do
  command -v "$candidate" >/dev/null 2>&1 || continue
  if "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
    PYTHON="$candidate"
    break
  fi
done

if [ -z "${PYTHON:-}" ]; then
  echo "siphon needs Python 3.10 or newer, and could not find one." >&2
  exit 1
fi

exec "$PYTHON" siphon.py "$@"
