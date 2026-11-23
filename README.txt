NORYX — a stream deck on your phone, the server on your PC

STARTING (this folder — Windows)
  install.bat        — once: creates .venv and installs the libraries (log: logs\install.log)
  start.bat          — the deck in the tray, no window. The icon by the clock: open the deck, autostart, log, update, exit
  start_console.bat  — the same with a log window (for debugging)
  stop.bat           — stop it if there's no tray icon
  firewall.bat       — open the ports for the phone (TCP 8765 — the deck, UDP 8766 — auto-discovery). As administrator
  phone.bat          — kiosk phone over USB (see below)
  On the PC the deck opens at http://127.0.0.1:8765, the address for the phone — in the tray icon menu.
  Builds for others: Noryx.zip (Windows, Python downloads itself) and Noryx-linux.tar.gz (KDE / PipeWire).

PHONE
  The app — Noryx.apk: open http://<PC IP>:8765 on the phone → Settings → «Download APK».
  It finds the PC on Wi-Fi by itself and updates itself when a new APK appears on the PC.
  A new device enters a 6-digit code the first time: the PC shows it as a notification by the clock and as a hint
  on phones already connected. The code lives 5 minutes. The device list, «Forget» and roles — the «Devices» window.
  Roles: «Main deck» (everything), «Game panel» (only the chosen modes, no settings or editing),
  «Monitoring screen» (view only), «Guest» (tiles and player). The PC itself is always the main one.

HOW TO USE
  Modes are sets of tiles: apps, app groups, hotkeys, scripts, scenarios, plugin tiles. «+» — add.
    Hold a mode (¾ s) — name, icon, color, grid size (4–8 × 2–4).
    Your own mode — home screen → «Edit» → «New mode».
  App tile: not running — starts it; running — brings its window to the front; again — minimizes it.
    While the app runs, the tile has its own buttons (Discord, VS Code, Arduino IDE…; your own — the pencil in «Edit»).
  Hold a tile — the «Edit / Rename / To group / Delete» menu. Hold and move — drag.
  Gestures: swipe up/down — the next mode; pinch two fingers in — home screen, spread — back.
  The dock on the right: buttons (modes, edit, settings, mixer, soundpad), PC monitoring, clock, player,
    plugin blocks. Block order and height — in «Edit». The arrow in the bottom right corner hides the dock.
  The bell — notifications and history («Repeat» for a mode, sound, app). The icon next to it — shared clipboard.
  Tap CPU/GPU/RAM — PC diagnostics and the «Deck» tab (deck health, developer report).
  Mode auto-switch — Settings, at the bottom: «an app started / the sound output changed / a time came → mode».
  Clock mode: the deck is unused for 30 min — a full-screen clock; an hour later the screen turns off (Settings → Screen).
  Language — Settings → «Languages»: English and Ukrainian are built in, other languages download from the catalog
  with a tap; hold a language to delete it.

SCRIPTS AND SCENARIOS — «+» → «Script…»
  The editor opens in «Blocks»: a step is a card with fields (combination, wait, text, hold a key, mode,
  launch / close an app, sound output, volume, microphone, lamp). ↑ ↓ — order, ⧉ — copy, ✕ — remove.
  «Text» — the same script as lines: ctrl+c · wait 500 · text Hello! · hold w 1500 · mode Hobby ·
  run OBS · volume Spotify 30 · mic off. Lines starting with ; # // are comments. Ukrainian words work too.
  «How many times» / «Loop»; press the button again — stop. «Check» — one run, the running step is highlighted.

SOUND
  Mixer — app volumes and output choice. The visualizer in the header — the spectrum of what plays on the PC, in cover colors.
  Soundpad — the note button in the dock; sounds (mp3, wav, ogg, flac) — into the sounds\ folder. Order — by file name.
  To make Discord hear the sounds: VB-Audio Virtual Cable, in the soundpad «Sound» → «On air» — CABLE Input,
  «Voice from the microphone — there too» — your microphone; in Discord the input device — CABLE Output.
  GPU load and temperatures — if LibreHardwareMonitor is running (as administrator)
  with Options → Remote Web Server turned on.

PLUGINS — things that ADD a feature (lamp, games, monitor, Discord, phone as a second screen)
  Settings → «Plugins»: on / off, permissions (what the plugin may do), «Delete» (erased for good).
  «Catalog» — plugins from the repository: «Install», «Update to …» with what's new, «Roll back» together with settings.
  The deck checks the catalog every 6 hours by itself. How to make your own plugin — plugins\README.txt.
  How to set up a particular plugin (lamp, monitor, Vintage Story…) — the README in its folder (catalog\plugins\<name>).
  The card shows which languages the plugin speaks («EN · UK»).
  A plugin is a program on the PC: install only your own or trusted ones.

MODS — things that CHANGE the deck itself (look, layout, behavior)
  Settings → «Mods». The deck shows a new mod right away (a folder put into mods\ and the deck restarted, or from the
  «Catalog») with a «Before / After» switch — you choose «Keep the new one» or «Restore as it was».
  «New look» — no code, with a finger, the deck changes before your eyes:
    look — ready themes, accent, background (color, gradient, pattern, your photo), tile shape and colors, dock, font;
    «Layout» — the home screen as a grid of panels (deck buttons, time, modes, tiles, player, monitoring, plugin
    blocks, text, picture); drag a panel — move it, the corner — size; the dock can be hidden.
  Mods with code (mod.js) can do more — intercept actions, add their own panels (description — catalog\README.txt).
  A mod with code runs with the deck page's rights: install only your own or trusted ones.

KIOSK PHONE — a separate phone just for the deck (without the Android home screen)
  1. On the phone: «Developer options» → «USB debugging»; remove the accounts (Google, Huawei ID) and the screen PIN.
  2. Cable to the PC → phone.bat. The script downloads adb itself, installs the app, removes the extras and turns the kiosk on.
  Turn it off — deck Settings → «Android app» → «Kiosk» or phone.bat off;
  release the phone completely — phone.bat release, bring back what was removed — phone.bat restore.
  An old phone (WebView 83) — update «Android System WebView»: phone.bat webview <file.apk>.

WAKE THE PC FROM THE PHONE (Wake-on-LAN)
  No connection to the PC for 12 s while the deck is in use — the phone sends a «magic packet» (only on the same network).
  Turn it off — Settings → «Wake the PC over the network». On the PC once: Wake-on-LAN in BIOS/UEFI and on the network card
  (Device Manager → adapter → «Power Management» and «Advanced» → Wake on Magic Packet). Usually over cable only.

UPDATE — Settings → Updates
  The deck checks the main branch on GitHub by itself and says «Update available». «Update» (or the tray icon → «Update the
  deck») downloads, installs libraries if needed and restarts. «Restore the previous version» — rollback.
  «Update from file…» — without internet: Noryx.zip, Noryx-linux.tar.gz or a source zip from GitHub.
  Log — logs\update.log.
  The update doesn't touch settings, sounds, your plugins and mods.

FILES
  data\config.json — modes, tiles, settings      data\phones.json — own devices (key hashes only)
  plugins\ — plugins      mods\ — mods      langs\ — downloaded languages      sounds\ — soundpad sounds
  logs\ — logs (server.log, update.log)
  A plugin put into mods\ or a mod in plugins\ is moved to the right place by the deck at startup. Cards carry a «Plugin» / «Mod» tag.
  Something's wrong — logs\server.log or tap CPU in the dock → «Deck» → «Developer report».
