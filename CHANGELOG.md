# Changelog

Newest first. Each `## x.y.z` section becomes the notes of that GitHub release.

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
