#!/usr/bin/env python3
"""Make every app icon from the one master image, packaging/icons/app/phone-use-icon.png
(1024 x 1024, full bleed, opaque). Run it after changing the master; it needs ImageMagick (magick).

  iOS:    ios/App/Assets.xcassets/AppIcon.appiconset/icon-1024.png
          the square itself, opaque: iOS draws the rounded mask.
  macOS:  mac/PhoneUse/Assets.xcassets/AppIcon.appiconset/icon-<px>.png (16 to 1024)
          Apple's macOS shape: an 824 x 824 rounded square inside a transparent 1024 canvas, with a
          soft shadow.
  Linux:  packaging/icons/hicolor/<n>x<n>/apps/co.lxe.PhoneUse.png (64 to 512)
          a rounded square with GNOME's margin (8 of every 128), transparent around it.
  Web:    phoneuse/web/favicon-32.png, favicon-64.png (the Linux shape) and apple-touch-icon.png
          (180, opaque: iOS rounds it).
  Windows: windows/PhoneUseMirror/Assets/AppIcon.png (256) and AppIcon.ico (16 to 256), the Linux shape:
          Windows draws an icon as it is, so the rounding has to be in the image.
Every PNG is then compressed with pngquant (quality 92-100), if it's installed.
"""
import glob
import json
import os
import shutil
import subprocess
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MASTER = os.path.join(ROOT, "packaging", "icons", "app", "phone-use-icon.png")


def magick(*args):
    subprocess.run(["magick", *args], check=True)


def rounded(out, inset, radius, shadow=False):
    """The master scaled into a rounded square, `inset` from each edge of a transparent 1024 canvas."""
    size = 1024 - 2 * inset
    with tempfile.TemporaryDirectory() as d:
        mask = os.path.join(d, "mask.png")
        art = os.path.join(d, "art.png")
        magick("-size", f"{size}x{size}", "xc:black", "-fill", "white",
               "-draw", f"roundrectangle 0,0 {size - 1},{size - 1} {radius},{radius}", mask)
        magick(MASTER, "-resize", f"{size}x{size}", mask, "-alpha", "off", "-compose", "CopyOpacity",
               "-composite", art)
        layers = ["-size", "1024x1024", "xc:none"]
        if shadow:
            layers += ["(", art, "-background", "black", "-shadow", "30x14+0+10", ")",
                       "-gravity", "center", "-geometry", "+0+10", "-compose", "over", "-composite"]
        magick(*layers, art, "-gravity", "center", "-compose", "over", "-composite", out)


def sizes(src, out, px):
    magick(src, "-filter", "Lanczos", "-resize", f"{px}x{px}", out)


def main():
    ios = os.path.join(ROOT, "ios", "App", "Assets.xcassets", "AppIcon.appiconset", "icon-1024.png")
    magick(MASTER, "-resize", "1024x1024", "-background", "white", "-alpha", "remove", "-alpha", "off", ios)

    with tempfile.TemporaryDirectory() as d:
        mac_full = os.path.join(d, "mac.png")
        rounded(mac_full, 100, 185, shadow=True)
        mac = os.path.join(ROOT, "mac", "PhoneUse", "Assets.xcassets", "AppIcon.appiconset")
        os.makedirs(mac, exist_ok=True)
        images = []
        for pt in (16, 32, 128, 256, 512):
            for scale in (1, 2):
                px = pt * scale
                sizes(mac_full, os.path.join(mac, f"icon-{px}.png"), px)
                images.append({"filename": f"icon-{px}.png", "idiom": "mac", "scale": f"{scale}x",
                               "size": f"{pt}x{pt}"})
        with open(os.path.join(mac, "Contents.json"), "w") as f:
            json.dump({"images": images, "info": {"author": "xcode", "version": 1}}, f, indent=2)
        with open(os.path.join(os.path.dirname(mac), "Contents.json"), "w") as f:
            json.dump({"info": {"author": "xcode", "version": 1}}, f, indent=2)

        linux_full = os.path.join(d, "linux.png")
        rounded(linux_full, 64, 200)
        for px in (64, 128, 256, 512):
            out = os.path.join(ROOT, "packaging", "icons", "hicolor", f"{px}x{px}", "apps", "co.lxe.PhoneUse.png")
            os.makedirs(os.path.dirname(out), exist_ok=True)
            sizes(linux_full, out, px)
        web = os.path.join(ROOT, "phoneuse", "web")
        for px in (32, 64):
            sizes(linux_full, os.path.join(web, f"favicon-{px}.png"), px)
        magick(MASTER, "-filter", "Lanczos", "-resize", "180x180", "-alpha", "off",
               os.path.join(web, "apple-touch-icon.png"))
        win = os.path.join(ROOT, "windows", "PhoneUseMirror", "Assets")
        sizes(linux_full, os.path.join(win, "AppIcon.png"), 256)
        magick(linux_full, "-filter", "Lanczos", "-define", "icon:auto-resize=256,64,48,40,32,24,20,16",
               os.path.join(win, "AppIcon.ico"))
    outputs = (glob.glob(os.path.join(ROOT, "ios", "App", "Assets.xcassets", "AppIcon.appiconset", "*.png"))
               + glob.glob(os.path.join(ROOT, "mac", "PhoneUse", "Assets.xcassets", "AppIcon.appiconset", "*.png"))
               + glob.glob(os.path.join(ROOT, "packaging", "icons", "hicolor", "*", "apps", "co.lxe.PhoneUse.png"))
               + glob.glob(os.path.join(ROOT, "phoneuse", "web", "favicon-*.png"))
               + [os.path.join(ROOT, "phoneuse", "web", "apple-touch-icon.png"),
                  os.path.join(ROOT, "windows", "PhoneUseMirror", "Assets", "AppIcon.png")])
    if shutil.which("pngquant"):  # the full-size PNGs are ~2 MB each uncompressed
        subprocess.run(["pngquant", "--quality=92-100", "--speed", "1", "--strip", "--force", "--ext", ".png",
                        *outputs], check=False)
    print("icons written")


if __name__ == "__main__":
    main()
