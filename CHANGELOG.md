# Changelog

Newest first. Each `## x.y.z` section becomes the notes of that GitHub release.

## 5.7.5

- **License**: OrbitBar is licensed under the PolyForm Noncommercial License 1.0.0, plus a permission from IATEK to use the official builds anywhere, including at work. It's free to use, study and change for yourself or your class; selling it or putting it in a paid product isn't allowed. See *License* in the README
- **Report a bug… / Suggest a feature…** in the right-click menu and the About window: they open a short GitHub form with your OrbitBar and Windows version filled in. Nothing is sent by itself
- Security problems can be reported privately (SECURITY.md)
- The log writes your Windows user folder as `%USERPROFILE%`, so a log you share doesn't carry your name

## 5.7.4

- **config.json edits apply by themselves**: save the file and the bar updates within a second, no Reload needed (only `scale` restarts the bar, as the Size setting does)
- **A mistake in config.json can't stop the bar any more**: wrong values (`"height": "banana"`, `"position": "left"`, a negative margin) are fixed or ignored, numbers are kept in a working range, and a notification says what was wrong. Click it to open the file
- A typo in the file (a missing comma) while you edit: the bar keeps its current settings and tells you the line. A file that is still broken is copied to `config.broken-<date>.json` before OrbitBar ever saves over it
- config.json is written in one step, so a crash or power cut can't leave half a file; files saved with a BOM (some Notepad versions) are read fine
- **Text from other apps and websites is shown as plain text**: a web page title, song name, Wi-Fi name, tray tooltip or calendar event containing HTML (e.g. `<img src=//server/x>`) is now displayed exactly as written. Before, the bar could render it as HTML and even fetch that file from another computer
- Calendar links must be `https://` or `webcal://` (or a .ics file on this PC), and calendars, weather and update answers are size-limited, so a broken or hostile server can't fill memory
- An update whose version isn't a plain number (like 5.7.4) is refused before anything is downloaded
- Settings saved from the ⚙ Settings window go through the same checks as config.json
- Releases now come with a signed build-provenance record (check a download with `gh attestation verify`), and are published only once the zip is uploaded
- README: an *Official builds* section

## 5.7.3

- The log now shows where start-up time goes, in one line, e.g. `startup: windows loading 2.4s · imports 1.9s · Qt 0.3s · bar built 1.1s · on screen 0.2s (total 5.9s)`
- The microphone check reuses its audio connection instead of opening a new one every 1.5 seconds
- Camera, microphone and location records are re-read every ~12 seconds instead of ~6 (the live camera and mic detectors still react at once)

## 5.7.2

- YouTube autoplay: when the browser starts the next video but keeps reporting the previous video's title and cover (a Brave/YouTube glitch that Windows' own media panel shows too), the music card now notices the length changed and shows the title of the YouTube tab instead
- The **Focus** button follows the Focus length setting right away (it used to keep the old length until a restart)
- A long "Top study apps" list ends with "…" instead of running off the card; hover it for the full list

## 5.7.1

- The music card no longer shows the previous video's time: when a browser switches to a new video it updates the title right away but the position a moment later, so the progress bar now waits (shows --:--) until the new video's time arrives, and never draws a position past the end
- `--check-media` shows each player's position, length and how long ago it reported them

## 5.7.0

- **Location indicator**: a blue 📍 badge appears next to the camera and microphone ones while an app uses your location, with a notification like the camera one
- **App permissions** in the privacy card: for camera, microphone and location, which apps are allowed, which you blocked (e.g. an app you denied the camera), whether desktop programs are allowed, and who used it in the last week
- Open it any time from the security shield → *App permissions*, even when nothing is in use
- `--check-privacy` now lists location and every app's permission too

## 5.6.2

- Clicking a desktop far away (e.g. 1 → 4) jumps straight there in one transition, instead of sliding through 2 and 3 (falls back to the old way on Windows builds that don't allow it)
- New desktops show up on the bar right away, whichever desktop you're on: the bar now reads the live list Task View uses, instead of a copy Windows only saves now and then (that copy is still the fallback)

## 5.6.1

- Size of everything: going back to **Normal** after Large or Extra large works again (the bar used to stay large)
- Closing a desktop no longer slides you through it: OrbitBar removes it directly and you stay where you are (falls back to the old way on Windows builds that don't allow it). Close 4, 3 and 2 from desktop 1 and you never leave desktop 1
- A little less background work when running as OrbitBar.exe

## 5.6.0

- New **About OrbitBar** window (right-click the bar): version, who makes it, links to the source code, what's new and bug reports, and an optional "Buy me a coffee" button. Nothing ever pops up by itself; OrbitBar stays free with nothing locked

## 5.5.9

- "Start with Windows" works again after you move OrbitBar or switch between the .py and the .exe: the startup shortcut kept pointing at the old copy, so nothing started at boot while the menu said it was on. OrbitBar now checks the shortcut every time it starts and points it at itself

## 5.5.8

- The Windows taskbar no longer flashes for a moment when the mouse reaches the bottom of the screen (noticeable with Task Manager open): OrbitBar now hides it again the instant Windows shows it

## 5.5.7

- Fullscreen VLC (and other players): grabbing the seek bar or opening a menu no longer brings the bar up over the video's controls
- Leaving fullscreen (Esc or double-click): the bar is back almost instantly, instead of showing the wallpaper in its place for a moment

## 5.5.6

- The Settings window opens wide enough for every row: with Large or bigger text sizes, the dropdowns and accent colours used to be cut off on the right

## 5.5.5

- Testing automatic updates

## 5.5.4

- Automatic updates are installed by the new OrbitBar.exe itself, no PowerShell script any more (in 5.5.2/5.5.3 the hidden script didn't run on some PCs, and the bar then didn't come back)
- If installing an update ever fails, OrbitBar starts the version you have instead of trying again at every start
- If copying the new files fails halfway, the old version is put back

Coming from 5.5.2 or 5.5.3? Download this one by hand once.

## 5.5.3

- Testing automatic updates

## 5.5.2

- **Updates install themselves.** When a new version is published, OrbitBar downloads it in the background, checks it against GitHub's own SHA-256 checksum, and installs it the next time it starts. Or click the notification to restart and update right away. Turn it off in ⚙ Settings → Alerts
- Clicking an update notification now does something (before, it only closed it)
- Scrolling down the Settings page no longer changes the dropdowns under the mouse (Bar widgets used to jump to Huge 150 % on the way to the Study section)

From this version on, updating is automatic. Coming from 5.5.0 or 5.5.1? Download this one by hand once.

## 5.5.1

- Jump around in a song: in the media card (hover the music on the bar), click or drag the progress bar, scroll over it for 5-second steps, or use −10 / +10
- ⟲ plays the current song from the beginning
- Works with players that allow it (YouTube and most sites in Chrome/Edge/Firefox, most music apps); when a player doesn't, the controls are greyed out

## 5.5.0

First public release by IATEK.

- Ready-to-run download: unzip and run `OrbitBar.exe`, no Python needed
- The bar tells you when a new version is published (twice-a-day check of GitHub Releases, can be turned off in Settings; it never downloads anything by itself)
- Starts faster: the bar is drawn before the audio library loads, and the quick launcher is prepared in the background
- New app icon
- Win+D / Show desktop no longer hides the bar after it has been running for a while (it now also notices when it lost "always on top" or the desktop was raised over it, and comes straight back)
- Quieter log: the antivirus check falls back to PowerShell once instead of reporting it every time, and tray hand-over messages are summarized

## 5.4.2

- The bar stays on screen through Win+D / Show desktop
- The desktop, Start, search and Task View are never mistaken for a fullscreen app

## 5.4.1

- Tray icons survive reloads; the bar re-asks apps for their icons if another tray window gets in front
- "Missing an app? Refresh" in the tray card, and `--check-tray` for troubleshooting

## 5.4.0

- Apps asking for attention (new message, finished download) pulse amber, like on the Windows taskbar
- Network safety chip: VPN on/off, warnings on open or WEP Wi-Fi, details card with DNS, router and network changes. Your public IP is only looked up when you ask, and shown masked

## 5.3.0

- Separate live sizes for bar widgets and for cards, calendar and notifications (Large by default)
- Bigger notifications, calendar and volume
- Scroll over the calendar to change month

## 5.2.1

- Changing "Size of everything" cross-fades between the old and new bar
- Crash watchdog: if OrbitBar ends unexpectedly, the Windows taskbar comes back within about 3 seconds

## 5.2.0

- Settings apply instantly, with no restart
- App icons no longer blink when apps open or close
- Slow checks (Recycle Bin, Start-menu scan) moved off the bar's own thread; less redrawing every second
