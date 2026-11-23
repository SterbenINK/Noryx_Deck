CATALOG: PLUGINS AND MODS (NOT IN THE BUILD)
============================================

Two different kinds:
  plugin (plugins/) — its own program on the PC: lamp, games, monitor, Discord. Goes into the deck's plugins/ folder.
  mod    (mods/)    — a change to the deck itself: the look (colors, shapes, fonts — CSS and variables) and page features (JS).
                      Goes into the deck's mods/ folder; the deck shows «before / after» and lets you choose.
They aren't part of the deck build: only those who need them install them. Easiest from the deck: Settings → «Plugins» or
«Mods» → «Catalog» → «Install» (there too — «Update» and «Roll back»). Or manually — copy the folder into the deck's
plugins/ or mods/.
Changed a plugin or mod — raise its version, add to CHANGES and rebuild the index: python server/catalog.py index.
Remove — «Delete» on the card or delete the folder. A deck update doesn't touch them.

MODS (mods/):
dashboard/ — «Control panel»: the home screen as a grid of panels, the dock hidden (a layout without code).
focus/     — «Focus timer»: a mod with code — its own panel for layouts, message interception (a DeckAPI sample).
warm/      — «Warm graphite»: warm colors and rounded tiles — a sample look mod (mod.json + style.css).

PLUGINS (plugins/):

lamp/     — «Lamp (Arduino)»: desk lamp brightness on an Arduino + XY-MOS with a slider in the dock, the scenario step
            «lamp 60». The sketch — lamp/ClaudeLamp; wiring — in lamp/README.txt.
monitor/  — «Monitor (DDC/CI)»: monitor brightness, contrast and colors from the deck, presets in one tap (Windows — no
            libraries, Linux — needs ddcutil). Turn DDC/CI on in the monitor menu.
discord/  — «Discord (mic and sound)»: mute Discord's microphone / sound from the deck without opening its window (the game
            doesn't minimize) — through Discord's local API, like Elgato Stream Deck. Once: your own app at
            discord.com/developers/applications (New Application → OAuth2: Client ID, Reset Secret → Client Secret,
            Redirects — http://localhost → Save), both values — into Settings → «Plugins» → Discord (easier from the PC:
            open http://127.0.0.1:8765 in a browser), «Connect» → «Authorize» in Discord. The buttons — on the Discord
            tile (instead of key combinations), in the dock and the «Microphone» / «Sound» tiles.
vintage/  — «Vintage Story»: its own game mode — health and treatment (the S&R System mod), satiety, world, hotkeys.
            Needs the Noryx Link game mod (in the folder, client only). Details — vintage/README.txt.
factorio/ — «Factorio»: a mode with factory stats — electricity, pollution, production, science, enemies;
            charts for 1 min / 10 min / 1 h / 10 h and «tops». Into the deck — the factorio folder (plugin.py); into the
            game (Factorio 2.1, Space Age too) — the game-mod/claude-deck-link mod (or claude-deck-link_0.1.4.zip) into
            the game's mods folder (Windows — %APPDATA%\Factorio\mods). Every 5 s the mod writes
            script-output/claude-deck/factorio.json.
satisfactory/ — «Satisfactory»: a mode with factory stats — electricity, batteries, production, AWESOME Sink;
            charts for 1 min / 10 min / 1 h / 10 h and «tops». No game mod of its own: the data comes from the Ficsit
            Remote Monitoring mod (install it via Satisfactory Mod Manager), in the game — /frm http start (or web server
            autostart in the mod settings). Into the deck — the satisfactory folder. Another port / PC — in the plugin
            settings.
screen2/  — «Second screen»: the phone as a second PC monitor (Windows only). Needs a virtual monitor driver —
            once, as administrator: winget install --id=VirtualDrivers.Virtual-Display-Driver -e
            (the monitor is always connected for Windows). The «Second screen» mode — the picture on the whole phone
            screen, the finger — Windows touches (or a mouse — in the plugin settings); pinch — to the deck's home
            (the monitor isn't disconnected, the picture just stops), spread — back. While it's open the phone doesn't
            sleep. Into the deck — the screen2 folder.

How to write your own — plugins/README.txt.

LANGUAGES: every catalog plugin has LANGS = ["en", "uk"] (which languages its texts are in, the first is the main one),
a mod has "langs": ["en", "uk"] in mod.json; NAME / ABOUT / name / about — {"en": …, "uk": …}. Other languages come from
the deck's language packs (catalog/langs/<code>.json → "plugins": {"<id>": {English text: translation}}); plugin lines to
translate — python server/langs.py plugins. The title and text of a layout panel can also be {"en": …, "uk": …}.

A MOD WITH CODE: window.DeckAPI (mod.js)
  DeckAPI.on("message", m => …)     — every message from the PC; returned false — the deck won't handle it (interception)
  DeckAPI.on("press", e => …)       — a tile press {mode, index, item}; false — don't press
  DeckAPI.on("render:home" | "render:mode" | "render:dock" | "render:layout", el => …) — after drawing
  DeckAPI.panel("kind", {name:{en, uk}, opts:[{k, label, def}], render(el, opts, ctx)}) — its own panel for layouts
  DeckAPI.home(el => …)             — its own whole home screen (returned false — the usual one)
  DeckAPI.act(name, data) / send(message) / toast(text) / openMode(id) / goHome() / css(text) / render()
  DeckAPI.S (state, read only), T(en, uk), esc(text), icons
  The phone's role rights are checked by the PC — a mod with code can't bypass them. Sample — mods/focus.
LAYOUT (mod.json → "layout"): {"cols": 12, "rows": 6, "gap": 10, "dock": false, "panels": [{"id", "kind", "x", "y",
  "w", "h", "title", "opts"}]}; kind: top, stats, media, clock, p:<plugin>, time, modes, tiles {mode, size}, text {text, size},
  image {src} and your own from DeckAPI.panel. Sample — mods/dashboard.
