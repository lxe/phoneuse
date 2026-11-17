"""Old backends on Windows: what a new `phoneuse up` (or the Phone Use window's background
start) clears away before it starts its own services.

A backend can outlive what it was for: the window or a terminal that started it was killed
without its job object closing, an agent ran `phoneuse up` again, or a checkout's copy was
left running beside the installed app's. Its services keep the fixed ports
(config.SERVICE_PORTS, the browser mirror's, AirPlay's), so the new copies can't start, and
if they've stopped answering, every command finds phoneuse "not running" while it is.

  * Another `phoneuse up` that still answers is left alone (up_windows says it's running).
  * One that doesn't is stopped, with its services, and so are services no running `up`
    owns (an orphaned video hub, frames service, browser mirror or UxPlay): stop_old.
  * The Bluetooth HID service runs on its own on purpose (it outlives `up`): only a copy
    that doesn't answer is stopped, and extra copies that don't hold its port.

Processes are matched by command line, from any install or checkout of phoneuse, never
this process or the ones that started it. The OCR service (vision) is shared by agents'
commands and left alone.
"""
import json
import os
import re
import signal
import subprocess
import time

from ..config import CFG, SERVICE_PORTS

UP = re.compile(r"-m\s+phoneuse\.windows\.background\b|-m\s+phoneuse\s+(up|serve)\b|[\\/]phoneuse\"?\s+(up|serve)\b")
SERVICES = re.compile(r"-m\s+phoneuse\.(avhub|frames|web)\b")
HID = re.compile(r"-m\s+phoneuse\.windows\.hid\b")
AIRPLAY_PORTS = (7000, 7100)

_QUERY = r"""
$p = Get-CimInstance Win32_Process -Filter "Name like 'python%' or Name = 'uxplay.exe'" |
  ForEach-Object { @{ pid = [int]$_.ProcessId; ppid = [int]$_.ParentProcessId; name = $_.Name; cmd = "$($_.CommandLine)" } }
$ports = @{}
foreach ($port in @(PORTS)) {
  $c = Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue | Select-Object -First 1
  if ($c) { $ports["$port"] = [int]$c.OwningProcess }
}
@{ procs = @($p); ports = $ports } | ConvertTo-Json -Compress -Depth 4
"""


def _snapshot():
    """phoneuse's processes (python and uxplay, with their command lines) and who listens on
    its ports: ({pid: proc}, {port: pid}). Empty when PowerShell can't say."""
    ports = [*SERVICE_PORTS.values(), int(CFG["web_port"]), *AIRPLAY_PORTS]
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                              _QUERY.replace("PORTS", ",".join(str(p) for p in ports))],
                             capture_output=True, text=True, timeout=20,
                             creationflags=0x08000000).stdout  # no console window
        data = json.loads(out or "{}")
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return {}, {}
    procs = {p["pid"]: p for p in data.get("procs") or [] if isinstance(p, dict)}
    return procs, {int(k): v for k, v in (data.get("ports") or {}).items()}


def _mine(procs):
    """This process and the ones that started it (a venv's python.exe starts the real one)."""
    pid, seen = os.getpid(), set()
    while pid and pid not in seen:
        seen.add(pid)
        pid = procs.get(pid, {}).get("ppid")
    return seen


def _alive(pid):
    import ctypes
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    h = k.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE
    if not h:
        return False
    try:
        return k.WaitForSingleObject(h, 0) == 0x102  # WAIT_TIMEOUT: still running
    finally:
        k.CloseHandle(h)


def _kill(pids, say):
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)  # TerminateProcess
        except OSError:
            pass
    deadline = time.time() + 5
    while time.time() < deadline and any(_alive(p) for p in pids):
        time.sleep(0.1)
    left = [p for p in pids if _alive(p)]
    if left:
        say(f"couldn't stop {', '.join(map(str, left))}")


def _describe(p):
    m = re.search(r"-m\s+(phoneuse[\w.]*)(?:\s+(\w+))?", p["cmd"]) or re.search(r"phoneuse\"?\s+(\w+)", p["cmd"])
    return p["name"] if p["name"].lower() == "uxplay.exe" else (" ".join(g for g in m.groups() if g) if m else p["cmd"][:60])


def stop_old(say, hid_answers):
    """Stop old backends (see the module's docstring): every other `phoneuse up` (called when
    this one holds the lock, or the one holding it doesn't answer: either way the others are
    stale), the services no `up` of this one's owns, and HID copies (all of them, or with
    hid_answers only the extra ones). Returns how many processes were stopped."""
    procs, ports = _snapshot()
    if not procs:
        return 0
    mine = _mine(procs)
    hid_owner = ports.get(SERVICE_PORTS["hid"])
    hid_keep = set()
    if hid_answers and hid_owner:  # the one answering, and the venv launcher that started it
        hid_keep = {hid_owner, procs.get(hid_owner, {}).get("ppid")}

    ups, services, hids = [], [], []
    for pid, p in procs.items():
        if pid in mine:
            continue
        cmd = p["cmd"]
        if UP.search(cmd):
            ups.append(pid)
        elif SERVICES.search(cmd) or p["name"].lower() == "uxplay.exe":
            services.append(pid)
        elif HID.search(cmd) and pid not in hid_keep:
            hids.append(pid)
    # The old `up`s first, so none of them starts its services again while they go.
    stopped = 0
    for group, what in ((ups, "phoneuse up"), (services, "service"), (hids, "Bluetooth HID service")):
        if not group:
            continue
        say(f"stopping an old {what}: " + ", ".join(f"{_describe(procs[p])} (pid {p})" for p in group))
        _kill(group, say)
        stopped += len(group)
    return stopped
