NORYX PLUGINS
=============

A plugin adds its own buttons, toggles, sliders and tiles to the deck. Put a file into this folder — in a couple of
seconds the deck finds it and shows it on the phone. Turn the plugin off in settings (the «Plugins» button in the
header — a separate page) or delete the file — everything it added goes away. Its tiles in modes hide and come back
if the plugin is turned on again. The «Plugins» page also has a master switch — all plugins at once.

WARNING: a plugin is an ordinary Python program, it runs on the PC with the same rights as the deck.
Install only your own plugins or ones whose code you've read and whose author you trust.

The sample is _example.py; ready plugins outside the build (lamp, monitor, Discord, games) — in the catalog/plugins/
folder of the repository.


WHERE A PLUGIN LIVES
--------------------
  plugins/<name>.py          — a single-file plugin;
  plugins/<name>/plugin.py   — a folder plugin (you can put your own modules next to it: import helper finds helper.py).
The name — Latin letters, digits, «-» and «_». Files and folders whose name starts with «_» or «.» aren't loaded.
Changed the file — the deck restarts the plugin by itself. The «Rescan» button in settings — check the folder now,
«Plugins folder» — open it on the PC.

A deck update replaces only its own files (_example.py, _vintage_demo.py, README) — it doesn't touch your plugins.


HOW A PLUGIN IS BUILT
---------------------
    NAME = {"en": "Counter", "uk": "Лічильник"}        # or just a string
    ABOUT = {"en": "What it does", "uk": "Що він робить"}
    LANGS = ["en", "uk"]       # which languages its own texts are in (required; the first is the main one). Other
                               # languages come from the deck's language packs: plugins["<id>"] {English text: translation}

    class Plugin:
        def __init__(self, api): ...       # api — the link to the deck (below)
        def start(self): ...               # turned on / the deck started (can be async def)
        def stop(self): ...                # turned off / deleted / the deck closes — release devices and threads
        def ui(self): return {...}         # what to show on the phone (below)
        def action(self, id, value): ...   # pressed (can be async def)

Only the Plugin class is required; the rest is optional. Methods are called in the deck's thread: do long work
(network, devices) in your own thread and call api.call(…), api.update() from there.


API VERSION AND PERMISSIONS
---------------------------
    API = 2                       # which deck API version the plugin is written for
    PERMS = ["network", "input"]  # what the plugin needs

Permissions: input (keyboard and mouse, api.script), launch (launching programs), native (ctypes and system calls),
screen (capturing the screen), files (files outside its folder), network (network), devices (COM / USB / monitors),
audio (sound), windows (windows), system (load, temperatures, processes).
The deck shows them on the «Plugins» page and asks for consent before the first start. It also looks at the code itself
(imports of socket, subprocess, ctypes…, calls to api.script): what's visible but not declared is marked «*» and also
needs consent. Updated a plugin and it asks for more — it stops until the user agrees again.
api.script without «input» won't work. This is a description and consent, not a lock: a plugin is still code on the PC.
API newer than the deck — the plugin won't start (update the deck); no API given — it counts as 1.


LANGUAGES
---------
Texts go through api.tr("English", "Українська", *values): the first is the plugin's main language, the second —
Ukrainian, any other deck language — from the language pack (plugins["<id>"]), no translation — the first.
Values — by substitution: api.tr("Port {0}", "Порт {0}", port) (not an f-string — otherwise a pack can't find the line).
NAME / ABOUT / the mode name — {"en": …, "uk": …}; you can add your own "<code>" (de, fr…).
All English lines of the plugins from the repository — python server/langs.py plugins.
The card on the «Plugins» page shows LANGS («EN · UK»); yellow — the plugin doesn't speak the deck's language.


UI(): WHAT TO SHOW
------------------
    {"dock": [...],        # the plugin block in the dock (on the right); it can be moved and resized in «Edit»
     "settings": [...],    # its own settings — in the plugin card on the «Plugins» page
     "screen": [...],      # its own full-deck screen (opened by a tile with "screen": True or a button in settings)
     "tiles": [...],       # tiles that can be put into any mode (the «+» button → «Plugins»)
     "ctx": [...],         # buttons on an app tile (below)
     "theme": {...}}       # its own look for the block, screen and settings (below)

Elements (dock and settings):
  {"type": "button", "id": "go", "label": "Text", "icon": "plus", "on": False, "disabled": False,
   "style": "lamp" | "dashed", "wide": True}
        a button; action(id, None). style "lamp" — square with an icon, "dashed" — dashed, full width.
  {"type": "toggle", "id": "x", "label": "Text", "on": True}
        a toggle; action(id, True/False) — the new state.
  {"type": "slider", "id": "v", "label": "Volume", "value": 30, "min": 0, "max": 100, "step": 1,
   "unit": "%", "off": False, "hint": "off"}
        a slider; action(id, number) — while dragging, at most ~16 times a second, and at the end.
        off / hint — the slider is «dimmed» and shows a caption instead of the number. color "#RRGGBB" — its own fill
        color (the bigger the value, the more saturated).
  {"type": "text", "label": "Caption", "value": "value"}
        a «caption — value» line.
  {"type": "row", "items": [...]}
        elements in a row.
  {"type": "input", "id": "save", "label": "", "placeholder": "Name", "button": "Save"}
        an input field with a button; action(id, text) — on the button or Enter.
For your own look (bars, stats, cards, pictures):
  {"type": "bar", "label": "Health", "icon": "❤", "value": 12, "max": 15, "color": "#C8432F", "size": 14,
   "text": "12 / 15"}
        a bar; parts: [{"value": 300, "color": "#5E9A3A", "label": "Vegetables"}, …] — of several pieces (up to 8).
  {"type": "stat", "icon": "🌡", "label": "Body", "value": "36.6", "unit": "°C", "sub": "normal", "color": "#7FB2D9"}
        a stat: a big value with a caption.
  {"type": "card", "title": "Character", "icon": "🧍", "color": "#D9B26A", "bg": "#2B221A", "items": [...]}
        a card with a title; inside — any elements.
  {"type": "grid", "cols": 3, "items": [...]}
        a grid of cols columns (1–8) — cards, stats.
  {"type": "label", "label": "POSITION"}
        a small heading.
  {"type": "image", "src": "icons/map.png", "w": 120, "h": 120, "pixel": True, "alt": "map"}
        a picture: a file from the plugin folder (png / jpg / webp / gif) or "data:image/png;base64,…" (up to 300 KB);
        pixel — no smoothing (pixel art).
An icon (icon) anywhere — a name from the list below, an emoji / 1–2 letters or a picture from the plugin folder
("icons/hp.png"). Colors — only "#RRGGBB" or "#RRGGBBAA".

Your own look (theme), for example as in a game:
  {"bg": "#1E1812", "panel": "#2B221A", "border": "#4A3A2A", "text": "#E8D9B5", "muted": "#A8977A",
   "accent": "#D9B26A", "radius": 6, "font": "serif", "texture": "wood.png", "pixel": True}
        background, panels (cards, stats, buttons), borders, text, muted text, accent, rounding (0–28),
        font: "sans" / "serif" / "mono" or a font file from the plugin folder ("fonts/x.woff2", .ttf, .otf);
        texture — the background picture of the block and screen (repeated), pixel — texture and pictures without smoothing.
Files (pictures, fonts) — only for a folder plugin (plugins/<name>/…), the deck gives them to the phone only while
the plugin is on. Use only game pictures you're allowed to (your own or with the author's permission).
A «game-like» sample — _vintage_demo.py: a Vintage Story–style character panel, a screen with cards,
it waits for data from a game mod as JSON over UDP (127.0.0.1:47811).

Icons (icon): bulb, plus, power, play, pause, stop, next, prev, mic, micOff, spk, bell, gear, lock, monitor,
wifi, bt, keys, script, note, check, x, edit, pen, grid, layout, chip, gauge, clip, shot, repeat, shuffle, up,
left, right. Any other string (1–2 letters, emoji) is shown as text.

Its own mode (mode) — the plugin adds a mode next to «Normal», «Hobby», «Hotkeys»:
  "mode": {"name": {"en": "Game", "uk": "Гра"}, "sub": "caption on the home screen", "icon": "gamepad", "color": "#D9B26A"}
        in the mode, instead of tiles — the plugin screen (screen), the mode header — like everyone's. It doesn't close
        apps on entering (like «Hotkeys»). Name, icon and color change by holding the mode. Plugin off — the mode is gone.
Layout and presses (for the mode / screen):
  {"type": "grid", "widths": [1, 3], "fill": True, "scroll": True, …}
        widths — column widths (here the second is three times wider); fill — full height; scroll — its own scrolling.
  card / bar / stat with "id" — pressable (action(id, None)), "on": True — selected (highlighted);
  "go": "mode" | "screen" — a press opens the plugin's mode / screen on this phone (a dock widget).
  {"type": "card", "scroll": True, …} — a card with its own scrolling.
  {"type": "label", "style": "line", "label": "…", "color": "#A0D0FF"} — a plain line of text (not a heading).
  {"type": "select", "id": "k", "label": "What", "value": "a", "options": [{"value": "a", "label": "A"}, …]}
        a picker; action(id, value).
  {"type": "textarea", "id": "t", "value": "…", "rows": 5, "button": "Save", "label": "hint"}
        a multi-line field (a script); action(id, text) — with the button.
  {"type": "shapes", "vw": 150, "vh": 290, "h": 300, "items": [shapes]}
        a doll / diagram of shapes in vw×vh coordinates; pressing a shape with an id — action(id, None). A shape:
        {"id": "zone:1", "x": 50, "y": 54, "w": 50, "h": 88, "r": 3, "fill": "#9E8461", "stroke": "#2B1F14",
         "bevel": True, "tex": True, "sel": True, "dash": False, "band": 0.5, "bandH": 0.14, "splint": True,
         "badges": ["blood", "alert"], "label": "Torso"}
        bevel — a bevel, tex — «grain», sel — a gold frame, dash — dashed, band — a bandage (position 0–1, height
        bandH), splint — a splint, badges — a blood drop / «!» / 1–4 characters; {"kind": "ellipse", …, "opacity": 0.3} — a shadow.

Tiles (tiles):
  {"id": "go", "label": "Name", "icon": "plus", "sub": "caption below", "on": False}
        a press — action(id, None); on — the tile «glows» in the mode color; "screen": True — a press opens the plugin
        screen (screen) instead of action.

Chart (chart) — lines by points:
  {"type": "chart", "label": "Electricity", "sub": "Nauvis", "unit": "W", "si": True, "h": 170,
   "xs": ["-10 min", "-5 min", "now"], "min": 0, "max": 100,
   "series": [{"label": "Generation", "color": "#3DDC6A", "fill": True, "values": [1.2e6, 1.3e6, None, 1.1e6]}, …]}
        up to 6 lines of 300 points; None — a gap; si — 1.2 kW / 3.4 MW; min / max — axis limits (otherwise automatic);
        under the chart — the xs captions and a legend with the last value. Sample — the catalog/plugins/factorio plugin.

Buttons on an app tile (ctx) — instead of its key combinations, while the app is running (up to three):
  {"procs": ["discord", "vesktop"], "id": "mute", "label": "Microphone", "on": False}
        procs — process names without .exe; a press — action(id, None), on — the button glows. Sample — the
        catalog/plugins/discord plugin.

Texts are shown as text (not HTML). The deck drops unknown kinds and fields.


API
---
  api.id, api.folder       — the plugin id and its folder
  api.lang, api.tr(en, uk, *values) — the deck language ("en", "uk", "de"…) and the text in it (see LANGUAGES)
  api.cfg                  — its own settings (a dict, saved in the deck config); changed — api.save()
  api.deck_cfg             — the deck settings (read only)
  api.update()             — redraw on the phones (after action this happens by itself)
  api.toast(text)          — a short hint on the phone
  api.event(text, level="info"|"warn"|"error", notify=False) — a history entry (notify — a notification too)
  api.later(sec, fn)       — call fn() later in the deck's thread
  api.call(fn)             — call fn() in the deck's thread from your own thread
  api.log                  — a logging logger (writes to the deck log)
  api.script(text, label, key) — a deck script (like a script button: ctrl+c / wait 500 / text …); the same key
                             while it runs — stop. Keys go to the window active on the PC.

If a plugin crashed while loading or starting — the error is visible in its card, the other plugins and the deck work.
An error in action() — a hint on the phone.

DEFAULT_ON = False in the plugin file — a new plugin appears turned off (like the lamp).

Plugins outside the build (for example «Lamp (Arduino)», «Monitor (DDC/CI)») — in the catalog/plugins/ folder of the
repository; the deck installs and updates them itself: Settings → «Plugins» → «Catalog». For the catalog, in the
plugin's plugin.py:
    VERSION = "1.1.0"                                         # raise it — decks will offer «Update»
    CHANGES = [("1.1.0", "what's new", "що нового"), …]       # newest first; the phone shows what's newer than installed
    OS = ["windows"]                                          # only these OSes (no line — any)
    LANGS = ["en", "uk"]                                      # required in the catalog
and rebuild the index: python server/catalog.py index (catalog/index.json; the CI check makes sure it's fresh).
An update keeps the previous version with its settings in plugins/_backups/<id>/ — «Roll back» in the catalog.

Scenarios: the step «lamp 60» is the lamp plugin's level action with the value 60.
