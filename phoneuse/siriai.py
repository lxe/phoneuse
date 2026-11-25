"""Ask Apple Intelligence over the existing mirror and Bluetooth input paths."""

import argparse
import re
import time

from . import cli, hidproto, orientation, phones, touch


# Each model but Siri is reached through its own shortcut. Shortcuts' "Use Model" action only sees the text
# it is given and can't run app actions by itself (WWDC26 "What's new in Shortcuts"), so personal context
# and app intents come only through Siri. The shortcuts share one input prompt and one answer title.
INPUT = "phoneuse model prompt"
OUTPUT = "phoneuse model answer"
SIRI = "siri-plus-context"
SHORTCUTS = {"on-device": "PhoneUse On-Device", "cloud": "PhoneUse Cloud", "cloud-pro": "PhoneUse Cloud Pro"}
# Use Model's menu on iOS 27, read on 2026-11-24: Cloud ("Great, fast answers"), Cloud Pro ("Increased
# reasoning"), On-Device ("Works offline"), ChatGPT, Ask Each Time.
MODEL_MENU = {"on-device": "On-Device", "cloud": "Cloud", "cloud-pro": "Cloud Pro"}
MODES = {
    SIRI: "Siri itself, typed to in the Siri app. The only mode with the owner's personal context (messages, "
          "mail, calendar, what's on screen) and app intents, so it can also act in apps. Use it when the answer "
          "needs the owner's data or an action; anything it would send, buy or change needs the owner's OK first.",
    "on-device": 'The small model on the iPhone, through the "PhoneUse On-Device" shortcut. Private and quickest. '
                 "Sees only the prompt: no personal context, no app actions. Good for rewriting, summarizing "
                 "or classifying text you pass in.",
    "cloud": "Apple's Private Cloud Compute model, through the \"PhoneUse Cloud\" shortcut. Larger and private, "
             "may look things up on the web. Sees only the prompt. Daily usage limits apply.",
    "cloud-pro": "Apple's largest cloud model, through the \"PhoneUse Cloud Pro\" shortcut. For hard reasoning; "
                 "slower, with the tightest daily limits. Sees only the prompt.",
}
MODES_HELP = "Choose a model with one flag. No prompt was sent.\n" + "\n".join(
    f"  --{mode}: {text}" for mode, text in MODES.items())
DISMISS = ("done", "ok")
SIRI_FIELDS = {"ask siri", "search or ask", "type to siri"}
SIRI_CHROME = SIRI_FIELDS | {"siri", "thinking", "searching", "working", "stop", "sources"}


def normalized(text):
    return " ".join(text.casefold().split())


def target(lines, *labels):
    labels = {normalized(label) for label in labels}
    matches = [(x, y) for x, y, text in lines if normalized(text) in labels]
    return matches[0] if len(matches) == 1 else None


def picture_ready():
    try:
        state = cli.picture_state()
    except (OSError, ValueError):
        cli.die("Can't verify the iPhone mirror. Run `phoneuse status`.")
    if state.get("paused"):
        cli.die("PAUSED: the user took over the phone. Wait for them to resume.")
    return state.get("live") and not state.get("stale")


def steady(value):
    """A value as compared for `quiet`: OCR positions wobble by about 0.001 between reads of an
    unchanged screen (0.611 vs 0.612 on 2026-11-24), so positions count to 0.01."""
    if isinstance(value, float):
        return round(value, 2)
    if isinstance(value, tuple):
        return tuple(steady(v) for v in value)
    return value


def wait_for(find, timeout, failure, quiet=0):
    """The first value find() gives a settled screen; stops the run with `failure` after `timeout` s,
    or returns None then when failure is None."""
    deadline = time.monotonic() + timeout
    prior, since, stale_since = None, None, None
    while (now := time.monotonic()) < deadline:
        began = now
        if picture_ready():
            stale_since = None
            value = find(cli.screen_text())
            now = time.monotonic()
            if steady(value) != steady(prior):
                prior, since = value, now
            if value and now <= deadline and now - since >= quiet and picture_ready():
                return value
        else:
            prior = None
            stale_since = now if stale_since is None else stale_since
            if now - stale_since >= 2:
                cli.die("The iPhone mirror stopped updating. Check it before retrying the request.")
        # One look every 0.35 s at most. A screen read itself took 0.6 to 0.8 s (2026-11-24), and sleeping a
        # further 0.35 s after it added about 4 s to a run.
        pause = min(0.35 - (time.monotonic() - began), deadline - time.monotonic())
        if pause > 0:
            time.sleep(pause)
    if failure is None:
        return None
    cli.die(failure)


def tap(h, pos):
    changed = cli.moved_since_look(*pos)
    if changed or not picture_ready():
        cli.die("The target changed or the mirror stalled. Inspect the screen before retrying.")
    h.first_seq = None
    touch.tap(h, *pos)
    cli.acted(h.first_seq)


def open_app(h, name):
    """Open an app the way `phoneuse open` does (Spotlight). The prompt itself never goes into
    Spotlight: on 2026-11-24 Return in its "Search or Ask" bar opened the top hit (a Mail message)
    instead of asking Siri."""
    cli.refuse_if_locked()
    cli.open_app(h, name)


# The Conversations list's header: y 0.160 on an iPhone 17 Pro (2026-11-25), higher on larger phones
CONVERSATIONS_TOP = 0.2


def siri_field(lines):
    if cli.spotlight_open(lines):
        return None  # Spotlight can display the same placeholder.
    return target([line for line in lines if line[1] > 0.5], *SIRI_FIELDS)


def keyboard_up(lines):
    """The on-screen keyboard: its letter rows read as ten or more single letters low on the screen."""
    return sum(1 for _, y, text in lines if 0.6 < y < 0.9 and len(text.strip()) == 1 and text.strip().isalpha()) >= 10


def siri_draft(lines):
    """The composer when text is already in it, so its "Ask Siri" placeholder is gone: a failed run left
    its prompt there (2026-11-24). Its + button reads as one short token at the row's left end (x 0.099,
    y 0.612) beside the draft's last line ("number only."); OCR missed the send arrow at the right end
    in some reads. A keyboard row has ten keys, and its bottom row ("123", space) holds no text. The
    tap goes to the field; typing then replaces the old text."""
    if cli.spotlight_open(lines) or target([line for line in lines if line[1] < CONVERSATIONS_TOP], "conversations"):
        return None
    for x, y, text in lines:
        if not (x < 0.15 and 0.5 < y < 0.97 and len(text.strip()) <= 2):
            continue
        row = [line for line in lines if abs(line[1] - y) < 0.015]
        if len(row) <= 4 and any(0.15 < rx < 0.85 and len(rtext.strip()) > 2 for rx, _, rtext in row):
            return 0.5, y
    # OCR can miss the + button and read only the send arrow at the row's right end ("个" at x 0.892,
    # y 0.598 on an iPhone 17 Pro, 2026-11-25). Only with the keyboard up, so a reply's line can't pass.
    if keyboard_up(lines):
        for x, y, text in lines:
            if not (x > 0.85 and 0.5 < y < 0.97 and len(text.strip()) <= 2):
                continue
            row = [line for line in lines if abs(line[1] - y) < 0.015]
            if len(row) <= 3 and any(0.15 < rx < 0.85 and len(rtext.strip()) > 2 for rx, _, rtext in row):
                return 0.5, y
    return None


def siri_ready(lines):
    """The Siri app's composer, or the compose button when it reopened on its Conversations list
    (it does when the last conversation was left there). OCR reads the compose icon as "0"; it sits
    alone in the bottom-right corner (0.87, 0.94 on 2026-11-24)."""
    field = siri_field(lines) or siri_draft(lines)
    if field:
        return "field", field
    if target([line for line in lines if line[1] < CONVERSATIONS_TOP], "conversations"):
        corner = [line for line in lines if line[0] > 0.7 and line[1] > 0.88]
        if len(corner) == 1 and len(corner[0][2].strip()) <= 2:
            return "compose", corner[0][:2]
    return None


def matching_prompt(lines, prompt):
    """Match an OCR prompt that may span several consecutive lines."""
    want = normalized(prompt)
    for start in range(len(lines)):
        seen = ""
        for end in range(start, len(lines)):
            seen = (seen + " " + normalized(lines[end][2])).strip()
            # The text cursor after a typed prompt read as a trailing "l" (2026-11-24: "number only.l")
            if seen == want or (seen[:-1] == want and seen[-1] in "|li"):
                yield start, end
                break
            if not want.startswith(seen + " "):
                break


def siri_reply(lines, prompt, draft):
    if cli.spotlight_open(lines):
        return None
    draft_y, before = draft
    prompts = [(start, end) for start, end in matching_prompt(lines, prompt)
               if lines[start][1] < draft_y - 0.035]
    if not prompts:
        return None
    # Only read below the submitted question. Existing conversations and input
    # suggestions were visible before submission and do not count as a reply.
    after = lines[prompts[-1][1] + 1:]
    composer_y = next((y for _, y, text in after if y > 0.5 and normalized(text) in SIRI_FIELDS), draft_y)
    return "\n".join(text for _, y, text in after if y < composer_y - 0.01
                     if normalized(text).rstrip(".…") not in SIRI_CHROME and normalized(text) not in before)


def alert_body(lines, title):
    heading = target(lines, title)
    if not heading:
        return []
    done = button_below(lines, heading)
    if not done:
        return []
    # Run from its summary page (iOS 27, 2026-11-24) the alert sits at the top with left-aligned text: a
    # one-word answer ("144") was centred at x 0.115.
    return [line for line in lines if heading[1] < line[1] < done[1] and 0.04 < line[0] < 0.96]


def button_below(lines, heading):
    """Show Alert's button reads OK or Done depending on where the shortcut runs."""
    return target([line for line in lines if line[1] > heading[1]], *DISMISS)


def shortcut_spec(mode):
    """What the mode's shortcut must be: its name and its three actions with every field the editor
    shows, as the iOS 27 editor read on 2026-11-24."""
    # Cloud and Cloud Pro also show "Use Broad World Knowledge"; the owner wants it on (2026-11-24)
    knowledge = "" if mode == "on-device" else "Use Broad World Knowledge on, "
    return (f'Name: {SHORTCUTS[mode]} (matched ignoring case)\n'
            f'  1. Ask for Input: Input Text, Prompt "{INPUT}"\n'
            f'  2. Use Model: Model {MODEL_MENU[mode]}, Request the "Provided Input" variable, {knowledge}'
            "Output Automatic, Follow Up off\n"
            f'  3. Show Alert: the "Response" variable, Title "{OUTPUT}", Show Cancel Button off\n'
            "  Nothing else: no other actions, and no steps before Ask for Input.")


def describe_shortcut(mode):
    """The sentence for iOS 27's "Describe a shortcut" box. On 2026-11-24 this wording built the on-device
    shortcut exactly as shortcut_spec describes it."""
    knowledge = "" if mode == "on-device" else ", broad world knowledge on"
    return (f'Ask for text input with the prompt "{INPUT}", send the provided input to the '
            f'{MODEL_MENU[mode]} model with Use Model (follow up off{knowledge}), then show an alert titled "{OUTPUT}" '
            "whose message is the model response, with no cancel button.")


def create_steps(mode):
    name = SHORTCUTS[mode]
    return (f'The "{name}" shortcut isn\'t on this iPhone. No prompt was sent.\n'
            f"To use --{mode}, the shortcut has to exist. Ask the owner whether they want to make it or want you "
            "to make it with the phone tools. The shortcut must be exactly:\n"
            f"{shortcut_spec(mode)}\n"
            "To make it:\n"
            "1. In Shortcuts, on All Shortcuts, tap + at the bottom. A \"Describe a shortcut\" field opens.\n"
            f"2. Type this into it, then press Return:\n   {describe_shortcut(mode)}\n"
            "3. Tap Edit. Expand each action (its > button) and check every field against the spec above. "
            "Fix any that differ, or add the actions by hand if the description didn't build them.\n"
            f'4. Tap the name at the top, choose Rename, select all and type "{name}", then press Return.\n'
            "5. Tap back to All Shortcuts and run siriai again.\n"
            "Other modes don't need it: " + ", ".join(f"--{m}" for m in MODES if m != mode) + ".")


def open_shortcut(h, mode):
    """Run the mode's shortcut from the Shortcuts app: its tile in the grid, or else the app's search.

    On 2026-11-24 the grid showed every tile and the app's Search field found a tile by its exact name.
    The tile is matched by its whole OCR text, so "PhoneUse Cloud"
    never picks "PhoneUse Cloud Pro"."""
    name = SHORTCUTS[mode]

    def tile_search_or_old_alert(lines):
        if alert_body(lines, OUTPUT):
            return "dismiss", button_below(lines, target(lines, OUTPUT))
        play = summary_play(lines)
        if play:
            # Shortcuts reopens where it was left: a summary page, titled with its shortcut's name
            title = target([line for line in lines if line[1] < 0.12], name)
            return ("run", play) if title else ("dismiss", back_button())
        # Not the title bar: a summary or editor page is titled with the shortcut's name
        tile = target([line for line in lines if line[1] > 0.12], name)
        if tile:
            return "open", tile_play(lines, tile)
        # Only All Shortcuts' own search: on 2026-11-24 a run typed the name into a shortcut editor's
        # action search and reported the shortcut missing.
        heading = target([line for line in lines if line[1] < 0.2], "all shortcuts")
        if not heading:
            return None
        search = target([line for line in lines if heading[1] < line[1] < 0.35], "search", "q search")
        if search:
            return "search", search
        # On 2026-11-24 All Shortcuts with two tiles opened with its Search field scrolled away under the
        # title: the whole grid fit on screen, so a tile that isn't on it doesn't exist.
        return "missing", heading

    lost = ("Shortcuts didn't show All Shortcuts (it may have an editor or a folder open). "
            "Go back to All Shortcuts and retry. No prompt was sent.")
    action, pos = wait_for(tile_search_or_old_alert, 5, lost, quiet=0.5)
    if action == "dismiss":
        tap(h, pos)
        action, pos = wait_for(tile_search_or_old_alert, 5, lost, quiet=0.5)
    if action == "missing":
        cli.die(create_steps(mode))
    if action == "run":
        tap(h, pos)
        return wait_for_input(name)
    if action == "search":
        tap(h, pos)
        # The focused field moves to the top of the screen, above the results.
        # Never the action library, whose search field looks the same (its category chips include Scripting)
        wait_for(lambda lines: not target(lines, "scripting")
                 and target([line for line in lines if line[1] < 0.12], "search", "q search"), 5, lost)
        cli.type_text(h, name)
        # Below the field only, since OCR may read the field's own text as the bare name.
        pos = wait_for(lambda lines: target([line for line in lines if line[1] > 0.12], name), 5, create_steps(mode))
    tap(h, pos)

    def input_or_summary(lines):
        if target(lines, INPUT):
            return "input", None
        play = summary_play(lines)
        return ("summary", play) if play else None

    # No settling here (it cost 2.2 s): the input is read straight away, and a summary page's play button
    # is only tapped if the screen around it held still since this look (tap()).
    action, pos = wait_for(input_or_summary, 5, f'"{name}" opened neither its input nor its summary page. '
                           "No prompt was sent.")
    if action == "summary":
        tap(h, pos)
        wait_for_input(name)


# Shortcuts' unlabeled buttons, measured on 2026-11-24 on an iPhone 18 Pro Max (440 x 956 points) and kept
# in points from the screen's edges: iOS lays these out with fixed margins, so on a narrower phone (393
# points for an iPhone 15 Pro) a fraction measured on this one would land about 10 points off.
def points(x, y):
    """Screen fractions for a position in points from the top-left corner of the phone's screen."""
    width, height = orientation.current_points()
    return x / width, y / height


def back_button():
    """The back button at the top left of a Shortcuts page: 42 x 84 points."""
    return points(42, 84)


def tile_play(lines, tile):
    """The play button of a tile in the All Shortcuts grid, which runs the shortcut without the summary
    page (about 2.5 s). Tiles fill two columns between 20-point margins with a 10-point gutter; the
    button's centre was 30 points in from its tile's right edge and 53.5 points above the bottom line of
    the tile's name, in both rows and columns measured. A name may wrap onto a second line just below
    its first."""
    width, height = orientation.current_points()
    left = tile[0] < 0.5
    bottom = max([tile[1]] + [y for x, y, _ in lines if (x < 0.5) == left and 0 < y - tile[1] < 0.035])
    right_edge = 20 + (width - 50) / 2 if left else width - 20
    return points(right_edge - 30, bottom * height - 53.5)


def summary_play(lines):
    """The play button of a shortcut's summary page. On iOS 27 a tile opened that page ("This shortcut
    will:" over its steps) instead of running the shortcut; the card's unlabeled play button ran it. Its
    centre was 99 points in from the screen's right edge and 62 points above that heading."""
    heading = target(lines, "this shortcut will:", "this shortcut will")
    if not heading:
        return None
    width, height = orientation.current_points()
    return points(width - 99, heading[1] * height - 62)


def wait_for_input(name):
    wait_for(lambda lines: target(lines, INPUT), 5,
             f'"{name}" did not show the "{INPUT}" input. Check that its first action is Ask for Input with that '
             "prompt. No prompt was sent.")


def already_open(lines, shortcuts):
    """Whether the app a run needs is in front, on a screen its run starts from: Shortcuts' All
    Shortcuts, a summary page or an old answer; Siri's conversation or Conversations list. Never
    Spotlight, whose "Search or Ask" bar also reads as a Siri field."""
    if cli.spotlight_open(lines):
        return False
    if shortcuts:
        return bool(target([line for line in lines if line[1] < 0.2], "all shortcuts")
                    or summary_play(lines) or alert_body(lines, OUTPUT))
    return bool(siri_ready(lines))


def ask(h, prompt, mode, timeout):
    on_device = mode != SIRI
    # Opening an app through Spotlight took 8 to 8.7 s over Bluetooth LE (2026-11-24), a third of a run;
    # back-to-back calls find the app still in front and skip it.
    in_front = already_open(cli.screen_text(), on_device) if picture_ready() else False
    if on_device:
        if not in_front:
            open_app(h, "Shortcuts")
        open_shortcut(h, mode)
    else:
        if not in_front:
            open_app(h, "Siri")
        lost = "The Siri app showed neither a conversation nor its Conversations list. No prompt was sent."
        action, pos = wait_for(siri_ready, 5, lost, quiet=0.5)
        if action == "compose":
            tap(h, pos)
            pos = wait_for(siri_field, 5, "Siri's new conversation didn't show its Ask Siri field. "
                                          "No prompt was sent.")
        tap(h, pos)

    cli.refuse_if_locked()
    if not picture_ready():
        cli.die("The iPhone mirror is stale. No prompt was submitted.")
    if not on_device:
        # Keys sent while the field is still taking focus can land out of order: the first letter of
        # "What time is sunset today?" ended up last on an iPhone 17 Pro (2026-11-25). The keyboard
        # showing means the field has focus. It may stay hidden while a hardware keyboard is
        # connected, so this only waits, briefly.
        wait_for(keyboard_up, 2, None)
    cli.key_combo(h, "cmd+a")
    cli.type_text(h, prompt)

    failure = "Couldn't verify the typed prompt. Nothing was submitted. Check the iPhone before retrying."
    if on_device:
        def submit_button(lines):
            body = alert_body(lines, INPUT)
            if any(matching_prompt(body, prompt)):
                return target([line for line in lines if line[1] > body[-1][1]], "done", "ok")

        tap(h, wait_for(submit_button, 5, failure))
        find_reply = lambda lines: "\n".join(text for _, _, text in alert_body(lines, OUTPUT))
    else:
        def draft_lines(lines):
            # Not the + and send buttons at the field's ends (x 0.099 and 0.895), which OCR reads as
            # text between a wrapped prompt's lines
            composer = [line for line in lines if line[1] > 0.5 and 0.15 < line[0] < 0.85]
            hits = list(matching_prompt(composer, prompt))
            if not cli.spotlight_open(lines) and hits:
                return composer[hits[-1][0]][1], {normalized(text) for _, _, text in lines}

        draft = wait_for(draft_lines, 5, None)
        if not draft:  # typed out of order or not at all: select it all and type it once more
            cli.key_combo(h, "cmd+a")
            cli.type_text(h, prompt)
            draft = wait_for(draft_lines, 5, failure)
        cli.key_combo(h, "enter")
        cli.acted(h.first_seq)
        find_reply = lambda lines: siri_reply(lines, prompt, draft)

    return wait_for(find_reply, timeout,
                    "Request submitted. No readable reply appeared in time. Inspect the iPhone before retrying.",
                    quiet=0 if on_device else 1.2)


def apple_intelligence(model):
    """Whether an iPhone hardware identifier (UxPlay's model, such as "iPhone19,3") can run Apple
    Intelligence. It needs iPhone 15 Pro (iPhone16,1) or later; every iPhone since, the 16e (iPhone17,5)
    included, has a first number of 16 or more, and the iPhone 15 and 15 Plus are iPhone15,4 and 15,5."""
    match = re.fullmatch(r"iPhone(\d+),\d+", model or "")
    return bool(match) and int(match.group(1)) >= 16


def refuse_without_apple_intelligence():
    name = phones.current()
    model = phones.entry(name).get("model")
    if not model:
        cli.die("Can't tell which iPhone is mirroring, so can't check that it has Apple Intelligence. "
                "Reconnect Screen Mirroring, then retry. No prompt was sent.")
    if not apple_intelligence(model):
        cli.die(f'The mirroring iPhone ("{name}", {model}) can\'t run Apple Intelligence: siriai needs an '
                "iPhone 15 Pro or later. No prompt was sent.")


def run(args):
    parser = argparse.ArgumentParser(prog="phoneuse siriai", description="Read a reply from Apple Intelligence.",
                                     epilog=MODES_HELP.split("\n", 1)[1], formatter_class=argparse.RawDescriptionHelpFormatter)
    models = parser.add_mutually_exclusive_group()
    for mode in MODES:
        models.add_argument(f"--{mode}", dest="model", action="store_const", const=mode,
                            help="Siri, with personal context and app intents" if mode == SIRI
                            else f'the "{SHORTCUTS[mode]}" shortcut')
    # The MCP tool passes its model enum as --model=MODE (commands.Command.arguments).
    models.add_argument("--model", choices=list(MODES), help=argparse.SUPPRESS)
    parser.add_argument("--timeout", type=float, default=30, help="seconds to wait for a reply (default: 30)")
    parser.add_argument("prompt", help="a short, single-line question")
    options = parser.parse_args(args)
    prompt = options.prompt.strip()
    if not prompt or not prompt.isprintable() or set(prompt) - hidproto.CHARS.keys() or "\\n" in prompt:
        parser.error("use a nonempty, single-line prompt with characters supported by the US keyboard")
    if not 0 < options.timeout <= 120:
        parser.error("--timeout must be greater than zero and at most 120 seconds")
    if options.model is None:
        cli.die(MODES_HELP)
    if cli.IS_WINDOWS and not cli.can_reach_services():
        cli.die(cli.SANDBOXED)
    if not picture_ready():
        cli.die("Can't see a fresh iPhone screen. Run `phoneuse status`.")
    refuse_without_apple_intelligence()
    print(ask(cli.hid(), prompt, options.model, options.timeout))
