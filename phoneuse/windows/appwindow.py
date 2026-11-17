"""The mirror window on Windows: the browser mirror in a window of its own (Edge or
Chrome in app mode, no tabs or address bar), sized like the phone. It is an
ordinary window: move it, resize it (the picture stays aspect-fit), close it;
`phoneuse view` opens it again."""
import ctypes
import os
import shutil
import subprocess

from ..config import CFG, PHONE_ASPECT, runtime_path

BROWSERS = [
    r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe",
    r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe",
    r"%ProgramFiles%\Google\Chrome\Application\chrome.exe",
    r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe",
    r"%LocalAppData%\Google\Chrome\Application\chrome.exe",
]
BEZEL = 30 / 498  # as screen.BEZEL


def browser():
    for p in BROWSERS:
        p = os.path.expandvars(p)
        if os.path.exists(p):
            return p
    return shutil.which("msedge") or shutil.which("chrome")


def window_size():
    """Phone-shaped, about 85% of the work area's height (in CSS pixels)."""
    try:
        user32 = ctypes.WinDLL("user32")
        dpi = user32.GetDpiForSystem() or 96
        height = user32.GetSystemMetrics(17)  # SM_CYFULLSCREEN: the work area's height
        height = int(height * 96 / dpi * 0.85)
    except (OSError, AttributeError):
        height = 900
    dw, dh = 1 + 2 * BEZEL, 1 / PHONE_ASPECT + 2 * BEZEL
    return int(height * dw / dh), height + 30  # + the title bar


_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# A checkout's build (windows\scripts\build-winui.ps1), or in the installed/portable app
# (windows/scripts/package.ps1) the exe beside core\, which is this phoneuse's root.
NATIVE = [os.path.join(_ROOT, "windows", "dist", "PhoneUseMirror.exe"),
          os.path.join(os.path.dirname(_ROOT), "PhoneUseMirror.exe")]


def native_window():
    """The native mirror window (WinUI 3, windows/PhoneUseMirror/), when it's built and not turned off
    (window = "browser" in config.toml)."""
    if CFG.get("window", "auto") == "browser":
        return None
    return next((p for p in NATIVE if os.path.exists(p)), None)


def open_window(url=None):
    """Open the mirror window: the native one when it's built (windows\\scripts\\build-winui.ps1),
    else the browser mirror in an Edge/Chrome app window. Returns False when neither is
    available (the caller then opens the page in the default browser)."""
    exe = native_window()
    if exe:
        subprocess.Popen([exe, "--port", str(CFG["web_port"])],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         creationflags=0x00000008 | 0x00000200)  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
        return True
    url = url or f"http://localhost:{CFG['web_port']}/"
    exe = browser()
    if not exe:
        return False
    w, h = window_size()
    # A profile of its own makes it a separate browser instance: the window gets
    # the requested size and doesn't join the user's browsing session.
    profile = runtime_path("window-profile")
    subprocess.Popen([exe, f"--app={url}", f"--window-size={w},{h}", f"--user-data-dir={profile}",
                      "--no-first-run", "--no-default-browser-check", "--disable-features=Translate"],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     creationflags=0x00000008 | 0x00000200)  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    return True
