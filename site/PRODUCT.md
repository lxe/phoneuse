# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Stack

Static HTML/CSS (lxe's choice, 2026-11-09). A small Node build script copies the pages into `dist/`, fills in the
release asset URLs from the GitHub API, and writes the sitemap. Hosted on the lxe.co nginx server at
`www/phoneuse.tech`, deployed with rsync. Plausible analytics from stats.pocketmodel.ai.

## Users

Both groups, agents first (lxe, 2026-11-09):

1. People who run coding agents (Claude Code, Codex and other agents with a shell) on a Linux PC or a Mac and want the agent to
   use their iPhone: open apps, read the screen, tap, type.
2. People who want to use their iPhone from the computer themselves, with the computer's mouse and keyboard,
   including iOS developers and testers.

## Product Purpose

Phone Use (two words, always) lets an AI agent, or you, see and operate a real, unmodified iPhone from a computer.
The iPhone sends its screen to the computer over AirPlay Screen Mirroring; the computer sends taps and keystrokes
to the iPhone as a Bluetooth mouse and keyboard, through its own Bluetooth or through the bridge. The site's job is
to get the visitor to download the Linux or macOS app (and later the iPhone app).

## Positioning

It works with any iPhone as it comes: no jailbreak, no developer account, no cable, nothing installed on the phone
for the control path. It relies on two features every iPhone has, Screen Mirroring and AssistiveTouch. The owner
stays in charge through the Phone Use iPhone app: agents are refused until it's paired, a Live Activity shows the
agent's current action, and Take over stops it.

## Operating Context

- Downloads (GitHub releases, `releases/latest`): `PhoneUse-<ver>-x86_64.flatpak` (Linux),
  `PhoneUse-<ver>-mac.dmg` (macOS 15 or later, signed and notarized; menu bar app; uses the bridge for input),
  `phoneuse-bridge-<ver>.bin` (bridge firmware). Windows: coming soon. iPhone app: on the App Store
  (https://apps.apple.com/us/app/phone-use/id6832337610, since 2026-11-10).
- The bridge: a LilyGO T-Display (the original ESP32 T-Display, not the S3), 1.14-inch screen, two buttons,
  USB-C, no soldering. The Linux app flashes it over USB; setup over BLE with a matching 6-digit code; joins
  2.4 GHz Wi-Fi; the iPhone pairs with "Phone Use Bridge". Signed UDP control (HMAC-SHA256). Can sit next to the
  phone on a battery or charger.
- Agents: the `phoneuse` CLI (`open`, `text`, `tap-on`, `gesture`, `recipe`, `screenshot`, `done`); no MCP server
  (removed 2026-11-12, lxe: one surface). A Claude Code skill ships in the app and the repo.
- Companion app: pairs over the local network (one-time link, 10 minutes), Live Activity with current action,
  step count and time, Take over / Resume, Agent control (Always, After a delay, Ask every time),
  settings for the bridge, keep-awake, keyboard, pointer, mirror audio.
- Browser mirror at localhost:8777, mirror window; the user's mouse and keyboard drive the phone.

## Capabilities and Constraints

- Alpha. The page says so in one short line at the top, without listing the tested setup (lxe, 2026-11-09:
  no Hyprland or NVIDIA mention).
- Network: everything runs on the local network. Released builds send Apple push notifications through a relay
  (Cloudflare Worker, phoneuse-push.lxe.workers.dev) that holds the APNs key; the agent's action text, warnings
  and the PC's name are sealed so the relay can't read them.
- Saving the iPhone passcode and unlocking with it are turned off (2026-11-09): don't advertise unlocking.
- App privacy policy: https://github.com/lxe/phoneuse/blob/master/docs/PRIVACY.md; the site's privacy page must agree with it.
- License: MIT. Source: https://github.com/lxe/phoneuse.

## Brand Commitments

- Name: "Phone Use", two words.
- App icon: a sleepy cream cloud hugging a dark plum phone, over layered clouds from plum through coral to
  amber (`~/phoneuse/packaging/icons/app/phone-use-icon.png`, rounded sizes in `packaging/icons/hicolor/`).
- Voice: plain, specific product language. No "not X but Y", no punchline closers, no filler, no "seamless",
  "effortless" or "unlock" as marketing verbs. Every sentence says something concrete.

## Evidence on Hand

- The repo docs (README, ARCHITECTURE, docs/COMPANION.md, docs/BRIDGE.md, docs/PUSH-RELAY.md).
- Measured numbers: pointer move to mirror about 100 ms; bridge round trip 15 ms median; PC Bluetooth 114–215
  reports/s, bridge 38.
- No screenshots, video, testimonials or user counts. Imagery is drawn and labeled as illustration (lxe chose no
  captures from the phone, 2026-11-09).

## Product Principles

1. The owner of the phone decides when an agent may use it, and can stop it at any moment.
2. Use what the iPhone already has; ask the user to install or modify as little as possible.
3. State what is measured and tested; don't promise setups that haven't been tried.
