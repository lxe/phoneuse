"""pip install with progress for both halves, for setup-vision.ps1 when the window runs it (PHONEUSE_PROGRESS=raw).

    <venv>\\Scripts\\python.exe -I pip-progress.py <pip install arguments>

pip reports only its downloads (--progress-bar raw), and nothing at all for a wheel it already has in its
cache, or while it unpacks: the CUDA and cuDNN wheels onnxruntime-gpu brings are over 1 GB, a long silence. So:
  1. pip download the wheels into a temporary folder (pip's own "Downloading <file> (<size>)" and
     "Progress <bytes> of <total>" lines)
  2. pip install from that folder alone, printing
         Unpacking <n> packages (<size>)
         Progress <bytes> of <total>        about once a second
     where total is what the wheels hold uncompressed and bytes is how much site-packages has grown
     (held at 99% while pip compiles the bytecode afterwards).
Packages the venv already has are in the total but not installed again, so the last line says done.
"""
import os
import shutil
import subprocess
import sys
import sysconfig
import tempfile
import threading
import zipfile
from pathlib import Path


def size_text(n):
    return f"{n / 1e9:.2f} GB" if n >= 1e9 else f"{n / 1e6:.1f} MB"


def tree_size(root):
    total = 0
    stack = [root]
    while stack:
        try:
            with os.scandir(stack.pop()) as it:
                for e in it:
                    try:
                        if e.is_dir(follow_symlinks=False):
                            stack.append(e.path)
                        else:
                            total += e.stat(follow_symlinks=False).st_size
                    except OSError:
                        pass
        except OSError:
            pass
    return total


def pip(*args):
    # Its output goes straight to ours: the window reads it line by line
    return subprocess.call([sys.executable, "-m", "pip", *args])


def main(args):
    wheels = Path(tempfile.mkdtemp(prefix="phoneuse-wheels-"))
    try:
        code = pip("download", "--progress-bar", "raw", "-d", str(wheels), *args)
        if code:
            return code

        files = list(wheels.glob("*.whl"))
        total = 0
        for w in files:
            with zipfile.ZipFile(w) as z:
                total += sum(i.file_size for i in z.infolist())
        site = sysconfig.get_path("purelib")
        before = tree_size(site)
        print(f"Unpacking {len(files)} packages ({size_text(total)})", flush=True)

        done = threading.Event()
        result = []

        def run():
            # Only from the folder: everything it needs was just downloaded. --index-url and the like
            # don't apply to an install from files, so they're left out.
            rest, skip = [], False
            for a in args:
                if skip:
                    skip = False
                elif a in ("--index-url", "-i", "--extra-index-url"):
                    skip = True
                elif not a.startswith(("--index-url=", "--extra-index-url=")):
                    rest.append(a)
            result.append(pip("install", "--progress-bar", "off", "--no-index", "--find-links", str(wheels), *rest))
            done.set()

        threading.Thread(target=run, daemon=True).start()
        print(f"Progress 0 of {total}", flush=True)
        while not done.wait(1.0):
            # at most 99% until pip is done: after unpacking it compiles the bytecode, which adds more
            print(f"Progress {max(0, min(tree_size(site) - before, total * 99 // 100))} of {total}", flush=True)
        if result[0] == 0:
            print(f"Progress {total} of {total}", flush=True)
        return result[0]
    finally:
        shutil.rmtree(wheels, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
