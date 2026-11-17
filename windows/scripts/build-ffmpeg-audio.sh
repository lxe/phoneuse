#!/bin/bash
# A minimal FFmpeg for the packaged Windows app (windows/scripts/package.ps1): the same version as
# MSYS2's, so GStreamer's libav plugin (gst-libav, built against it) loads it unchanged, but with only
# what UxPlay uses it for: decoding the phone's audio (AAC/AAC-ELD while mirroring, ALAC). MSYS2's own
# build links every codec library there is (x265, aom, SVT-AV1, rav1e, librsvg, ...): ~150 MB of DLLs
# against a few MB here. Same DLL names (avcodec-NN.dll, ...) and ABI: a drop-in for the bundle only.
# MSYS2's FFmpeg in /ucrt64 is left alone.
#
# Run in MSYS2 UCRT64:  bash windows/scripts/build-ffmpeg-audio.sh [PREFIX]   (default ~/ffmpeg-audio)
set -euo pipefail
PREFIX=${1:-$HOME/ffmpeg-audio}
P=mingw-w64-ucrt-x86_64
# Only what's missing: reinstalling the compiler from a newer package database is a partial upgrade
missing=$(for p in make $P-gcc $P-pkgconf; do pacman -Q "$p" >/dev/null 2>&1 || echo "$p"; done)
[ -z "$missing" ] || pacman -S --needed --noconfirm --disable-download-timeout $missing
pacman -Q $P-ffmpeg >/dev/null || { echo "MSYS2's FFmpeg isn't installed (gst-libav brings it): run build-uxplay.sh first" >&2; exit 1; }

# The version gst-libav was built against: MSYS2's FFmpeg package, without its pkgrel ("8.1.2-2")
VER=$(pacman -Q $P-ffmpeg | awk '{print $2}' | sed 's/-[0-9]*$//; s/^[0-9]*://')
SRC=${FFMPEG_SRC:-$HOME/src/ffmpeg-$VER}
if [ -f "$PREFIX/.version" ] && [ "$(cat "$PREFIX/.version")" = "$VER" ]; then
  echo "FFmpeg $VER (audio only) already in $PREFIX"; exit 0
fi
if [ ! -f "$SRC/configure" ]; then
  mkdir -p "$(dirname "$SRC")"
  curl -fsSL "https://ffmpeg.org/releases/ffmpeg-$VER.tar.xz" | tar -xJ -C "$(dirname "$SRC")"
fi
cd "$SRC"
make distclean >/dev/null 2>&1 || true
# gst-libav links avcodec, avformat, avfilter and avutil (and avcodec swresample): all of them stay,
# nearly empty. --disable-autodetect: nothing external (no zlib, iconv, schannel, media foundation).
./configure --prefix="$PREFIX" --enable-shared --disable-static \
  --disable-programs --disable-doc --disable-network --disable-autodetect --disable-debug \
  --disable-everything --enable-w32threads --disable-x86asm \
  --disable-avdevice --disable-swscale \
  --enable-decoder=aac,aac_latm,alac --enable-parser=aac,aac_latm \
  --enable-protocol=file --enable-filter=aresample,anull,null \
  --extra-ldflags=-static-libgcc > configure.out 2>&1 || { tail -20 configure.out >&2; exit 1; }
make -j"$(nproc)" > make.out 2>&1 || { tail -30 make.out >&2; exit 1; }
rm -rf "$PREFIX"
make install >/dev/null
echo "$VER" > "$PREFIX/.version"
echo "FFmpeg $VER (audio only) in $(cygpath -w "$PREFIX")"
ls -la "$PREFIX/bin"/*.dll
