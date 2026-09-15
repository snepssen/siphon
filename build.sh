#!/usr/bin/env bash
# Build the things somebody can double-click.
#
#   ./build.sh            everything this platform can make
#   ./build.sh app        siphon.app                   (macOS only)
#   ./build.sh pyz        siphon.pyz                   (anywhere)
#
# What this does NOT do is bundle a Python, an ffmpeg or a yt-dlp, and that is
# a decision rather than an omission. Bundling Python would take a build-time
# dependency on PyInstaller or py2app, which everyone who wanted to build this
# would then need too. Bundling ffmpeg means eighty megabytes and a licensing
# decision that belongs to whoever redistributes it. And a frozen yt-dlp would
# be broken within a fortnight — the sites it reads change weekly, which is the
# whole reason siphon calls the one on the PATH.
#
# So the bundle removes the terminal, not the prerequisites. It finds what it
# needs, and when it cannot, it offers to install it — which is the honest
# version of "double-click to run" for a program that is, underneath, a front
# end to two other programs.
set -euo pipefail
cd "$(dirname "$0")"
ROOT="$(pwd)"
VERSION="$(tr -d '[:space:]' < "$ROOT/VERSION")"
[ -n "$VERSION" ] || { echo "VERSION is empty" >&2; exit 1; }
BUILD="$ROOT/build"

# Everything the application needs at runtime, and nothing else: no tests, no
# fixtures, no build scripts. A bundle that carries its own test suite is a
# bundle nobody checked the contents of.
MODULES=(siphon.py app.py jobs.py model.py formats.py library.py paths.py \
         credentials.py platform_support.py net.py resolve.py bootstrap.py \
         cobalt_service.py pot_provider.py index.html __main__.py)
PACKAGES=(sources engines)

say() { printf '  %s\n' "$*"; }

stage_into() {
  local staging="$1"
  mkdir -p "$staging/tools"
  for module in "${MODULES[@]}"; do cp "$ROOT/$module" "$staging/"; done
  for package in "${PACKAGES[@]}"; do
    # Copy the package, then drop the caches — a __pycache__ built by a
    # different Python is bytes that will never be read and might confuse
    # somebody reading the archive.
    cp -R "$ROOT/$package" "$staging/"
    rm -rf "$staging/$package/__pycache__"
  done
  cp "$ROOT/tools/check_tools.py" "$staging/tools/"
}

build_pyz() {
  local staging="$BUILD/pyz"
  rm -rf "$staging"; mkdir -p "$staging"
  stage_into "$staging"

  python3 -m zipapp "$staging" \
    --output "$BUILD/siphon.pyz" \
    --python "/usr/bin/env python3" --compress
  chmod +x "$BUILD/siphon.pyz"
  rm -rf "$staging"

  # A build nobody ran is a build that does not work. Prove the archive
  # executes and answers before calling it finished.
  #
  # It counts the formats rather than checking for a particular one: pinning
  # the test to "mp3" would turn green to red the day the catalogue is
  # reordered, which is the tool growing rather than the build breaking.
  local listed
  listed="$("$BUILD/siphon.pyz" formats 2>/dev/null | grep -cE '^  [a-z]' || true)"
  if [ "${listed:-0}" -ge 15 ]; then
    say "siphon.pyz  ($(du -h "$BUILD/siphon.pyz" | cut -f1)) — runs, lists $listed formats"
  else
    echo "the .pyz did not answer 'formats' as expected: $listed lines" >&2
    exit 1
  fi
}

build_app() {
  [ "$(uname)" = "Darwin" ] || { say "skipping the .app: not macOS"; return; }
  local app="$BUILD/siphon.app"
  rm -rf "$app"
  mkdir -p "$app/Contents/MacOS" "$app/Contents/Resources"

  stage_into "$app/Contents/Resources"
  cp "$ROOT/packaging/launcher.sh" "$app/Contents/MacOS/siphon"
  chmod +x "$app/Contents/MacOS/siphon"

  build_icon "$app/Contents/Resources/appicon.icns"

  cat > "$app/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>siphon</string>
  <key>CFBundleDisplayName</key><string>siphon</string>
  <key>CFBundleIdentifier</key><string>com.snepssen.siphon</string>
  <key>CFBundleVersion</key><string>$VERSION</string>
  <key>CFBundleShortVersionString</key><string>$VERSION</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleExecutable</key><string>siphon</string>
  <key>CFBundleIconFile</key><string>appicon</string>
  <key>LSMinimumSystemVersion</key><string>11.0</string>
  <key>NSHighResolutionCapable</key><true/>
</dict>
</plist>
PLIST

  # An unsigned bundle is quarantined on first open. Ad-hoc signing does not
  # avoid that, but it does stop macOS reporting the app as damaged after any
  # later change to its contents.
  codesign --force --deep --sign - "$app" >/dev/null 2>&1 \
    || say "note: could not ad-hoc sign (codesign unavailable)"

  say "siphon.app — drag it to /Applications"
}

build_icon() {
  local target="$1"
  local iconset="$BUILD/icon.iconset"
  rm -rf "$iconset"; mkdir -p "$iconset"
  python3 "$ROOT/tools/icon.py" "$BUILD/icons" >/dev/null
  for size in 16 32 128 256 512; do
    cp "$BUILD/icons/icon_${size}.png" "$iconset/icon_${size}x${size}.png"
    cp "$BUILD/icons/icon_$((size * 2)).png" \
       "$iconset/icon_${size}x${size}@2x.png"
  done
  # macOS 27's iconutil rejects the same conventional ten-file iconset that
  # earlier versions accept. Keep Apple's validator as the first choice, then
  # pack those PNG representations directly as an ICNS chunk container.
  if ! iconutil --convert icns "$iconset" --output "$target" 2>/dev/null; then
    say "note: iconutil rejected the iconset; using the ICNS fallback"
    python3 "$ROOT/tools/make_icns.py" "$iconset" "$target"
  fi
  [ -s "$target" ] || { echo "the application icon was not built" >&2; exit 1; }
  rm -rf "$iconset" "$BUILD/icons"
}

build_desktop() {
  python3 "$ROOT/tools/icon.py" "$BUILD/icons" >/dev/null
  mv "$BUILD/icons/icon_256.png" "$BUILD/icon_256.png"
  rm -rf "$BUILD/icons"
  cat > "$BUILD/siphon.desktop" <<DESKTOP
[Desktop Entry]
Type=Application
Name=siphon
Comment=Fetch media from a URL, or convert what you already have
Exec=$ROOT/start.sh
Icon=$BUILD/icon_256.png
Terminal=false
Categories=AudioVideo;Audio;Video;Network;
DESKTOP
  say "siphon.desktop — copy it to ~/.local/share/applications/"
}

mkdir -p "$BUILD"
echo "siphon $VERSION"
case "${1:-all}" in
  pyz)     build_pyz ;;
  app)     build_app ;;
  desktop) build_desktop ;;
  all)
    build_pyz
    if [ "$(uname)" = "Darwin" ]; then build_app; else build_desktop; fi
    ;;
  *) echo "usage: ./build.sh [all|pyz|app|desktop]" >&2; exit 2 ;;
esac
echo
echo "in $BUILD"
