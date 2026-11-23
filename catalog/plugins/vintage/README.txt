VINTAGE STORY ON THE DECK
=========================

Its own «Vintage Story» mode and a dock block: health and treatment (the S&R System mod), satiety and nutrition, world
(temperature, weather, time, position, temporal storm), your own buttons for the game.

WHAT YOU NEED
1. The deck plugin: copy this folder (vintage) into the deck's plugins/ (or Settings → «Plugins» → «Catalog»).
   Settings → «Plugins»: the master switch and «Vintage Story» — on. The «Vintage Story» mode appears on the home screen.
2. The «Noryx Link» game mod (client only, not needed on the server): claudedecklink_0.3.0.zip — into
   VintagestoryData/Mods. It sends data to the deck over UDP 127.0.0.1:47811 and takes commands on 127.0.0.1:47812 —
   the game and the deck must be on the same PC. The mod runs commands only with the key from VintagestoryData/ModConfig/
   claudedecklink.json (it creates it itself on the first run; the deck reads it itself — needs the «Files» permission).
   The deck didn't find the file (your own game folder) — set it in pluginCfg.vintage.dataDir.
3. Injuries and treatment — the S&R System mod 0.5.0+ (on client and server); treating other players and the treatment bar —
   0.6.2+; the «Health» tab like in the game 0.7 — 0.7.18+. Without it — health only.

THE MODE
On the left — four buttons: Health, Satiety, Temperature, Hotkeys. On the right — the menu of the chosen one.
• Health — like the S&R menu in the game: a doll of six zones (color — severity, bandage, splint, drop — bleeding,
  «!» — won't heal without treatment). Tap a zone — its injuries and treatment: the button — with the best item,
  the arrow — pick an item. How it ended — in a hint (the server's answer). Tap the zone again or tap
  past the buttons — the overall condition, like in the game (injuries by severity, recovery, nutrition, bonuses).
  Whom to treat: «Me» or players within 5 blocks (how many injuries and how far). K on a player in the game — the deck
  switches to them by itself; closed the window or walked away — back to yourself. Beyond 3.5 blocks you can't treat (a hint).
  Treatment bars: yours (whom and with what) and what another player is doing on you.
• Satiety — the overall bar and by food type.
• Temperature — body, air, weather, time and date, until the storm, stability, position.
• Hotkeys — your own buttons. «New» → «Edit»: name, what it does and the text:
    Game commands (through the mod, the game may be out of focus): one per line — /command, .client command, «slot 3»
    (a hotbar slot), «; comment».
    Deck script (keys): like a deck script button — ctrl+c / wait 500 / text … / hold w 1500;
    the keys go to the window active on the PC (the game must be in front).
The dock block (health, satiety, day and time) — a tap opens the mode.

THE GAME MOD (game-mod/)
Noryx Link sources: dotnet build -c Release (the game path — the VINTAGE_STORY variable,
~/Games/VintageStory by default) → release/claudedecklink_<version>.zip.
S&R System gives it public DeckStateJson() / DeckTreat(...) — the bridge calls them by reflection.
