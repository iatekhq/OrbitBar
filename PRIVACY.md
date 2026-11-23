# OrbitBar privacy policy

*Effective November 21, 2026 · Published by IATEK, Toronto, Canada*

OrbitBar is a taskbar for Windows. This page explains what it reads on your PC, what (little) it sends over the internet, and what it never does.

## The short version

- **No account, no ads, no analytics, no tracking.** OrbitBar doesn't collect anything about you and has no server of its own.
- **Everything it shows is read on your PC and stays on your PC.**
- It only goes online for **weather**, **checking for updates**, **calendar links you add yourself**, and a **public IP lookup when you press the button for it**. All of these are listed below, with what is sent.

## What OrbitBar reads on your PC (never sent anywhere)

To work as a taskbar, OrbitBar reads information from Windows while it runs. None of it leaves your computer.

- **Open windows and apps**: their names and icons, to show your apps on the bar. Window titles are shown in tooltips and previews but never saved.
- **Media**: the title, artist and cover art of what's playing, from Windows' own media controls.
- **System status**: CPU, memory, GPU, disk, network speed and battery levels.
- **Security status**: whether Windows Security (or your antivirus) and the firewall are on.
- **Network details**: Wi-Fi name and security type, VPN on/off, DNS servers and router, for the network safety chip.
- **Privacy indicators**: which apps are using your camera, microphone or location right now, and the app permissions Windows records for them, so OrbitBar can show them to you.
- **System tray icons** of your background apps.
- **Your study calendar and deadlines**, from links or entries you add in Settings.

## What OrbitBar stores on your PC

All in your own Windows user folders, readable only by you:

- `%APPDATA%\OrbitBar\config.json`: your settings.
- `%APPDATA%\OrbitBar\study.json`: if study tracking is on, daily totals of study and distraction time with app and website **names** only. Window titles are never stored. You can turn this off in Settings → Study.
- `%APPDATA%\OrbitBar\orbitbar.log`: a technical log for troubleshooting. It stays on your PC unless you choose to share it.
- `%LOCALAPPDATA%\OrbitBar\update`: a downloaded update, until it is installed (downloaded version only).

Deleting these folders removes everything OrbitBar has stored.

## When OrbitBar connects to the internet

| What | Where it connects | What is sent | When |
|---|---|---|---|
| Weather | Open-Meteo (api.open-meteo.com), OpenStreetMap Nominatim, wttr.in, MET Norway (api.met.no) | The city you set (default Toronto) or its coordinates. If you set the city to "auto", wttr.in estimates your city from your IP address. | Every 15 minutes while the weather widget is on the bar |
| Update check | GitHub (api.github.com, github.com) | Nothing about you: it asks for the latest OrbitBar version and, for the downloaded version, downloads it | Twice a day; turn off in Settings → Alerts. The Microsoft Store version is updated by the Store instead. |
| Your calendar links | The school or calendar sites whose links **you** paste (Moodle, Brightspace, Canvas, Google, Outlook…) | A normal request for that calendar | Every 30 minutes, only if you added links |
| Public IP lookup | api.ipify.org | A request for your public IP address, which is shown masked and forgotten when your network changes | Only when you press **Look up** in the network card |

Like any website you visit, these services can see your IP address when OrbitBar contacts them. Their own privacy policies apply to that. OrbitBar sends them no identifier, cookie or account information.

Links you click inside OrbitBar (for example GitHub, Buy Me a Coffee or Windows Settings) open in your browser or in Windows.

## Children

OrbitBar is suitable for all ages and collects no personal information from anyone, including children.

## Open code

OrbitBar's source code is public (PolyForm Noncommercial license). You can check everything above in the code at [github.com/iatekhq/OrbitBar](https://github.com/iatekhq/OrbitBar).

## Changes

If this policy changes, the new version will be posted here with a new effective date.

## Contact

IATEK · [iatek.ca/contact](https://iatek.ca/contact) · or open an issue on [GitHub](https://github.com/iatekhq/OrbitBar/issues). Security problems: please report them privately, see [SECURITY.md](https://github.com/iatekhq/OrbitBar/blob/main/SECURITY.md).
