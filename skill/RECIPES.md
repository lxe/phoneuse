# Gestures and recipes

Every touch is a gesture: steps for one finger. Recipes are gestures with names.
This file ships with the skill; SKILL.md has the short version.

`phoneuse up` runs the AirPlay mirror and mouse control together; on the iPhone,
start Control Center → Screen Mirroring → Phone Use on <this computer>. Agents then use the commands
below and look at `phoneuse screenshot`, which waits for the mirror to catch up
after the last action, so it always shows the result.

Coordinates are fractions of the screenshot (0 to 1), or iPhone points when any
value is above 1. The screen is 402 × 874 points.

## Gestures

```
down X Y      touch down at (X, Y); the pointer moves there and settles 0.1-0.2 s first
move X Y [S]  slide to (X, Y) over S seconds (default 0.3), finger still down
hold S        stay still for S seconds (finger down or up)
up            lift (a gesture ends lifted anyway)
```

CLI: `phoneuse gesture down 0.2 0.3 hold 1 move 0.7 0.6 1.2 hold 0.5 up`.

- `down` lands exactly where asked. A `down`, `hold`, `up` with no `move` (a tap or
  long press) goes as one timed press, so the hold is exact even over the bridge.
- `move` is relative motion while the finger is down, sized from the phone's
  calibration (`phoneuse calibrate`). On a calibrated iPhone 17 Pro (through the
  bridge, November 2026) every drag ended where it was sent: the pointer sat on
  the requested point, including a 0.6-screen diagonal and a drag with a 1.2 s
  hold in the middle. An uncalibrated phone can fall short. Give long moves
  0.6-1.5 s: slow moves are steadier.
- Moves can chain with holds between them, for as long as you need, up to 30 s
  per step: drag, pause over a target, drag on.
- Each gesture records a film: a frame at the end of every move, at the end of
  every hold with the finger down, before a long press lifts, and 0.3 and 0.8 s
  after the lift, labelled, with the finger marked (film.py). The command prints
  `film: PATH` (`--film PATH` chooses the file). To see a moment, end a
  step there: `hold 0.4` mid-drag adds a frame of the phone at that point.
- Moving fast in the last instant before `up` flings scrolling content. To stop
  content exactly, end with a short slow move, or use `scroll` / `scroll-by`,
  which do that for you.

| To do this | Gesture |
|---|---|
| Tap | `down X Y up` (or `phoneuse tap X Y`) |
| Long press (context menu) | `down X Y hold 0.8 up` |
| Double tap (not yet verified) | `down X Y up hold 0.1 down X Y up` |
| Swipe | `down 0.5 0.8 move 0.5 0.3 0.3 up` |
| Slow drag (a slider) | `down X0 Y move X1 Y 0.8 hold 0.2 up` |
| Press, then drag (move an icon, reorder a list) | `down X0 Y0 hold 1.0 move X1 Y1 1.0 hold 0.5 up` |
| Drag an app out of an open folder | `down X0 Y0 hold 0.5 move 0.5 0.85 0.6 hold 1.2 move X1 Y1 0.6 hold 0.6 up` (below the folder, wait for it to close, then go to the slot) |
| Drag to the next home screen page (not yet verified) | `down X Y hold 1.0 move 0.98 Y 0.8 hold 1.2 move 0.5 Y 0.6 hold 0.5 up` |

### Moving icons on the home screen (measured, iPhone 17 Pro, iOS 26)

The finger goes exactly where you send it; what decides the result is how iOS
reads the drop point:

- Holding an icon about 0.5 s opens its menu. Keep holding and start moving, and
  the menu closes and the home screen enters edit mode. In edit mode a 0.4 s hold
  is enough to pick an icon up.
- **The icon goes into the slot under the finger when it lifts.** Slots are the
  grid cells: on this phone four columns centred at x 0.154, 0.385, 0.615, 0.845
  and rows at y 0.14, 0.255, 0.37, 0.485, 0.60, 0.715. A cell's edges are halfway
  between centres, so y 0.20 already belongs to row 2.
- **Ending close to another icon's centre makes a folder** (0.12 of the width
  from it was close enough). To take another icon's place, end inside its cell
  but off its centre, toward the top-left: 30-45 pt from the centre worked.
- Icons shift while you drag, so the slot you aimed at may hold a different icon
  by the time you lift. Moving an icon toward the end of the page, it lands after
  the icon you dropped beside; moving it toward the start, it lands before.
- An empty slot at the end of a page is always safe: nothing to merge with.
- A folder with one app left dissolves, and the app takes the folder's place.
- Leave edit mode with the checkmark at the top right (0.827, 0.037).

## Recipes

A recipe is a gesture with a name: `phoneuse recipe NAME [VALUES]`. `$1`, `$2`... in its steps take the values in order, and
`$3=0.8` gives one a default. `phoneuse recipe list` shows every
recipe with its steps.

When a gesture works for something you will do again, save it:

```
phoneuse recipe save move-icon down $1 $2 hold 1.0 move $3 $4 1.0 hold 0.5 up -- drag a home screen icon from ($1,$2) to ($3,$4)
phoneuse recipe move-icon 0.2 0.3 0.7 0.6
```

Saved recipes live in `~/.config/phoneuse/recipes.json` on this PC, for every
agent that uses it. A saved recipe with a built-in's name replaces that one here
(to tune it for this phone); `phoneuse recipe forget NAME` brings the built-in back.

### Built-in recipes (verified on an iPhone 17 Pro, iOS 26, November 2026)

| Recipe | Steps | What it does |
|---|---|---|
| `back` | `down 0 0.5 move 0.8 0.5 0.35 up` | a finger sliding in from the very left edge (x = 0) |
| `switcher` | `down 0.5 1 move 0.5 0.62 0.45 hold 0.6 up` | slow swipe up from the bottom, then hold |
| `control-center` | `down 0.88 0 move 0.88 0.45 0.6 up` | slow (0.6 s) swipe down from the top-right corner |
| `notifications` | `down 0.15 0 move 0.15 0.7 0.6 up` | slow swipe down from the top-left |
| `dismiss` | `down 0.5 1 move 0.5 0.15 0.12 up` | fast, long swipe up from the very bottom; closes Control Center, Notification Center and the switcher |
| `long-press` | `down $1 $2 hold $3=0.8 up` | press and hold; opens context menus |
| `home` | (the Home key) | Cmd+H; works from any app |

`phoneuse back`, `phoneuse switcher` and the others are the same recipes.

## Other commands

| Goal | Command | What it does |
|---|---|---|
| Pointer on | `phoneuse cursor on` | checks first and does nothing if the pointer is already on; otherwise holds the Siri key (consumer usage 0xCF), waits until Siri's orb shows at the bottom centre (about 1-1.4 s), plays "Turn on AssistiveTouch" on the PC speakers, and lets go. `phoneuse cursor` moves the pointer A, B, A, B and wants a pointer-sized change centred on each spot that repeats on both visits, so a moving wallpaper or video can't pass for the pointer |
| Per-phone drag speed | `phoneuse calibrate` | hovers the pointer up from a fixed start with N relative reports of each size (28 down to 1 unit), finds it in the picture, and returns to the start by stepping beside it first (iOS ignores an absolute report that repeats the previous position after relative moves). The table goes to `~/.config/phoneuse/phones.json` under the mirroring phone's name; drags use it while that phone mirrors. An iPhone 15 Pro moved 0.08x-0.22x as far as the 17 Pro per report, and its drags fell far short until calibrated |
| Scroll | `phoneuse scroll down [N]` / `up` | drags the content by N half-screens, slowing down before the lift: no fling |
| Scroll exactly | `phoneuse scroll-by DY [X]` | the same drag by DY (screen fraction, or points above 1); lands within ~5-8% |
| Open an app | `phoneuse open Settings` | Cmd+H, Cmd+Space, type the name, Enter |
| Type | `phoneuse type "hello\n"` | US layout; `\n` presses Enter |
| Keys | `phoneuse key cmd+space` | modifiers: cmd, shift, alt, ctrl |

## Things that don't work, or bite

- **Flings.** iOS keeps scrolling after the lift when the touch's last moves
  were fast, and resting before the lift doesn't stop it (a resting touch sends
  no moves, so the fast ones stay the last): a 0.45 drag at full speed to the
  end moved 0.65-0.67 of the screen even with a 0.4 s rest, and one ending at
  one unit per tick (~450 pt/s) still moved 0.65-0.73. Scroll drags slow down
  further instead (`touch.scroll_drag`): the last 60 pt at one unit per tick,
  the last 20 pt one unit every 3 ticks (~150 pt/s), then 0.15 s still and
  lift. Eight 0.45 scrolls in a row then moved 0.426-0.439. The mirror's wheel
  uses the same profile (`pointer.py`) and never grips for less than 15 pt,
  since a shorter press-and-move reads as a tap.
- iOS hides the iPhone's on-screen keyboard while any Bluetooth keyboard is
  connected, and Eject only toggles it blindly. Dropping the link while idle
  would bring the keyboard back, but once Phone Use drops it, iOS refuses
  Phone Use's reconnects ("Connection refused") until the owner taps Phone Use in
  Bluetooth settings. Links the phone dropped itself reconnect fine. So the
  daemon's `release` command exists (it declines the iPhone's reconnects and
  drops hovers until the next real input), but nothing calls it.
- Avoid tapping the top centre of the screen: it hits the Dynamic Island, which
  opens the Screen Mirroring popup with a Stop Mirroring button.
- Cmd+H doesn't close Control Center or Notification Center; use `phoneuse dismiss`.
- iOS ignores the mouse wheel and Cmd+Tab. Over the mirror window and in the
  browser mirror, Phone Use turns the wheel into a drag instead (see pointer.py).
- Fast swipes down from the top are ignored; the top-edge recipes are slow on purpose.
- If the phone locks, the passcode screen appears. The agent must stop and ask the
  owner to unlock it. Never type a passcode.

## Why it works this way (HID facts measured on the device)

- The absolute pointer report lands exactly where asked, anywhere on the screen.
- Button presses count only from the absolute pointer, not from the relative mouse.
- While the button is held, an absolute position change ends the touch after about
  90 ms (it reads as a click), so a drag is an absolute press, then relative motion
  (button 0), then an absolute release at the same spot. Verified with touchlab/.
- Move the pointer, wait about 0.1 s, then press. Pressing in the same instant as
  a jump makes iOS start the touch on the way, so an edge swipe turns into a drag
  of the page (this is what broke `back` at first).
- iOS accelerates relative motion by report size and drops motion from reports
  that arrive less than about 8 ms apart, so drags send one report every 8 ms
  (including empty ones, which keeps slow drags alive), sized from a measured
  table (`touch.DRAG_PTS`) and rounded down so the touch never overshoots.

## On Windows (Bluetooth LE)

- Reports reach the phone once per Bluetooth LE connection interval (iOS settles
  on 15 ms for a keyboard), not every 8 ms as over Classic. The HID service spaces
  them at least one interval apart: two reports in one connection event arrive a
  fraction of a millisecond apart, and iOS drops pointer motion that comes faster
  than ~8 ms, so drags would lose distance.
- Each phone's drag table is measured over this link (`phoneuse calibrate`, also
  automatic the first time its pointer is on); the iPhone 17 Pro table from Classic
  doesn't apply. Measured on two phones over LE, a report moved 0.08-0.3x as far as
  that table says (phones also differ by Tracking Speed). Taps use the absolute
  pointer and need no calibration.
- Scroll drags over LE use small steps (at most 6 pt a report) and a steady end, and
  go 8 pt further than asked: a scroll view starts following the finger only after a
  few points, and those never come back (scroll-by fell 7.5-10 pt short).
- `open` checks each step on the screen (iPhone 14 Pro Max, iOS 26.5):
  - An app opened from Spotlight goes back to Spotlight on Home, so Home is pressed
    twice.
  - Cmd+Space toggles Spotlight, so it counts as open only when the picture differs
    from the home screen's, and matches count only under Spotlight's section titles.
  - Right after an app closes, Spotlight sometimes takes no keys for a moment, so the
    name is typed again.
  - Spotlight keeps its last search, so that is selected first.
  - The top hit's icon is tapped, not its name: a tap on Weather's name did nothing.
  - The app must come up. Enter is never pressed after a miss: it opened the last
    search's app.
- In the mirror window Ctrl is Cmd: the Windows key belongs to the Start menu.

## iPhone settings this relies on

- Accessibility → Touch → AssistiveTouch: on; Dwell Control: off (otherwise a
  resting pointer taps on its own).
- Optional: Accessibility → Accessories → Pointer Control colour Orange, large size, Automatically
  Hide Pointer off. Only `phoneuse where` and the telemetry tools use it, to find
  the pointer in screenshots; gestures never need to locate the pointer.
- Consider Display & Brightness → Auto-Lock: Never while an agent is working.
