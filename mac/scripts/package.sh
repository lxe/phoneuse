#!/bin/bash
# Builds "Phone Use.app" with the core inside it, signs it, and makes a DMG.
#   mac/scripts/package.sh [--fresh]      --fresh: rebuild the core bundle (bundle_core.py) too
# Signs with the keychain's Developer ID Application certificate and notarizes the DMG when there is one
# (SIGN_IDENTITY overrides; "-" signs ad hoc, which runs on this Mac only).
set -euo pipefail
cd "$(dirname "$0")/.."
MAC=$PWD
# a Developer ID Application certificate in the keychain signs a release; without one, ad hoc
DEVID=$(security find-identity -v -p codesigning | sed -n 's/.*"\(Developer ID Application: [^"]*\)".*/\1/p' | head -1)
IDENTITY=${SIGN_IDENTITY:-${DEVID:--}}
ENT=$MAC/PhoneUse/PhoneUse.entitlements
OUT=$MAC/build/release

if [[ "${1:-}" == "--fresh" || ! -d build/core ]]; then
  /opt/homebrew/bin/python3.15 scripts/bundle_core.py build/core
fi

xcodegen generate -q
TEAM=$(sed -n 's/.*(\([A-Z0-9]*\))$/\1/p' <<< "$IDENTITY")  # "Developer ID Application: Name (TEAMID)"
xcodebuild -project PhoneUseMac.xcodeproj -scheme PhoneUse -configuration Release -derivedDataPath build \
  CODE_SIGN_IDENTITY="$IDENTITY" CODE_SIGN_STYLE=Manual DEVELOPMENT_TEAM="$TEAM" build -quiet
APP="$OUT/Phone Use.app"
rm -rf "$OUT" && mkdir -p "$OUT"
cp -R "build/Build/Products/Release/Phone Use.app" "$APP"
mkdir -p "$APP/Contents/Resources"
cp -R build/core "$APP/Contents/Resources/core"
find "$APP/Contents/Resources/core" -name __pycache__ -prune -exec rm -rf {} +
# Compile every module now, so it's sealed with the app: the services and the CLI never write .pyc files
# into the bundle (PYTHONDONTWRITEBYTECODE), which would break its signature
"$APP/Contents/Resources/core/python/bin/python3.15" -m compileall -q -j 0 "$APP/Contents/Resources/core" >/dev/null

# Sign from the inside out: every library and program in the core, then the app. Python and UxPlay load
# libraries this team didn't sign, so they get the app's entitlements (library validation off).
TIMESTAMP=--timestamp=none
[[ "$IDENTITY" == Developer\ ID* ]] && TIMESTAMP=--timestamp  # notarization needs a secure timestamp
sign() { codesign --force $TIMESTAMP --options runtime --sign "$IDENTITY" "$@"; }
while IFS= read -r -d '' f; do
  if file -b "$f" | grep -q "Mach-O"; then
    if [[ -x "$f" && "$f" != *.dylib && "$f" != *.so ]]; then sign --entitlements "$ENT" "$f"; else sign "$f"; fi
  fi
done < <(find "$APP/Contents/Resources/core" -type f -print0)
sign --entitlements "$ENT" "$APP"
codesign --verify --deep --strict "$APP" && echo "signed: $(codesign -dvv "$APP" 2>&1 | grep Authority= | head -1)"

# The disk image: the app, a shortcut to Applications to drag it into, and the app's icon as the volume's icon
STAGE="$OUT/dmg"
rm -rf "$STAGE" "$OUT/rw.dmg" && mkdir -p "$STAGE"
ditto "$APP" "$STAGE/Phone Use.app"
ln -s /Applications "$STAGE/Applications"
hdiutil create -quiet -fs HFS+ -volname "Phone Use" -srcfolder "$STAGE" -ov -format UDRW "$OUT/rw.dmg"
MNT=$(hdiutil attach -nobrowse -noautoopen "$OUT/rw.dmg" | awk -F'\t' '/Apple_HFS/ {print $NF}')
ICNS="$APP/Contents/Resources/AppIcon.icns"
if [[ -f "$ICNS" ]]; then
  cp "$ICNS" "$MNT/.VolumeIcon.icns"
  SetFile -c icnC "$MNT/.VolumeIcon.icns"
  SetFile -a C "$MNT"  # the volume has a custom icon
fi
hdiutil detach -quiet "$MNT"
# ULFO (LZFSE) instead of UDZO (zlib): the same image, ~10% smaller and ~40% faster to write.
# The gain is over UDZO's default zlib level; raising that level would erase most of it.
hdiutil convert -quiet "$OUT/rw.dmg" -format ULFO -ov -o "$OUT/PhoneUse.dmg"
rm -rf "$STAGE" "$OUT/rw.dmg"
if [[ "$IDENTITY" == Developer\ ID* ]]; then
  # Notary credentials: `xcrun notarytool store-credentials phoneuse-notary` with the App Store Connect key
  codesign --force --timestamp --sign "$IDENTITY" "$OUT/PhoneUse.dmg"
  xcrun notarytool submit "$OUT/PhoneUse.dmg" --keychain-profile "${NOTARY_PROFILE:-phoneuse-notary}" --wait
  xcrun stapler staple "$OUT/PhoneUse.dmg"
  spctl --assess --type open --context context:primary-signature -v "$OUT/PhoneUse.dmg"
else
  echo "not notarized: signed ad hoc, so it runs on this Mac only (no Developer ID Application certificate)"
fi
echo "$APP"
echo "$OUT/PhoneUse.dmg ($(du -h "$OUT/PhoneUse.dmg" | cut -f1))"
