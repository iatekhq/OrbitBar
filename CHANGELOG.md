# Changelog

Newest first. Each `## x.y.z` section becomes the notes of that GitHub release.

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
