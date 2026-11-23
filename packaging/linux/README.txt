NORYX for Linux — a stream deck on your phone for your PC
Made for KDE Plasma 6 (Wayland) with PipeWire, as in Nobara / Fedora. Needs python3 — Fedora/Nobara already have it.

INSTALLATION
1. Unpack the archive, for example into your home folder — you get ~/Noryx
   (Dolphin: right-click the archive → «Extract» → «Extract archive here»).
2. Run start.sh: in Konsole — cd ~/Noryx && ./start.sh (or double-click start.sh → «Execute»).
   • The first time it installs the libraries into the .venv folder next to it (~60 MB, 1–2 minutes, internet needed).
     Nothing is installed into the system, no root password needed.
   • At the end it shows the address for the phone. «Noryx» appears in the app menu — start it from there from now on.
   • By the clock — the deck icon: right-click — open the deck, «Start at login», log, restart, exit.
3. Once, if you like: sudo ./system-setup.sh — opens the firewall ports (if it blocks the phone),
   gives access to the virtual keyboard for hotkey buttons and to Arduino (for the lamp plugin). Then log in again.
   start.sh tells you itself if this is needed.

PHONE (Android, the same Wi-Fi as the PC)
1. Send Noryx.apk from this folder to the phone (for example, to your Telegram «Saved Messages») and install it.
   The phone may ask to allow installs from this source, and Play Protect may warn
   about an unknown app: «Install anyway».
2. Open the app — it finds the PC by itself. It didn't — «Enter the address» and type the address start.sh showed
   (it's also in the tray icon tooltip). It also works without the app: open that address in the phone's browser.
3. The first time the phone asks for a 6-digit code — the PC shows it as a notification by the clock (and as a hint on
   phones already connected). After that the phone remembers itself. Own devices and «Forget» — the «Devices» window.

WHAT IT CAN DO
• Modes with buttons: apps, app groups, macros, hotkeys. The «+» button — apps from the app menu,
  including Flatpak and Steam games. «Pick an app on the PC…» — for AppImage, scripts and .desktop files.
  On the first run, apps from the default set that aren't on this PC are removed by themselves.
  A mode can start its apps by itself and close the apps of another mode —
  turn this on in Settings: «Autostart the base» and «Close other apps».
• The «Hotkeys» mode (turned on in Settings → Features): small tiles (8×4) with key combinations —
  add your own with «+», «Check» presses the combination on the PC right away; at the bottom you can switch
  to the on-screen keyboard or the numpad. A hotkey can also go into a regular mode: «+» → «Hotkey…».
• Scripts like AutoHotkey: «+» → «Script…» — one button presses combinations in turn with pauses, types
  text, holds keys. A line is a step: ctrl+c / wait 500 / text Hello! / hold w 1500.
  Repeat N times or «Loop»; press the button again — stop. «Check» — one run on the PC right away.
  Text is typed with key presses, so only Latin letters and with the English layout.
• Mode window — hold the mode (¾ second): name, icon, color and tile size — horizontally 4–8,
  vertically 2–4 (4×2 by default, 8×4 for «Hotkeys»). Swipe up/down — the next mode.
• Language — Settings → «Languages»: English and Ukrainian are built in, others download from the catalog with a tap
  (the icon menu, the log and the app menu shortcut too).
• Your own modes: on the home screen «Edit» → «New mode» (name, icon, color). There too — the order
  (two tiles in a row — swap them), the pencil — rename, the cross — delete your mode.
  More than two modes — the tiles become small 2×2 and scroll with a swipe.
  The mode color is chosen only in the mode window — any color: the rainbow circle opens the hue wheel,
  saturation and brightness; «Edit colors» — adjust a color or remove your own.
• A clock with the date in the dock — Settings → «Clock».
• «Edit»: tiles and modes are dragged with a finger; in the right panel — its width and the block order (arrows).
• The tile of a running app shows its window, one more press — minimizes it.
• Clock mode: the deck is unused for 30 minutes — a dimmed full-screen clock; music is playing — controls
  and the visualizer. Tap — back. An hour later the screen turns off. Time and turning off — Settings → Screen.
• Plugins — Settings → the «Plugins» button in the header: turn each on / off, or all at once. Your own plugin — a .py
  file in the plugins folder (how to write one — plugins/README.txt). Install only your own or trusted ones: a plugin is
  a program on the PC.
• Visualizer: color from the track cover or your own — Settings → Screen. In the mode window the ready colors can be
  replaced with your own («Edit colors» → tap a ready one).
• Dock: volume and app mixer (PipeWire), sound output change, player (Spotify / YouTube Music /
  «what's playing» — Settings → Music; works with any player the KDE player widget sees),
  CPU and GPU load and temperatures — by themselves, nothing to install.
• Soundpad — the note button in the dock. Drop sounds (mp3, wav, ogg, flac) into the sounds/ folder
  or press «Add sounds from the PC…».
  To make Discord hear the sounds: «Sound» → «On air» — «Noryx Virtual Mic» (the deck creates this
  virtual microphone itself, no VB-Audio needed), «Voice from the microphone — there too» — your microphone.
  In Discord: Settings → Voice & Video → Input Device — «Noryx Virtual Mic».
  Better turn off Krisp noise suppression in Discord — it cuts the sounds.
• Hotkeys: combination buttons are pressed through a virtual keyboard. «FPS overlay» presses right Shift + F12 —
  that's MangoHud. Screenshot — Spectacle (select an area), «Lock PC» — the lock screen.
• Plugins (Arduino lamp, monitor, Discord, games…) and mods (your own look, a home screen layout of panels) —
  Settings → «Plugins» / «Mods» → «Catalog»: install, update, roll back. Instructions — in each plugin's README.

IF SOMETHING'S WRONG
• The phone doesn't connect — sudo ./system-setup.sh (ports 8765/TCP and 8766/UDP). The phone and the PC must be
  on the same network (the router's guest Wi-Fi won't do).
• Hotkeys aren't pressed — sudo ./system-setup.sh (access to /dev/uinput).
• The lamp plugin says «no access (dialout)» — sudo ./system-setup.sh and log in again.
• No tray icon — GNOME needs the AppIndicator extension; without the icon the deck still works (./stop.sh — exit).
• The installation failed — check the internet and run ./start.sh again. Details: logs/install.log.
  Completely broken — delete the .venv folder and run ./start.sh again.
• The deck doesn't start or glitches — see logs/server.log.
• Stop the deck — tray icon → «Exit» (or ./stop.sh).
• Kiosk phone (a separate phone just for the deck): the phone over USB with debugging, without accounts → ./phone.sh.
  Turn it off — deck Settings → «Kiosk» or ./phone.sh off; bring everything back — ./phone.sh release and restore.

A new phone connects only with the 6-digit code the PC shows. Device roles — the «Devices» window.
Use it at home, not on public Wi-Fi.

UPDATES
10 seconds after starting, the deck checks GitHub. A new version exists — «Update available» pops up
(on the phone and by the clock). To update — tray icon → «Update the deck» or Settings → Updates → «Update»:
the deck downloads the new version, installs libraries if needed and restarts.
It doesn't touch settings (data/) and sounds (sounds/). «Restore the previous version» — rollback.
«Update by itself» (off by default) — install new versions without the button.
Without internet — «Update from file…» (in the tray and in Settings → Updates): a picker window
opens on the PC — choose Noryx-linux.tar.gz or a source zip from GitHub (Code → Download ZIP).
The deck doesn't update a git clone folder itself — use git pull there.
Manually: ./stop.sh, unpack the new version over this folder and run ./start.sh.
Uninstall: turn off «Start at login» in the icon menu, press «Exit», delete the folder
and ~/.local/share/applications/noryx.desktop.
