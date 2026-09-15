#!/usr/bin/env bash
# Package what build.sh makes into things somebody can download.
#
#   ./release.sh                test, build, package and verify into dist/
#   ./release.sh --skip-tests   package what is already here
#
# This stops at the edge of publishing. It writes dist/ and prints the command
# that would create the GitHub release; it does not run it. Putting a binary in
# front of the public is a decision, and a script that makes it silently is a
# script that will one day make it by accident.
#
# What is in a release, and why there are four:
#
#   siphon-<v>.pyz              one file, every platform, needs python3
#   siphon-<v>-macOS.zip        the .app, for people who want an icon
#   siphon-<v>-linux.tar.gz     the .pyz, a launcher and a .desktop entry
#   siphon-<v>-windows.zip      the .pyz and a .bat launcher
#
# None of them bundle Python, ffmpeg or yt-dlp — see the note at the top of
# build.sh for why that is a decision. Every archive carries INSTALL.txt saying
# what it needs, and siphon offers to install all of it on first run, so a
# download cannot fail silently on a machine that is missing something.
set -euo pipefail
cd "$(dirname "$0")"
ROOT="$(pwd)"
VERSION="$(tr -d '[:space:]' < "$ROOT/VERSION")"
[ -n "$VERSION" ] || { echo "VERSION is empty" >&2; exit 1; }
BUILD="$ROOT/build"
DIST="$ROOT/dist"

say() { printf '  %s\n' "$*"; }

if [ "${1:-}" != "--skip-tests" ]; then
  say "running the tests"
  python3 -m unittest discover -s tests -q >/dev/null 2>&1 \
    || { echo "tests failed — not packaging a release" >&2; exit 1; }
  say "compiling every module"
  python3 -m compileall -q . -x '(build|dist|\.git)' >/dev/null \
    || { echo "a module does not compile" >&2; exit 1; }
fi

./build.sh all

rm -rf "$DIST"; mkdir -p "$DIST"

install_notes() {
  cat <<NOTES
siphon $VERSION

siphon needs three things it does not ship:

  python3   3.10 or newer
  ffmpeg    to convert, and to mux what yt-dlp fetches
  yt-dlp    to fetch from a URL

It does not bundle them on purpose. yt-dlp ships a release most weeks because
the sites it reads keep changing, and a copy frozen inside this download would
be broken by the time you opened it — this way, upgrading yt-dlp fixes siphon
without siphon being touched.

You do not have to install them by hand. Start siphon and it will list what is
missing, show the exact commands, and install all of it if you press return.

  macOS    brew install python ffmpeg yt-dlp
  Linux    your package manager; yt-dlp is often 'pipx install yt-dlp'
  Windows  winget install Python.Python.3.12 Gyan.FFmpeg yt-dlp.yt-dlp

Everything siphon does happens on your machine. Nothing is uploaded, and the
only thing that leaves is the request that fetches the media.
NOTES
}

# ---- the bare archive ---------------------------------------------------
cp "$BUILD/siphon.pyz" "$DIST/siphon-$VERSION.pyz"
say "siphon-$VERSION.pyz"

# ---- macOS --------------------------------------------------------------
if [ -d "$BUILD/siphon.app" ]; then
  staging="$DIST/macos"
  mkdir -p "$staging"
  cp -R "$BUILD/siphon.app" "$staging/"
  install_notes > "$staging/INSTALL.txt"
  ( cd "$staging" && zip -qry "../siphon-$VERSION-macOS.zip" "siphon.app" "INSTALL.txt" )
  rm -rf "$staging"
  say "siphon-$VERSION-macOS.zip"
fi

# ---- Linux --------------------------------------------------------------
staging="$DIST/linux/siphon-$VERSION"
mkdir -p "$staging"
cp "$BUILD/siphon.pyz" "$staging/siphon.pyz"
install_notes > "$staging/INSTALL.txt"
cat > "$staging/siphon" <<'LAUNCH'
#!/bin/sh
# Run siphon from wherever this was unpacked.
exec python3 "$(dirname "$0")/siphon.pyz" "$@"
LAUNCH
chmod +x "$staging/siphon"
cat > "$staging/siphon.desktop" <<DESKTOP
[Desktop Entry]
Type=Application
Name=siphon
Comment=Fetch media from a URL, or convert what you already have
Exec=siphon
Terminal=false
Categories=AudioVideo;Audio;Video;Network;
DESKTOP
( cd "$DIST/linux" && tar -czf "../siphon-$VERSION-linux.tar.gz" "siphon-$VERSION" )
rm -rf "$DIST/linux"
say "siphon-$VERSION-linux.tar.gz"

# ---- Windows ------------------------------------------------------------
staging="$DIST/windows"
mkdir -p "$staging"
cp "$BUILD/siphon.pyz" "$staging/siphon.pyz"
install_notes > "$staging/INSTALL.txt"
printf '@echo off\r\npython "%%~dp0siphon.pyz" %%*\r\npause\r\n' > "$staging/siphon.bat"
( cd "$staging" && zip -qr "../siphon-$VERSION-windows.zip" . )
rm -rf "$staging"
say "siphon-$VERSION-windows.zip"

# ---- prove the archives contain something that runs ---------------------
say "checking the packaged archive actually runs"
scratch="$(mktemp -d)"
trap 'rm -rf "$scratch"' EXIT
tar -xzf "$DIST/siphon-$VERSION-linux.tar.gz" -C "$scratch"
listed="$(python3 "$scratch/siphon-$VERSION/siphon.pyz" formats 2>/dev/null | grep -cE '^  [a-z]' || true)"
[ "${listed:-0}" -ge 15 ] \
  || { echo "the packaged .pyz did not run: $listed formats" >&2; exit 1; }
say "it runs and lists $listed formats"

( cd "$DIST" && shasum -a 256 siphon-* > "SHA256SUMS" )
say "SHA256SUMS"

echo
echo "dist/ is ready. Nothing has been published."
echo
echo "To publish, when you have decided to:"
echo
echo "  gh release create v$VERSION dist/siphon-* \\"
echo "    --title 'siphon $VERSION' --notes-file RELEASE_NOTES.md"
