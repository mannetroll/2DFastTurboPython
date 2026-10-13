#!/usr/bin/env bash
set -euo pipefail
version=${1:?Usage: package-macos.sh VERSION}
root=$(cd "$(dirname "$0")/.." && pwd)
bash "$root/reference-macos/2DFastTurbo/Scripts/package.sh"
app="$root/reference-macos/2DFastTurbo/Build/2DFastTurbo.app"
/usr/libexec/PlistBuddy -c "Set :CFBundleShortVersionString $version" "$app/Contents/Info.plist"
/usr/libexec/PlistBuddy -c "Set :CFBundleVersion $version" "$app/Contents/Info.plist"
codesign --force --sign - "$app"
codesign --verify --deep --strict "$app"
mkdir -p "$root/dist-native" "$root/build-native-release"
stage=$(mktemp -d "$root/build-native-release/dmg.XXXXXX")
trap 'rm -rf "$stage"' EXIT
ditto "$app" "$stage/2DFastTurbo.app"
ln -s /Applications "$stage/Applications"
dmg="$root/dist-native/2DFastTurbo-${version}-macOS-arm64.dmg"
hdiutil create -ov -format UDZO -volname "2DFastTurbo $version" -srcfolder "$stage" "$dmg"
hdiutil verify "$dmg"
echo "$dmg"
