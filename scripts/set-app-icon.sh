#!/bin/bash
# Build AppIcon.icns from a square PNG and install it in ListingProject Explorer.app
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP="$ROOT/ListingProject Explorer.app"
RESOURCES="$APP/Contents/Resources"
BUILD="$ROOT/.build"
ICONSET="$BUILD/AppIcon.iconset"

if [[ $# -lt 1 ]]; then
	echo "Usage: $0 path/to/icon.png" >&2
	exit 1
fi

SRC="$1"
if [[ ! -f "$SRC" ]]; then
	echo "File not found: $SRC" >&2
	exit 1
fi

mkdir -p "$RESOURCES" "$ICONSET"
rm -f "$ICONSET"/*.png

NORMALIZED="$BUILD/app-icon-normalized.png"
sips -s format png "$SRC" --out "$NORMALIZED" >/dev/null

make_icon() {
	local size=$1
	local name=$2
	sips -s format png -z "$size" "$size" "$NORMALIZED" --out "$ICONSET/$name" >/dev/null
}

make_icon 16 icon_16x16.png
make_icon 32 icon_16x16@2x.png
make_icon 32 icon_32x32.png
make_icon 64 icon_32x32@2x.png
make_icon 128 icon_128x128.png
make_icon 256 icon_128x128@2x.png
make_icon 256 icon_256x256.png
make_icon 512 icon_256x256@2x.png
make_icon 512 icon_512x512.png
make_icon 1024 icon_512x512@2x.png

iconutil -c icns "$ICONSET" -o "$RESOURCES/AppIcon.icns"

PLIST="$APP/Contents/Info.plist"
/usr/libexec/PlistBuddy -c "Delete :CFBundleIconFile" "$PLIST" 2>/dev/null || true
/usr/libexec/PlistBuddy -c "Add :CFBundleIconFile string AppIcon" "$PLIST"

touch "$APP"
echo "Installed AppIcon.icns in ListingProject Explorer.app"
