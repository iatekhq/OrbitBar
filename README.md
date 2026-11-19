# OrbitBar by IATEK

**A modern taskbar for Windows 11/10.**

A floating dock that replaces the Windows taskbar: apps, music, system stats, security status, a study planner and more, all live and all customizable with a right-click. Free and open source, made by [IATEK](#about).

## 📥 Install (2 minutes, no Python needed)

1. Go to the **[Releases page](../../releases/latest)** and download `OrbitBar-vX.Y.Z-windows.zip`.
2. **Unzip it to a folder you'll keep**, for example `Documents\OrbitBar`. Don't run it from inside the zip or from Downloads if you clean that folder: "Start with Windows" remembers where it is.
3. Open the folder and double-click **`OrbitBar.exe`**.
4. **"Windows protected your PC"?** Click **More info → Run anyway**. Windows shows this for any new program that isn't code-signed (signing certificates cost money every year). The code is all here in this repo if you want to check it first, and the download is built by GitHub from this code (see *Actions*), not on someone's PC.
5. The bar appears and the Windows taskbar hides. **Right-click the bar** for everything: ⚙ Settings, styles, sizes, 🧩 Customize widgets, Start with Windows.

**Getting your normal taskbar back:** right-click the bar → **Quit OrbitBar**. If OrbitBar ever crashes, a small watchdog brings the Windows taskbar back by itself within about 3 seconds.

**Updating:** the bar tells you when a new version is out (right-click → *Get OrbitBar X.Y.Z*). Quit OrbitBar, unzip the new version over the old folder, start it again. Your settings are kept in `%APPDATA%\OrbitBar`.

**Uninstalling:** right-click → turn off *Start with Windows* → *Quit OrbitBar*, then delete the folder (and `%APPDATA%\OrbitBar` if you want your settings gone too).

## What's in it

OrbitBar has:

- **Start + Search**: left-click opens Start, right-click opens the Win+X power menu
- **Workspaces**: virtual desktop pills (it uses the desktop names you set in Windows). Click one to switch, scroll to move between them, and press + to add a desktop. **Middle-click** a desktop to close it, or **right-click** for Switch / Close / New / Task View. Its windows move to the neighboring desktop; nothing gets closed
- **Pinned apps + open windows**, grouped by app:
  - left-click focuses the window, minimizes it if it's already focused, or cycles through the app's windows
  - middle-click opens a new window
  - right-click shows the window list, Pin/Unpin, and Close
- **Media**: shows what's actually playing. With several YouTube tabs or players open, it picks the one that's playing, not the one Windows guesses. Prev / play-pause / next go straight to that player; next/previous grey out when the page has no playlist. **Hover** it for a card with cover art, title, artist, app, live progress and big controls. **Click or drag the progress bar** to jump anywhere in the song (scroll over it for 5-second steps), use **−10 / +10**, or **⟲** to start the song over. If other tabs or apps have media, they're listed too: click one to control it from the bar
- **Stats**: a CPU sparkline, RAM, **GPU** (works for NVIDIA, AMD and Intel, using the same counters as Task Manager), network ↓↑ and battery. Hover GPU for the card name, VRAM and busiest engine. Click the stats for top CPU, GPU and memory apps and disk space
- **Volume**: scroll to change it, click for a slider, middle-click to mute
- **Security shield**: green ✓ (protected), amber ! (check something) or red ✕ (at risk). It covers Microsoft Defender or any other antivirus registered with Windows Security (Bitdefender, ESET…), real-time and tamper protection, how old the virus definitions are, the last quick scan, and the firewall for the Domain, Private and Public networks. Click it for details and shortcuts to Windows Security. It rechecks every 5 minutes in the background, and a notification appears if protection turns off
- **System tray**: the ^ button opens your background apps (WhatsApp, Telegram, Discord, Steam…) with their real icons, live tooltips and unread badges. Left-click opens the app, right-click shows the app's own menu, double-click works too. OrbitBar acts as the tray host and passes everything on to Windows' own tray, so nothing is lost if OrbitBar quits. Remove it in Customize widgets if you don't want it
- **Camera / mic indicator**: a green camera or orange mic badge appears while any app is using them, and it shows which app (Zoom, Teams, OBS…). It asks the drivers directly: the Media Foundation sensor monitor for cameras and Core Audio recording sessions for microphones. This works on new Windows 11 builds, where the old registry records stopped updating. A notification appears when an app turns them on. Click it for the Windows privacy settings
- **Battery** (laptops): a battery icon or ring that changes color with the level. While charging it shows a moving shimmer and a ⚡ bolt; when low, the outline pulses red. Notifications appear at 20%, 10% and when fully charged. Click it for a level history graph and time left or time to full. Right-click to switch between icon and ring. It only appears on PCs with a battery (laptops, tablets); desktops don't show it
- **Clock**: click it to open notifications and the calendar
- **Weather**: current conditions on the bar. Click it for the hourly and daily forecast and sunrise/sunset. It's free with no API keys: it tries **Open-Meteo**, then **wttr.in**, then **MET Norway (yr.no)**, and uses the first that answers. If all three fail, click the weather to see why
- **Timer / Pomodoro**: Focus 25, Break 5, Long break 15, or a custom length. The countdown shows on the bar and a notification pops up when it ends
- **Recycle Bin**: shows the item count. Click to open it, right-click to empty it
- **Quick launcher**: click 🔍, then type an app name, a folder (`C:\Users`, `%APPDATA%`, `shell:startup`) or a file path, and press Enter. Right-click 🔍 for Windows Search
- **Live window previews**: hover an app to see its windows. Click a preview to switch, middle-click or ✕ to close
- **Cards**: click the clock for a month calendar and world clocks, or click the stats for top CPU and memory apps and disk space
- **Styles**: Glass (real acrylic blur), Midnight, Black, White, in floating-pill or full-width shape, docked top or bottom, with optional auto-hide
- **Settings window**: right-click the bar → ⚙ Settings… to change everything with clicks, including accent color, alerts, weather city, world clocks, battery and timer presets
- **Drag to reorder**: drag app icons to rearrange your pinned apps. Drop a running app among them to pin it
- **Right-click setup**: right-click empty space on the bar to change style, shape and position, toggle auto-hide and previews, or open **🧩 Customize widgets…**. No config editing needed
- Hides itself when a game or video goes fullscreen. Maximized windows never cover it; they stop at the bar's edge
- Stays put on **Win+D / Show desktop**: if Windows minimizes or hides it, or raises the desktop over it, it's back on screen immediately
- Brings the normal taskbar back when it quits

## 🧩 Customize widgets

Right-click the bar → **🧩 Customize widgets…** (or ⚙ Settings → Widgets).

- **Gallery:** every widget, grouped into Navigation, Study, System, Media, Security, and Time & weather, with a **live preview**. Use **＋ Add left / ＋ Add right**, or **drag the preview onto the bar**.
- **Edit mode:** while the gallery is open, widgets on the bar get a dashed outline. **Drag** them to reorder or move them to the other side, and click the red **×** to remove one. Changes apply instantly; nothing restarts, so your music, timer and tray keep running.
- **Styles:**

  | Widget | Styles |
  |---|---|
  | CPU | Graph · Number · Ring |
  | Memory, GPU | Number · Ring |
  | Clock | Digital · Analog · Flip |
  | Battery | Icon · Ring · Gauge · Dots |

- **Separate system widgets:** CPU, Memory, GPU and Network speed are separate widgets now. Keep only the ones you care about.
- **New:** a 🌐 **World clock** chip, showing the first zone from Settings › World clocks.
- **Reset to default** brings back the standard layout.

Related widgets placed next to each other share one rounded pill (e.g. CPU + RAM + GPU, or volume + clock).

## ✨ Polish in 5.0

- Cards, previews and notifications **fade and glide** out of the bar
- Notifications **stack** (up to 4) above the right end of the bar instead of replacing each other
- The security shield reads Defender and antivirus status **directly from Windows (WMI)** and the firewall from the registry: about 10 ms, with no PowerShell window every 5 minutes. A third-party firewall (Bitdefender, ESET…) also counts as protection. Check it with `python orbitbar.py --check-security`

## 🎓 Study tools

| | On the bar | Click for |
|---|---|---|
| **Next class** | `🎓 CYB404 · 24m`, or `till 11:50` during class | Today's and tomorrow's classes with rooms |
| **Deadlines** | `📌 Lab 4 · 19h`, amber under 48h, red under 24h | Next 14 days. Tick ✓ when done |
| **Exam countdown** | `📝 CYT145 · 11d` | Upcoming exams |
| **Focus mode** | `🔒 24:13` while focusing, `☕ 04:12` on break | Pomodoro, quick timers, today's stats, 7-day chart |

**Getting your deadlines in.** Open ⚙ Settings → Study. Either:
- **Paste your school's calendar link** (any iCal / .ics feed) and OrbitBar syncs every 30 minutes:
  - **Moodle:** Calendar → *Import or export calendars* → *Export calendar* → "All courses" + "Recent and next 60 days" → **Get calendar URL**
  - **Brightspace / D2L:** Calendar → *Settings* → tick **Enable Calendar Feeds** → *Subscribe* → copy the URL
  - **Canvas:** Calendar → **Calendar Feed** (bottom right) → copy
  - **Blackboard Learn (Ultra & Original):** Calendar → ⋯ → **Share calendar** → copy the iCal link
  - **Schoology:** Calendar → *Export* (iCal) → copy
  - **Google Classroom:** each class has a Google Calendar. In Google Calendar → Settings → that calendar → **Secret address in iCal format**
  - **Outlook / Microsoft 365:** Settings → Calendar → *Shared calendars* → *Publish* → copy the **ICS** link
  - Paste several links (one per line) if you have courses on different systems. Deadlines from all of them merge into one list, and an assignment that shows up twice (Brightspace lists "Due" and "Availability Ends" separately) appears only once
- **Or type them in**, one per line: `2026-11-20 23:59 | Lab 4 | CYT145`

**Testing it:** `test-calendar.ics` (in this folder) has sample events written the way Moodle, D2L, Canvas, Blackboard and Google Classroom name them. Paste its full path (e.g. `D:\OrbitBar\orbitbar\test-calendar.ics`) into the calendar links box. Then run `python orbitbar.py --check-calendar` to see exactly what was understood. That command is also the first thing to run when a real link misbehaves.

**Timetable**, one line per class: `Mon,Wed 10:00-11:50 | CYB404 | B2034`, or paste a Google/Outlook calendar link.

**Reminders:** deadlines 24 hours and 1 hour before, classes 10 minutes before, exams a week before and the day before.

**Focus mode.** Start a 25/5 or 50/10 session. Focus and break rounds repeat, with a longer break every 4th round. If you switch to something distracting (YouTube, Netflix, TikTok, Discord, games… edit the list in Settings):
- **normal mode** sends a gentle nudge
- **strict mode** minimizes distracting *apps* (Discord, games). For *websites* it asks a quick **Focus check** instead: "📚 It's for study" allows that window for 15 minutes, "↩ Back to work" minimizes it
- **study context carries over:** a YouTube video opened in the same browser window you were just using for Moodle, Blackboard, D2L or Canvas (within 10 minutes) counts as study, not a distraction

**Study stats.** Time in study apps and sites (VS Code, Word, Moodle, VMware, Wireshark…) versus distractions, counted only while you're at the keyboard. You also get focus sessions, a 🔥 streak of days with 25+ minutes of focus, and a 7-day chart.

**Privacy.** Stats are stored only in `%APPDATA%\OrbitBar\study.json` as daily totals and app or site names. Window titles are never saved, and nothing is uploaded. Turn tracking off in Settings → Study.

## Run from source (Python)

For students who want to read or change the code. Needs Python 3.10+.

```powershell
git clone https://github.com/iatekhq/OrbitBar.git
cd OrbitBar
py -m pip install -r requirements.txt
py orbitbar.py               # starts in the background; you can close the terminal
```

Everything is in one file, `orbitbar.py`. With the downloaded version, use `OrbitBar.exe` instead of `python orbitbar.py` in the commands below.

## Troubleshooting

Check which version you're running: `python orbitbar.py --version`. Only one bar can run at a time; launching it again just exits.

If the media widget shows the wrong song, run `python orbitbar.py --check-media` while it's playing. It lists every player Windows reports and which one OrbitBar picks.

If a background app is missing from the tray card, press **Missing an app? Refresh** at the bottom of the card. If it's still missing, run `python orbitbar.py --check-tray`: it lists every tray window in the order apps find them, and tells you whether Explorer or an old OrbitBar copy is in front.

If the camera/mic badge doesn't appear, run `python orbitbar.py --check-privacy` while the camera is on. It shows what the live camera monitor, the live mic sessions, and the older registry history each see.

If OrbitBar crashes or is ended from Task Manager, a small **watchdog** brings the Windows taskbar back within about 3 seconds (a second, small OrbitBar process that just waits and uses no CPU; the log notes when it stepped in). If the taskbar is ever still hidden, run:

```powershell
python orbitbar.py --restore
```

## Start with Windows

Right-click the bar and turn on **Start with Windows**, or tick it in ⚙ Settings. That's all.

## Running in the background

`python orbitbar.py` starts the bar in the background and frees the terminal, so you can close it. Quit the bar from right-click → Quit OrbitBar. Errors go to `%APPDATA%\OrbitBar\orbitbar.log` (right-click → Open log). To keep it attached to the terminal while troubleshooting, run `python orbitbar.py --console`.

## Build it yourself

```powershell
py -m pip install -r requirements.txt pyinstaller
.\build.ps1             # -> dist\OrbitBar\OrbitBar.exe   (.\build.ps1 -Console shows errors in a console)
```

It's a folder build, not a single .exe on purpose: a one-file .exe unpacks itself to a temp folder on every start (slower), and the crash watchdog would need the full unpack too.

## Publishing a new version (maintainers)

1. Set `APP_VERSION` in `orbitbar.py`, e.g. `"5.6.0"`.
2. Add a `## 5.6.0` section at the top of `CHANGELOG.md`. It becomes the release notes.
3. Commit, then tag and push: `git tag v5.6.0` and `git push origin v5.6.0`.
4. GitHub Actions builds `OrbitBar.exe`, checks it starts, zips it and publishes the release. Running bars see the update within 12 hours.

`GITHUB_REPO` near the top of `orbitbar.py` (`"iatekhq/OrbitBar"`) tells the update check where releases are published; change it if the repo moves. To test a build without publishing, use *Actions → Build release → Run workflow*.

## Contributing

Ideas, bug reports and pull requests are welcome. For a bug, please include `OrbitBar.exe --version`, your Windows version, and the end of `%APPDATA%\OrbitBar\orbitbar.log` (right-click the bar → Open log). Keep changes in `orbitbar.py` readable: this project is also used for teaching.

## Config

The config file is `%APPDATA%\OrbitBar\config.json`. You can also open it by right-clicking empty space on the bar and choosing **Edit config**, then **Reload** (changes made in ⚙ Settings or the right-click menu apply instantly; hand edits to the file need the Reload).

| key | what it does |
|---|---|
| `theme` | `"glass"`, `"midnight"`, `"black"`, `"white"`, or `"custom"` (uses `background`) |
| `position` | `"bottom"` or `"top"` |
| `radius` | corner rounding (0 = square) |
| `autohide`, `previews` | `true` / `false` |
| `weather` | `{"city": "Toronto", "units": "celsius", "provider": "auto"}`. `city` can be `"auto"` (find you by IP). `provider` can be `"open-meteo"`, `"wttr.in"` or `"met-norway"` to try that one first. You can also add `"latitude"` and `"longitude"` |
| `world_clocks` | `[["London", "Europe/London"], ...]` |
| `timer_presets` | `[["Focus", 25], ["Break", 5], ...]` |
| `battery` | `{"style": "icon" or "ring", "low_alert": 20, "full_alert": true}` |
| `height`, `margin` | the bar's size and the gap around the floating dock |
| `scale` | size of everything: `0.9` compact, `1.0` normal, `1.15` large, `1.3` extra large, `1.5` huge (right-click › Size) |
| `icon_size` | app icons in the middle, 16–48 px (right-click › Size › App icons); the bar grows taller if needed |
| `widget_size`, `card_size` | `1.0`, `1.15`, `1.3`, `1.5`: bar widgets, and cards/calendar/notifications (right-click › Size) |
| `accent` | highlight color, e.g. `"#7c9cff"` |
| `background` | `[r, g, b, alpha]`, used when `theme` is `"custom"` |
| `clock_24h` | `true` / `false` |
| `pinned` | list of .exe paths. Right-click an app → Pin does this for you. `shell:AppsFolder\<AUMID>` also works for Store apps |
| `layout` | `{"left": [...], "right": [...]}`: widget order on each side (easier with 🧩 Customize widgets…) |
| `widget_variants` | style per widget, e.g. `{"clock": "analog", "cpu": "ring"}` |
| `hide_windows_taskbar`, `hide_on_fullscreen` | behavior toggles |

## 🛡 New in 5.4

**Apps ask for attention.** When Teams, Discord, Telegram, a download or an installer wants you, its icon pulses amber (like the Windows taskbar), then stays amber until you open it. OrbitBar now gets the same "shell hook" messages from Windows that the real taskbar uses, which also makes the app list pick up new and closed windows faster.

**Network safety chip** (next to the security shield). The bar only shows a status, never an address:

| On the bar | Means |
|---|---|
| green 🔒 | your internet traffic goes through a VPN |
| amber ⚠ | open Wi-Fi with no encryption (red ⚠ for WEP, which is just as readable) |
| quiet Wi-Fi / Ethernet icon | normal connection, no VPN (amber if the Wi-Fi uses old WPA/TKIP security) |

Click it for the details: VPN name, Wi-Fi name and security (WPA2/WPA3…), this PC's address, router, DNS servers (Cloudflare, Google, Quad9… named), and when the network last changed. It tells a full VPN apart from one that only reaches its own network, like Tailscale or ZeroTier, and doesn't mistake Hyper-V, WSL, VMware or VirtualBox adapters for VPNs.

- **Your public IP is never looked up on its own.** Press **Look up** in the card and it asks `api.ipify.org` once. The result is masked (`•••.•••.•••.42`); **Show** reveals it for 20 seconds, then it hides again. It's forgotten as soon as the network changes.
- Everything else comes from Windows itself; nothing leaves the PC.
- **Alerts:** a notification when you join open or WEP Wi-Fi (once per network), and when your VPN disconnects. Turn them off in ⚙ Settings → Alerts.
- Existing bars get the chip once, after the shield. Remove it in 🧩 Customize widgets and it stays removed.

## 🔎 Sizes in 5.3

Right-click the bar → **Size**, or ⚙ Settings. Every size changes instantly, with no restart:

| Setting | What it changes |
|---|---|
| **Bar widgets** | weather, timer, Recycle Bin, CPU/RAM/GPU, network, volume, clock, security shield, Start button |
| **Cards & notifications** | the calendar, every card (weather, focus, study, system…), notifications, the media card, the quick launcher |
| **App icons** | the app icons in the middle |

Each has Normal, Large (115 %, the new default), Extra large (130 %) and Huge (150 %). If bigger widgets don't fit on a small screen, the least important ones are tucked away (see Known limits).

**Calendar:** scroll the mouse wheel over it to change month. Scroll down for next month, up for previous. Touchpads work too.

## ⚡ Speed in 5.2

- **Settings apply instantly, with no restart.** Style, accent, position, shape, height, icon size, auto-hide, previews, weather, clock, study and timer settings change in place, in a few milliseconds. Music, focus timers, the tray and open cards keep running. Only **Size of everything** still restarts the bar, because Qt fixes its scale when it starts. The log notes how long each change took ("settings applied live in 12 ms")
- **App icons don't blink.** When an app opens or closes, only its own button is added or removed. Before, every button was rebuilt
- **Focus highlight moves the moment you click** an app or a desktop, without waiting for Windows to confirm
- **Window events are filtered.** Tooltips, menus and child controls no longer trigger a rescan of every window. Title changes (a loading tab, a terminal) only update the tooltip text. A burst of events can't hold updates back any more
- **Nothing slow runs on the bar's own thread:** the Recycle Bin count (which can wake a sleeping disk), the Start-menu scan for the quick launcher, and the launcher's icons, which are now cached so typing stays instant. Desktop switching no longer pauses the bar between steps
- **Less redrawing every second:** the focus timer and study chips no longer re-style themselves each tick, and open cards only rebuild rows that changed, all in one frame
- **Lighter background work:** GPU counter names are parsed once, the camera/mic registry scan runs every ~6 s instead of every second (the live detectors cover the rest), and background threads hand control back to the bar more often
- One app-wide style sheet instead of a copy per card and popup
- **Size of everything** is the one setting that restarts the bar. The swap is now a quick cross-fade: cards and menus close, the old bar fades out while the new one fades in, and you never see two bars at once

## Performance notes

- Windows tells the bar when windows open, close, change title or get focus, so the bar updates instantly with no fast polling
- Slow checks run in the background: processes, GPU, security status, camera/mic, Recycle Bin, Start-menu scan and weather
- Animations (charging, low battery) only run while needed and stop when the bar is hidden

## Known limits

- **Not enough room?** If your widgets don't fit the screen (big Size setting, 125 % Windows scaling, lots of study chips), OrbitBar tucks the least important ones away (GPU, Network, World clock, Recycle Bin, Weather…) and shows only the most urgent study chip. They come back by themselves when there's room. 🧩 Customize widgets shows everything so you can choose. The log says what was tucked away.

- It only runs on the primary monitor.
- System tray: apps already running when OrbitBar starts are asked to re-add their icons; the rare app that ignores this shows up after you restart it. Windows' own system icons (network, volume, battery, clock) aren't in the tray card, because OrbitBar has its own widgets for those.
- Store (UWP) apps can show a generic icon.
- Workspace switching sends Ctrl+Win+←/→, so you'll see the normal Windows slide animation.

## About

OrbitBar is made by **IATEK**, a cybersecurity bootcamp and consultancy, and started as a teaching project: everything is in one readable Python file so students can see how a real Windows app talks to the system (window events, the system tray, network and security status). Released under the MIT license.
