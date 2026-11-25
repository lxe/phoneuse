---
name: phoneuse
description: See and operate the owner's iPhone from this computer (Linux, Mac or Windows) with Phone Use, through its MCP tools or the `phoneuse` CLI, and ask Apple Intelligence on the phone. Use when a task needs the iPhone: open or use an app, read the screen, tap, type, swipe, check a notification, change a phone setting, or test something on the device. Also use when the mirror or the Bluetooth link needs starting or fixing.
---

# Operating the iPhone with Phone Use

Phone Use drives a real, unmodified iPhone. Its screen arrives on this computer
over AirPlay Screen Mirroring, and input goes in as a Bluetooth pointer and
keyboard, either from this computer or through the Phone Use Bridge board.
Nothing runs on the phone to control it.

You reach it in one of two ways, and they do the same things:

- **MCP tools**, when your client has the Phone Use MCP server connected
  (Claude Code and Codex do after Connect in the Phone Use app). Tool names
  are below, such as `look` and `tap_on`.
- **The `phoneuse` command** in a shell, when MCP isn't connected or you want
  to chain steps in one call. `phoneuse --help` lists every command, and
  `phoneuse commands` shows which command each MCP tool runs.

Both use the same owner approvals, the same mirror and the same input, so
pick whichever your client has. The tables below give both names.

## Before acting

1. **Check the setup:** `status` (`phoneuse status`). You need a paired Phone
   Use app (`iPhone app: 1 paired` or more), the HID service active, the
   Bluetooth link `connected=True`, and the mirror showing the phone. If
   something is off and you can't tell why, `phoneuse doctor` checks the whole
   setup and prints the fix for each problem.
2. **Not paired:** stop and ask the owner to pair the Phone Use app on the
   iPhone (Pair iPhone in the Phone Use window). Every action is refused until
   they do.
3. **Not mirroring:** ask the owner to start Screen Mirroring to this
   computer's receiver ("Phone Use on <computer>"; `status` names it) from the
   iPhone's Control Center. You can't start mirroring from the computer, and
   every input refuses until it runs.
4. **Phone Use isn't running:** on Linux, open the Phone Use app, or run
   `phoneuse up` (with a mirror window) or `phoneuse serve` (browser mirror
   only) somewhere the owner can see it, not hidden in the background. On a
   Mac, open the Phone Use app. On Windows, open **Phone Use** from Start;
   closing its window keeps it in the tray, and **Quit** in the tray stops it
   and its MCP server. After reopening it, reconnect your MCP client or start a
   new chat if the tools are still missing.
5. **`connected=False`:** check `status` again after a few seconds, since the
   link reconnects by itself. Then try `reconnect_bluetooth`
   (`phoneuse reconnect`) once. If it stays down, ask the owner to tap the
   Phone Use entry in the iPhone's Bluetooth settings.
6. **Show the owner the phone** before your first action, so they can watch:
   `show_mirror` (`phoneuse view`) opens the browser mirror at
   http://localhost:8777. If your harness has its own browser, such as the
   Claude desktop app's browser pane, open that address there instead
   (`phoneuse view --print` prints it). Do this once per task; if the page is
   already open, leave it. The page is for watching. Act through the tools,
   never by clicking in the page.

## The loop

Look, decide, act, look again. Read the screen as text first: it costs a few
hundred tokens, where a screenshot image costs about 1,300.

```bash
phoneuse text                         # MCP: look. Every text line on screen: "x y  text"
phoneuse tap-on "Wi-Fi"               # MCP: tap_on. Tap a unique label
phoneuse tap 0.5 0.35 --text          # CLI: end any action with --text to act, then read the screen
```

Positions are fractions of the screen from 0 to 1, from the top-left corner,
or iPhone points when any value is above 1. Take them from a recent `look`,
`inspect_region` or screenshot. If the screen moved since then, look again.

`look` (`phoneuse text`) drops the status bar and stray icon glyphs (`--all`
keeps them), shows the back chevron as `[back]`, and lists on/off switches as
`[switch on]` and `[switch off]`, since OCR can't read them.

When text isn't enough (pictures, icon-only screens, checking a layout), use
`inspect_region` (`phoneuse inspect-region`) for a crop plus its text, or a
full screenshot:

```bash
phoneuse screenshot $SCRATCH/s1.png   # then look at the image
```

Write screenshots into your own scratch folder with a new name each time, so
you never read an old image. They come from the mirror's video, so windows
covering the mirror don't matter. The grey AssistiveTouch button near the
lower right stays on screen and can hide what's under it; scroll a little if
you need that spot.

## The tools

**Session and connection**

| MCP tool | CLI | Use it to |
|---|---|---|
| `status` | `status` | Check the mirror, Bluetooth and owner takeover. The first call, and again after anything goes wrong. |
| `show_mirror` | `view` | Show the owner the mirror before you act. |
| `reconnect_bluetooth` | `reconnect` | Reconnect a paired phone after `status` shows Bluetooth down. Try it once. |
| `request_unlock` | `unlock` | Locked phone: it says unlocking is up to the owner. Ask them to unlock it, then use `wait_for_unlock`. |
| `wait_for_unlock` | `wait-for-unlock` | Wait, without sending input, while the owner unlocks the phone. |
| `end_session` | `done` | Give control back to the owner. Always call it at the end. |

**Seeing the screen**

| MCP tool | CLI | Use it to |
|---|---|---|
| `look` | `text` | Read the visible text with positions (OCR). Cheap, so use it first. |
| `inspect_region` | `inspect-region` | Get a crop image plus OCR for icons, images, or text that `look` misreads. |
| `wait_for` | `wait-for` | Wait for a label to appear, without touching anything. |
| `find_in_scroll` | `find-in-scroll` | Scroll a list until a label shows up, then return its position. |
| (CLI only) | `screenshot PATH` | Save a full PNG of the screen once it has settled. |

**Acting**

| MCP tool | CLI | Use it to |
|---|---|---|
| `open_app` | `open` | Open an app by its name (through search). |
| `tap_on` | `tap-on` | Tap a label that appears exactly once. Use `near` or `region` to narrow it, and `expect` to check that the next screen came. The preferred tap. |
| `tap` | `tap` | Tap a position, when the target has no readable label. |
| `long_press` | `longpress` | Press and hold, for context menus. |
| `swipe` | `swipe` | Drag, for carousels, sliders and dismissing sheets. |
| `scroll_by` | `scroll-by` | Scroll by part of a screen. A positive value shows what is below. |
| `type_text` | `type` | Replace the focused field's text (`--append` on the CLI adds to it). |
| `press_key` | `key` | Press a key or a combination: return, esc, tab, cmd+a, cmd+space. |
| `toggle_keyboard` | `keyboard` | Show or hide the on-screen keyboard. |
| `back` | `back` | Go back in the current app. |
| `navigate` | `recipe NAME` | A system gesture: home, switcher, control-center, notifications or dismiss. |
| `run_steps` | (MCP only) | Batch up to six steps in 90 seconds. Stops on failure or uncertainty and returns the screen's text. |
| (CLI only) | `gesture`, `recipe` | Any one-finger touch as steps, and saved gestures. See Gestures below. |
| (CLI only) | `notifications-read` | Open Notification Center, read it and close it. |

`tap_on` (`tap-on`) needs the label to appear exactly once, as a whole line or
at the start of one. A label shown twice (a title and a tab, say) is refused
as ambiguous: narrow it with `near` or `region`, or tap by position. Words that
aren't on screen are refused, so scroll first. Icons and pictures have no
text: tap them by position.

Taps by position are checked: `tap` and a gesture's first touch are refused
when the area around the point changed since your last look, because the
position then belongs to something else. Look again, then tap. On the CLI,
`--force` taps anyway, for fixed controls such as a tab bar right after a page
change.

If a result says input "was sent" or "may have been sent", look at the screen
before you retry, so nothing happens twice. When a result notes that no change
showed, the action may have done nothing or the phone is slow: look before
repeating it.

## Working fast

Every round trip with the phone costs you a turn, so make each one count.

- **Never `sleep`.** `look`, `--text` and `screenshot` wait until the screen
  has settled (apps launching, pages sliding in) before reading. A fixed sleep
  is either too short or wasted time.
- **Batch the steps you can already predict.** Over MCP, use `run_steps` when
  you can give the steps and their arguments up front, even before the app is
  open; `tap_on` with `expect` checks each transition, and `wait_for` checks
  that a label appears. Put `look` or `inspect_region` last. On the CLI, chain
  them in one call and read once at the end:
  `phoneuse open Mail && phoneuse tap-on "Search" && phoneuse type "from:lxe" --text`.
  Stop batching where the next step depends on what appears, or needs the
  owner's approval. If a batch stops after sending input, look at the screen
  before resuming from the unfinished step.
- **Read with text, not images.** Text is enough for menus, lists, feeds and
  counts. Take a screenshot only for pictures, icon-only screens, or when text
  lands somewhere surprising.
- **Feeds you act on (X, Instagram, lists): scroll, then look, then tap.**
  `scroll_by` (`phoneuse scroll down 0.6`) moves the content without momentum,
  so it stops where the finger lifts and your next look stays true. A flick
  keeps the feed gliding after you look, and a tap from that picture hits the
  wrong post.
- **Flicks only for snap feeds** such as Reels, TikTok or Shorts:
  `phoneuse gesture down 0.5 0.75 move 0.5 0.25 0.2 up`.

## Gestures (CLI)

Any touch other than a plain tap is a gesture: steps for one finger, in order.

```
down X Y      touch down at X Y
move X Y [S]  slide to X Y over S seconds (default 0.3), finger still down
hold S        stay still S seconds (finger down or up)
up            lift (a gesture ends lifted anyway)
```

```bash
phoneuse gesture down 0.5 0.4 hold 0.8 up                           # long press
phoneuse gesture down 0.2 0.3 hold 1.0 move 0.7 0.6 1.0 hold 0.5 up # press, then drag (move an icon)
phoneuse gesture down 0.5 0.4 up hold 0.1 down 0.5 0.4 up           # double tap
```

Each gesture comes back as a film: one image with a frame at the end of each
step and 0.3 s and 0.8 s after the lift, with the finger marked on each. The
command prints `film: PATH`; read it when the gesture's middle matters.
`--exact` on `swipe`, `gesture` or `recipe` finds the pointer in the picture
before the finger lifts and slides the rest of the way, for icons, sliders
and text selection (about 1.6 s more). Slow moves are the steadiest; for
scrolling content, use `scroll` or `scroll-by`, which stop exactly.

Recipes are named gestures: `phoneuse recipe back`. `phoneuse recipe list`
shows the built-in ones and their steps. Save one that works, with `$1`, `$2`
for values given when it runs:

```bash
phoneuse recipe save move-icon down $1 $2 hold 1.0 move $3 $4 1.0 hold 0.5 up -- drag a home screen icon
phoneuse recipe move-icon 0.2 0.3 0.7 0.6
```

[RECIPES.md](RECIPES.md), next to this file, has the gesture table and how
each recipe was measured; read it before a gesture you haven't done before.

## Asking Apple Intelligence: `siriai`

`siriai` (`phoneuse siriai`) types a prompt on the phone and returns the
visible reply. You must choose a model. Without one, it refuses and lists the
choices.

| `model` (MCP) / flag (CLI) | What answers | Sees | Use for |
|---|---|---|---|
| `siri-plus-context` / `--siri-plus-context` | Siri, typed to in the Siri app | The owner's personal context (messages, mail, calendar, what's on screen), plus app intents, so it can act in apps | Questions about the owner's own data, or getting something done on the phone |
| `on-device` / `--on-device` | The on-device model, through the **PhoneUse On-Device** shortcut | Only the prompt | Quick, private text work: rewrite, summarize, classify |
| `cloud` / `--cloud` | Private Cloud Compute, through **PhoneUse Cloud** | Only the prompt, and it may also look things up on the web | Harder questions that should stay private |
| `cloud-pro` / `--cloud-pro` | Apple's largest cloud model, through **PhoneUse Cloud Pro** | Only the prompt | Hard reasoning. It is slower and has tight daily limits. |

- Shortcut models can't read the owner's data or run app actions. If you need
  either, use `siri-plus-context`, and put any text the model needs inside the
  prompt.
- Siri can send, buy and change things. Get the owner's OK before any prompt
  that would do so.
- Prompts are one line, at most a few sentences, using US-keyboard characters
  only.
- **If a model's shortcut is missing,** `siriai` says so and gives its iCloud
  link. Ask the owner whether they want to add it or want you to. Opening the
  link in Safari on the iPhone shows an Add Shortcut button; the Phone Use
  iPhone app's Agents tab also links all three, under Apple Intelligence:

  | Shortcut | Link |
  |---|---|
  | PhoneUse On-Device | https://www.icloud.com/shortcuts/f76f5ae481854d139c6d786c267d69e7 |
  | PhoneUse Cloud | https://www.icloud.com/shortcuts/82f5af01617e4584bfe01ad3c08816c0 |
  | PhoneUse Cloud Pro | https://www.icloud.com/shortcuts/a2e0e65830b24c1ba382379b74154465 |

  If a link fails, `siriai` also gives the steps to build the shortcut by
  hand. Follow them exactly: the shortcut name, the input prompt
  `phoneuse model prompt` and the alert title `phoneuse model answer` must
  match character for character.

## Typing

Typing replaces what the focused field already holds (fields often keep
earlier text, such as a previous search); on the CLI, `--append` adds to it
instead. The phone applies autocorrect to typed text (a test typed "phoneuse"
and sent "phone use"). Read the field before sending anything where exact text
matters, and fix it with backspace (`press_key backspace`) and typing again.

## Apps reopen where they were left

`open_app` brings an app back in whatever state it was last in: a detail
sheet, a settings sub-page, a half-scrolled list. Check the first look before
trusting it. A sheet with an X or "Done" in a top corner is a leftover: close
it, or go `back` until you reach the app's main view.

## Ending a session, and the owner taking over

Your actions start an agent session by themselves: the owner's view glows and
their mouse and keyboard pause. When your task is done, call `end_session`
(`phoneuse done`) to hand the phone back at once.

If an action fails with "The user took over the phone", or a result starts
with PAUSED, stop and wait. Continue only when the owner tells you to. A note
that the picture is old means the phone's screen is off or mirroring stalled:
ask the owner.

## Rules

- **Locked phone:** ask the owner to unlock it, then `wait_for_unlock` once,
  with no other calls until it returns. Resume only on `unlocked`; on a
  timeout, wait for the owner before trying again. Never ask for, guess or
  type a passcode.
- **Don't tap the top centre** (the Dynamic Island, about x 0.35–0.65,
  y < 0.06): it opens the Screen Mirroring popup, whose Stop Mirroring button
  cuts the screen feed.
- **This is the owner's personal phone.** Messages, notifications, badges and
  photos on it are private. Looks and screenshots will show things unrelated
  to your task: ignore them, use only what the task needs, and don't repeat
  other contents. Ask before anything that sends, posts, buys, deletes, or
  changes an account or security setting, as you would in a browser.
- **Permission prompts** ("Allow notifications?" and the like) belong to the
  owner. Never tap Allow; leave them, or ask, unless the task is about that
  permission.
- If the owner is using the phone at the same time (the screen changes on its
  own), pause and ask before continuing: you'd both be moving the same
  pointer.

## When something misbehaves

- **A tap or swipe seems to miss:** look again first. The mirror trails the
  phone slightly, so an earlier look may be out of date.
- **An action seems to do nothing:** the screen was probably still changing
  (an app launching, a sheet animating). Look, then retry once.
- **Taps happen on their own wherever the pointer rests:** Dwell Control is on.
  Ask the owner to turn it off (Accessibility › Touch › AssistiveTouch ›
  Dwell Control).
- **Taps and drags do nothing at all:** the pointer needs AssistiveTouch on.
  `phoneuse cursor` says on, off or unknown.
- **Drags or scrolls fall short:** `phoneuse calibrate` measures this phone's
  pointer for about 13 seconds, with the phone upright, without tapping
  anything. It runs by itself the first time; run it again after the owner
  changes Tracking Speed.
- **Keep the screen from locking:** `phoneuse awake on` (or the setting in the
  browser mirror).
- **Input through the bridge:** `phoneuse bridge status` shows its address,
  its phone and the round trip.
