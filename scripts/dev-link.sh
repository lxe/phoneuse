#!/bin/sh
# Run the installed Phone Use Flatpak from this checkout.
#
# A per-user Flatpak override puts this checkout first on the app's Python path, so the
# app, its services and the `phoneuse` command all run the code here. The skill in
# ~/.claude/skills links to skill/ here. Edits reach the next `phoneuse` command at once;
# the app's own services (mirror, Bluetooth, frames) pick them up when Phone Use restarts.
#
#   scripts/dev-link.sh          link
#   scripts/dev-link.sh --off    back to the installed app's own code and skill
#   scripts/dev-link.sh --status
set -eu
ID=co.lxe.PhoneUse
ROOT=$(cd "$(dirname "$0")/.." && pwd)

if ! flatpak info "$ID" >/dev/null 2>&1; then
    echo "Phone Use isn't installed as a Flatpak ($ID)." >&2
    exit 1
fi

case "${1:-}" in
--status)
    flatpak override --user --show "$ID" | grep -E 'PYTHONPATH|filesystems' || echo "not linked"
    echo "skill: $(readlink "$HOME/.claude/skills/phoneuse" 2>/dev/null || echo 'not a link')"
    ;;
--off)
    flatpak override --user --unset-env=PYTHONPATH "$ID"
    flatpak override --user --nofilesystem="$ROOT" "$ID"
    # The installed app's skill, through the "active" link that follows updates
    files="$(dirname "$(flatpak info --show-location "$ID")")/active/files"
    ln -sfn "$files/share/phoneuse/skill" "$HOME/.claude/skills/phoneuse"
    echo "Unlinked: Phone Use runs its own code again. Restart Phone Use for its services."
    ;;
"")
    flatpak override --user --filesystem="$ROOT:ro" --env=PYTHONPATH="$ROOT" "$ID"
    # The checkout's claude_setup links its skill and writes the `phoneuse` command
    flatpak run --command=python3 "$ID" -c 'from phoneuse import claude_setup; claude_setup.connect(); print(claude_setup.state())'
    echo "Linked: Phone Use runs $ROOT. Restart Phone Use for its services to run it too."
    ;;
*)
    echo "usage: $0 [--off|--status]" >&2
    exit 2
    ;;
esac
