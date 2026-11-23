# Notes for Microsoft Store certification

## Restricted capability: runFullTrust (paste into Partner Center when asked why the app needs it)

OrbitBar is a replacement taskbar for Windows 10/11. To do this it must run as a full-trust desktop app:

- It hides the Windows taskbar while it runs and restores it when it closes (including after a crash, through a small watchdog process).
- It lists and switches between open windows and virtual desktops, and shows live window previews, using standard Win32 window APIs and WinEvent hooks.
- It hosts the system tray so background apps' notification-area icons appear on the bar, and reserves screen space for the bar as an app bar (SHAppBarMessage).
- It reads system status (CPU, GPU, network, battery, Windows Security and firewall status, camera/microphone/location usage) to show it on the bar.

None of this is possible in the app container. OrbitBar does not require administrator rights, installs no drivers or services, and does not modify system files.

## Notes for certification (Submission options → Notes for certification)

OrbitBar replaces the Windows taskbar while it is running.

How to test:
1. Launch OrbitBar from Start. The Windows taskbar hides and OrbitBar's bar appears at the bottom of the screen.
2. Right-click empty space on the bar for all options: Settings, styles, sizes, widget gallery, About.
3. To get the normal Windows taskbar back, right-click the bar → Quit OrbitBar. The taskbar returns immediately.

No account, sign-in or test credentials are needed. The app works offline; only the weather widget, the update notice and optional calendar links use the internet. Source code: https://github.com/iatekhq/OrbitBar
