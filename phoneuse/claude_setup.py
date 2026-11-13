"""Connect Claude Code to Phone Use: the skill and the `phoneuse` command.

Agents use Phone Use through the `phoneuse` command, guided by the skill. Both point at
this copy of Phone Use, so they change when it does:
  * the skill is a link: to a checkout's skill/ folder (or the Mac app's), or inside the
    Flatpak to the installed app's skill through Flatpak's "active" path, which follows
    updates;
  * ~/.local/bin/phoneuse runs this copy's CLI (inside the Flatpak: `flatpak run`).
A Flatpak pointed at a checkout (scripts/dev-link.sh) runs the checkout's code, so it
links the checkout's skill, and edits there reach agents at once.

state() says which pieces are in place; connect() and disconnect() add or remove both.
refresh() redoes connect() for someone who connected before (the app runs it at start),
so older setups catch up: a copied skill, or the MCP server Phone Use used to add.
Nothing else in ~/.claude or ~/.claude.json changes.
"""
import json
import os
import shutil

HOME = os.path.expanduser("~")
SKILL_DIR = os.path.join(HOME, ".claude", "skills", "phoneuse")
CLAUDE_JSON = os.path.join(HOME, ".claude.json")
BIN = os.path.join(HOME, ".local", "bin", "phoneuse")
PKG = os.path.dirname(os.path.realpath(__file__))
FLATPAK_ID = os.environ.get("FLATPAK_ID")
MARK = "# Phone Use"  # the first comment line of the command we write, so we only ever remove our own


def _flatpak_files():
    """The installed app's files as the host sees them, through the "active" link that
    follows updates (/.flatpak-info's app-path names one version)."""
    try:
        with open("/.flatpak-info") as f:
            path = next(line.split("=", 1)[1].strip() for line in f if line.startswith("app-path="))
    except (OSError, StopIteration):
        return None
    parts = path.rstrip("/").split("/")  # .../app/ID/ARCH/BRANCH/COMMIT/files
    if len(parts) < 3 or parts[-1] != "files":
        return None
    return "/".join(parts[:-2] + ["active", "files"])


def _skill_source():
    """Where the skill link points, as the host sees it, or None."""
    here = os.path.join(os.path.dirname(PKG), "skill")
    if not PKG.startswith("/app/") and os.path.exists(os.path.join(here, "SKILL.md")):
        return here  # a checkout or the Mac app's bundle: the same path outside
    if FLATPAK_ID and os.path.exists("/app/share/phoneuse/skill/SKILL.md"):
        files = _flatpak_files()
        return os.path.join(files, "share", "phoneuse", "skill") if files else None
    return None


def _cli_script():
    target = (f"flatpak run --command=phoneuse {FLATPAK_ID}" if FLATPAK_ID
              else os.path.join(os.path.dirname(PKG), "bin", "phoneuse"))
    return f"#!/bin/sh\n{MARK}: lets agents (and you) run phoneuse commands.\nexec {target} \"$@\"\n"


def _read_json():
    try:
        with open(CLAUDE_JSON) as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


def _write_json(d):
    # In place: inside the Flatpak only this one file is visible, not its directory, so a
    # temporary file beside it can't be made. The text is made first, so a failure leaves
    # the old file as it was.
    text = json.dumps(d, indent=2)
    with open(CLAUDE_JSON, "w") as f:
        f.write(text)


def _ours(path):
    try:
        with open(path) as f:
            return MARK in f.read()
    except OSError:
        return False


def state():
    """{"skill", "command"}: each True when that piece is in place and points at this copy
    of Phone Use, and "claude": whether Claude Code looks installed (~/.claude exists)."""
    src = _skill_source()
    # Compare the link's text: inside the Flatpak its target isn't visible.
    skill_ok = bool(src) and os.path.islink(SKILL_DIR) and os.readlink(SKILL_DIR) == src
    try:
        with open(BIN) as f:
            cmd_ok = f.read() == _cli_script()
    except OSError:
        cmd_ok = False
    return {"claude": os.path.isdir(os.path.join(HOME, ".claude")), "skill": skill_ok, "command": cmd_ok}


def connected():
    s = state()
    return s["skill"] and s["command"]


def _remove_skill():
    if os.path.islink(SKILL_DIR) or os.path.isfile(SKILL_DIR):
        os.remove(SKILL_DIR)
    elif os.path.isdir(SKILL_DIR):
        shutil.rmtree(SKILL_DIR)


def _remove_old_mcp():
    """Phone Use used to add an MCP server; agents now use the command."""
    d = _read_json()
    if (d.get("mcpServers") or {}).pop("phoneuse", None) is not None:
        _write_json(d)


def connect():
    src = _skill_source()
    if not src:
        raise RuntimeError("the skill isn't in this copy of Phone Use")
    _remove_skill()
    os.makedirs(os.path.dirname(SKILL_DIR), exist_ok=True)
    os.symlink(src, SKILL_DIR)
    _remove_old_mcp()
    os.makedirs(os.path.dirname(BIN), exist_ok=True)
    if os.path.islink(BIN) or os.path.exists(BIN):
        os.remove(BIN)
    with open(BIN, "w") as f:
        f.write(_cli_script())
    os.chmod(BIN, 0o755)


def refresh():
    """At start: for someone who connected before (our command, the skill or our old MCP
    entry is there), connect again when a piece is missing or points elsewhere. Never
    connects someone who hasn't."""
    try:
        before = _ours(BIN) or os.path.lexists(SKILL_DIR) or \
            "phoneuse" in (_read_json().get("mcpServers") or {})
        if before and (not connected() or "phoneuse" in (_read_json().get("mcpServers") or {})):
            connect()
            return True
    except (OSError, RuntimeError, ValueError):
        pass
    return False


def disconnect():
    _remove_skill()
    _remove_old_mcp()
    if _ours(BIN):
        os.remove(BIN)
