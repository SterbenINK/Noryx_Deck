NORYX — a stream deck on your phone for your PC
You need: Windows 10/11 (64-bit), an Android phone on the same Wi-Fi.

INSTALLATION
1. Unpack the archive into a regular folder, preferably C:\Noryx
   (right-click the archive → «Extract All…» → type C:\Noryx). Don't run it straight from the archive.
2. Run Start.bat.
   • The first run downloads Python and the libraries itself (~40 MB, 1–3 minutes, internet needed).
     Everything goes into this folder only — nothing is installed into Windows.
   • At the end Windows asks «Do you want to allow this app to make changes?» (Windows Command
     Processor) — press «Yes»: this opens the firewall ports so the phone can reach the PC
     (only for devices on your home network).
   • «Windows protected your PC» popped up — «More info» → «Run anyway».
     To avoid it: before unpacking, right-click the archive → Properties → tick «Unblock» → OK.
3. Done: the deck icon appeared by the clock. Right-click it — open the deck, «Start with Windows»,
   log, exit. From now on start it the same way, with Start.bat (or turn on autostart in the icon menu).

PHONE
1. Send Noryx.apk from this folder to the phone (for example, to your Telegram «Saved Messages») and install it.
   The phone may ask to allow installs from this source, and Play Protect may warn
   about an unknown app: «Install anyway».
2. Open the app — it finds the PC by itself. It didn't — «Enter the address»: the address is shown in the window
   after the first run and in the tray icon menu («From a phone: http://…:8765»).
   It also works without the app: open that address in the phone's browser (an iPhone works too).
3. The first time the phone asks for a 6-digit code — the PC shows it as a notification by the clock (and as a hint on
   phones already connected). After that the phone remembers itself. Own devices and «Forget» — the «Devices» window.

IF SOMETHING'S WRONG
• The phone doesn't connect — run Firewall.bat (it asks for administrator rights itself).
  If you have an antivirus with its own firewall (Kaspersky, ESET, Avast…) — allow Python from this folder in it.
  The phone and the PC must be on the same network (the router's guest Wi-Fi won't do).
• The installation failed — check the internet and run Start.bat again. Details: logs\install.log.
  Completely broken — delete the python folder and run Start.bat again.
• The deck doesn't start or glitches — see logs\server.log.
• Stop the deck — tray icon → «Exit» (or Stop.bat).

WHAT IT CAN DO
• Modes with buttons: apps, app groups, macros, hotkeys. Added from the phone with the «+» button.
  On the first run, apps from the default set that aren't on this PC are removed by themselves.
  A mode can start its main apps by itself and close the apps of another mode —
  turn this on in Settings: «Autostart the base» and «Close other apps».
• The «Hotkeys» mode (turned on in Settings → Features): small tiles (8×4) with key combinations —
  add your own with «+», «Check» presses the combination on the PC right away; at the bottom you can switch
  to the on-screen keyboard or the numpad. A hotkey can also go into a regular mode: «+» → «Hotkey…».
• Scripts like AutoHotkey: «+» → «Script…» — one button presses combinations in turn with pauses, types
  text, holds keys. A line is a step: ctrl+c / wait 500 / text Hello! / hold w 1500.
  Repeat N times or «Loop»; press the button again — stop. «Check» — one run on the PC right away.
• Mode window — hold the mode (¾ second): name, icon, color and tile size — horizontally 4–8,
  vertically 2–4 (4×2 by default, 8×4 for «Hotkeys»). Swipe up/down — the next mode.
• Language — Settings → «Languages»: English and Ukrainian are built in, others download from the catalog with a tap.
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
  file in the plugins folder (how to write one — plugins\README.txt). Install only your own or trusted ones: a plugin is
  a program on the PC.
• Visualizer: color from the track cover or your own — Settings → Screen. In the mode window the ready colors can be
  replaced with your own («Edit colors» → tap a ready one).
• Dock: volume and mixer, player (Spotify / YouTube Music / «what's playing» — Settings → Music),
  CPU and RAM load.
• GPU load and temperatures — if LibreHardwareMonitor is running (as administrator)
  with Options → Remote Web Server turned on (port 8085).
• Soundpad — the note button in the dock. Drop sounds (mp3, wav, ogg, flac) into the sounds\ folder
  or press «Add sounds from the PC…».
  To make Discord hear the sounds: install VB-Audio Virtual Cable (free, vb-audio.com/Cable),
  in the soundpad «Sound» → «On air» — CABLE Input, «Voice from the microphone — there too» — your microphone.
  In Discord the input device — CABLE Output.
• Plugins (Arduino lamp, monitor, Discord, games…) and mods (your own look, a home screen layout of panels) —
  Settings → «Plugins» / «Mods» → «Catalog»: install, update, roll back. Instructions — in each plugin's README.
• Kiosk phone (a separate phone just for the deck): the phone over USB with debugging, without accounts → Phone.bat.
  Turn it off — deck Settings → «Kiosk» or Phone.bat off; bring everything back — Phone.bat release and restore.

A new phone connects only with the 6-digit code the PC shows. Device roles — the «Devices» window.
Use it at home, not on public Wi-Fi.

UPDATES
10 seconds after starting, the deck checks GitHub. A new version exists — «Update available» pops up
(on the phone and by the clock). To update — tray icon → «Update the deck» or Settings → Updates → «Update»:
the deck downloads the new version, installs libraries if needed and restarts.
It doesn't touch settings (data\) and sounds (sounds\). «Restore the previous version» — rollback.
«Update by itself» (off by default) — install new versions without the button.
Without internet — «Update from file…» (in the tray and in Settings → Updates): a picker window
opens on the PC — choose Noryx.zip or a source zip from GitHub (Code → Download ZIP).
Manually: close the deck (icon → «Exit»), unpack the new version over this folder and run Start.bat.
Uninstall: turn off «Start with Windows» in the icon menu, press «Exit» and delete the folder.
