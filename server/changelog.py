"""Лог изменений дека для пользователя: что поменялось в самом деке (не в плагинах и модах — у них свои CHANGES).
Пишется для человека, а не разработчика: что стало видно / удобнее / исправлено, без имён функций и веток.

CHANGES — свежие сверху: (дата "ГГГГ-ММ-ДД", [(английский, украинский), …]); другие языки — из мовних пакетов
(ключ — английский текст, его отдаёт langs.keys). Поменял дек заметно для пользователя — допиши пункт
в запись с сегодняшней датой (нет такой — новая запись сверху); tests/test_update.py проверяет порядок и оба языка.
Окно «Лог изменений» (Настройки → Обновления) показывает установленный лог, а при новой версии — ещё и её пункты:
update.check читает этот же файл с GitHub (parse — только литералы, код не выполняется) и отдаёт новые (newer).
Только стандартная библиотека: модуль читает и помощник обновления."""
import ast

CHANGES = [
    ("2026-11-23", [
        ("Seven more languages in the catalog: German, French, Spanish"
         ", Italian, Japanese, Chinese and Korean — the whole deck, the PC log and the catalog plugins.",
         "Ще сім мов у каталозі: німецька, французька, іспанська, італ"
         "ійська, японська, китайська й корейська — весь дек, лог на ПК і плагіни каталогу."),
        ("Settings are now cards: a short header with what's inside and a ▼ arrow — tap it and the card opens downward with all "
         "its switches (▲ closes it). The phone remembers which cards are open. Plugins — a compact row with a switch, their "
         "settings, permissions and «Delete» are inside the card.",
         "Налаштування тепер картами: коротка шапка з тим, що всередині, і стрілка ▼ — тапни, і карта відкриється вниз з усіма "
         "перемикачами (▲ — закрити). Які карти відкриті — телефон пам'ятає. Плагіни — компактна плашка з перемикачем, а їхні "
         "налаштування, дозволи й «Видалити» — у карті."),
        ("The PC log (logs/server.log), the changelog and the README are in English too (Ukrainian — when the deck is in "
         "Ukrainian). Phone app 1.8: its own screens (connection, waking the PC) in English or Ukrainian.",
         "Лог на ПК (logs/server.log), лог змін і README теж англійською (українською — коли дек українською). "
         "Застосунок для телефона 1.8: його власні екрани (підключення, пробудження ПК) англійською або українською."),
        ("The deck is now in English by default, Ukrainian is built in, Russian is gone (those who used Russian switch "
         "to Ukrainian). Other languages — a separate «Languages» page next to mods and plugins: tap a language — it "
         "downloads from the catalog and turns on, hold it — delete. First in the catalog — English (UK).",
         "Дек тепер англійською за замовчуванням, українська вбудована, російської більше немає (хто був на російській — переходить "
         "на українську). Інші мови — окрема сторінка «Мови» поруч із модами й плагінами: тап по мові — завантажиться з каталогу "
         "й увімкнеться, утримати — видалити. Першою в каталозі — англійська (Британія)."),
        ("Plugins and mods from the catalog are also in English with Ukrainian; the card shows which languages a plugin "
         "speaks (a yellow tag — not the deck's language). Language packs can translate plugins too.",
         "Плагіни й моди з каталогу теж англійською з українською; на картці видно, якими мовами плагін (жовта мітка — "
         "не мовою деку). Мовні пакети можуть перекладати й плагіни."),
    ]),
    ("2026-11-22", [
        ("New Noryx logo — «deck 2×2»: in the tray, on the browser tab and in the phone app (1.7, updates itself).",
         "Новий логотип Noryx — «дек 2×2»: у треї, на вкладці браузера і в застосунку на телефоні (1.7, оновиться сам)."),
        ("Phone app 1.6: it's now called Noryx (updates itself).",
         "Застосунок для телефона 1.6: називається Noryx (оновиться сам)."),
    ]),
    ("2026-11-20", [
        ("App icons with a transparent background (round PNGs) no longer sit in a gray square — the background under "
         "them matches the tile.",
         "Значки програм із прозорим фоном (круглі PNG) більше не сидять у сірому квадраті — фон під ними такий самий, як у підкладки."),
        ("Changing modes doesn't close busy apps: the one recording the microphone (Discord voice chat, recording) and "
         "Steam while a game is running. The «Don't close busy apps» switch is in settings, the reason — in the hint.",
         "Зміна режиму не закриває зайняті програми: ту, що пише мікрофон (голосовий чат Discord, запис), і Steam, поки йде гра. "
         "Перемикач «Не закривати зайняті» в налаштуваннях, причина — у підказці."),
        ("The app tile menu has «Don't close on mode change».",
         "У меню плитки програми — «Не закривати при зміні режиму»."),
        ("Auto-switch doesn't get in the scenario's way: while it runs, the rules wait, and the apps it started don't "
         "switch the mode a second time for 2 more minutes.",
         "Автоперемикання не заважає сценарію: поки він іде, правила чекають, а програми, які він запустив, ще 2 хвилини не "
         "перемикають режим вдруге."),
    ]),
    ("2026-11-18", [
        ("A plugin screen (e.g. Vintage Story) scrolls smoothly under your finger, even when the plugin updates its data"
         " often.",
         "Екран плагіна (наприклад, Vintage Story) гортається пальцем без ривків, навіть коли плагін часто оновлює дані."),
        ("Settings scroll smoothly: the window no longer «lets go» of your finger mid-scroll.",
         "Налаштування гортаються пальцем без ривків: вікно більше не «відпускає» палець посеред прокрутки."),
        ("«Catalog» on the «Mods» page shows mods, on the «Plugins» page — plugins.",
         "«Каталог» на сторінці «Моди» показує моди, на сторінці «Плагіни» — плагіни."),
        ("Mods can be deleted from the deck, like plugins.", "Моди можна видаляти з дека, як плагіни."),
        ("Deleted plugins and mods are erased for good and take no space (the old «trash» is erased too).",
         "Видалені плагіни й моди стираються назавжди й не займають місця (попередній «кошик» теж стерто)."),
        ("The deck loads the PC less: the CPU temperature is read from a single file, without phones the deck doesn't "
         "poll sensors, and checks the player, volume and apps less often.",
         "Дек менше навантажує ПК: температура процесора читається одним файлом, без телефонів дек не опитує датчики, а плеєр, гучність і програми перевіряє рідше."),
        ("Your deck look now covers the whole deck: dock blocks and buttons, tile buttons, settings windows — the same "
         "colors and shapes. Saved looks pick this up by themselves.",
         "Свій вигляд дека тепер на всьому деку: блоки й кнопки дока, кнопки на плитках, вікна налаштувань — ті самі кольори й форми. Збережені вигляди підхоплюють це самі."),
    ]),
    ("2026-11-17", [
        ("Buttons at the top of the dock (edit, settings, mixer, soundpad) light up immediately, not a couple of seconds"
         " later.",
         "Кнопки вгорі дока (правка, налаштування, мікшер, саундпад) підсвічуються одразу, а не за кілька секунд."),
        ("A dropdown closes when you tap it again.",
         "Випадний список закривається повторним натисканням на нього."),
        ("Hints in settings rewritten: shorter and clearer.",
         "Підказки в налаштуваннях переписано: коротше й зрозуміліше."),
        ("«Changelog» in the «Updates» section: what changed in the installed version and what's coming in the new one.",
         "«Лог змін» у розділі «Оновлення»: що змінилося у встановленій версії і що буде в новій."),
        ("The color picker in the look editor is the deck's own window (field, hues, HEX, ready colors) instead of the "
         "Android system window.",
         "Вибір кольору в редакторі вигляду — власне вікно дека (поле, відтінки, HEX, готові кольори) замість системного вікна Android."),
        ("Plugin and mod cards carry a «Plugin» / «Mod» tag; a plugin put into mods or a mod in plugins is moved by the "
         "deck itself.",
         "На картках плагінів і модів — мітка «Плагін» / «Мод»; плагін, покладений у mods, або мод у plugins дек перекладе сам."),
        ("Your own deck look without code: tile shape and colors, background (gradient, pattern, photo), dock, font — "
         "visible right away.",
         "Свій вигляд дека без коду: форма й кольори плиток, тло (градієнт, візерунок, фото), док, шрифт — видно одразу."),
        ("Deck mods: they change the look and features; a new mod can be compared «Before / After» before you keep it.",
         "Моди дека: змінюють вигляд і функції; новий мод можна порівняти «Було / Стало», перш ніж залишити."),
        ("The home screen can be built from panels (via mods).",
         "Головний екран можна зібрати з панелей (через моди)."),
        ("Catalog: plugins and mods install, update and roll back right from the deck.",
         "Каталог: плагіни й моди встановлюються, оновлюються й відкочуються просто з дека."),
        ("Scenarios: a block editor — steps as cards, «Blocks» / «Text» switch, the running step is highlighted.",
         "Сценарії: редактор блоками — кроки картками, перемикання «Блоки» / «Текст», поточний крок підсвічено."),
        ("Own devices: a new phone connects with a code from the PC; the list and «Forget» — in the «Devices» window.",
         "Свої пристрої: новий телефон підключається за кодом з ПК; список і «Забути» — у вікні «Пристрої»."),
        ("Device roles: main deck, game panel, monitoring screen, guest.",
         "Ролі пристроїв: основний дек, ігрова панель, екран моніторингу, гість."),
        ("Plugins: the deck asks for consent to what they need; a deleted plugin can be restored.",
         "Плагіни: дек питає згоди на те, що їм потрібно; видалений плагін можна повернути."),
        ("Diagnostics of the deck itself and a «Developer report»; a plugin that keeps crashing stops by itself.",
         "Діагностика самого дека і «Звіт для розробника»; плагін, що раз у раз падає, зупиняється сам."),
        ("The Arduino lamp no longer ships with the deck — it's in the «Catalog». The update doesn't touch plugins "
         "already installed.",
         "Лампа на Arduino більше не в комплекті — вона в «Каталозі». Встановлених плагінів оновлення не чіпає."),
        ("Security: tiles from the phone are checked, the file with the GitHub token is closed to other PC users.",
         "Безпека: плитки з телефона перевіряються, файл із токеном GitHub закрито від інших користувачів ПК."),
    ]),
    ("2026-11-07", [
        ("Dock: many blocks — it scrolls with a finger; block height drags smoothly.",
         "Док: багато блоків — гортається пальцем; висота блоку тягнеться плавно."),
        ("Hold a tile and move — just dragging, without «Edit».",
         "Утримав плитку й повів — просто перетягування, без «Правки»."),
        ("Steam game icons are sharp: from the game itself (Windows) or the icon theme (Linux).",
         "Значки ігор Steam чіткі: із самої гри (Windows) або теми значків (Linux)."),
        ("Pinch: pinch in — to the home screen, spread — back to the mode; a gestures hint.",
         "Щипок: звів — на головний екран, розвів — назад у режим; підказка з жестами."),
        ("Plugins in settings: enabled and disabled apart, alphabetically. Plugins can add their own modes.",
         "Плагіни в налаштуваннях: увімкнені й вимкнені окремо, за абеткою. Плагіни можуть додавати свої режими."),
    ]),
    ("2026-11-06", [
        ("The dock hides like a tray: tiles fill the screen, a mini player in the header.",
         "Док ховається, як трей: плитки на весь екран, міні-плеєр у шапці."),
        ("The Android app 1.5 updates itself.",
         "Застосунок для Android 1.5 оновлюється сам."),
        ("Tile menu: hold — edit, rename, to a group, delete.",
         "Меню плитки: утримати — змінити, перейменувати, у групу, видалити."),
        ("Plugins can have their own look: bars, cards, pictures, their own theme and screen.",
         "Плагіни можуть мати свій вигляд: шкали, картки, картинки, своя тема й екран."),
    ]),
    ("2026-11-01", [
        ("Plugins: the plugins folder and the «Plugins» page in settings.",
         "Плагіни: тека plugins і сторінка «Плагіни» в налаштуваннях."),
        ("Kiosk phone: the phone only for the deck (Phone.bat / phone.sh); its own screen brightness.",
         "Телефон-кіоск: телефон лише під дек (Phone.bat / phone.sh); своя яскравість екрана."),
        ("Old phones (WebView 83): windows open and look right.",
         "Старі телефони (WebView 83): вікна відкриваються й виглядають правильно."),
    ]),
    ("2026-10-31", [
        ("Auto-switch: a rule for any app, even without a tile.",
         "Автоперемикання: правило для будь-якої програми, навіть без плитки."),
        ("Deck-style dropdowns.", "Випадні списки в стилі дека."),
        ("Phone charge on the clock screen.", "Заряд телефона на екрані годинника."),
        ("Visualizer: three color zones and a hue wheel.", "Візуалізатор: три зони кольору й коло відтінків."),
        ("Compact interface: dock block height is dragged by the bottom edge.",
         "Компактний інтерфейс: висоту блоків дока тягнуть за нижній край."),
        ("Waking the PC: 3 seconds to «Cancel» before the phone wakes the PC.",
         "Пробудження ПК: 3 секунди на «Скасувати», перш ніж телефон розбудить ПК."),
    ]),
]


def clean(changes):
    """Лог (свой или с GitHub) → [{date, items: [{en, uk}]}] (у старых версий первым шёл русский); кривые записи пропускаются."""
    out = []
    for e in changes if isinstance(changes, (list, tuple)) else []:
        if not (isinstance(e, (list, tuple)) and len(e) == 2 and isinstance(e[0], str) and isinstance(e[1], (list, tuple))):
            continue
        items = [{"en": str(i[0])[:400], "uk": str(i[1])[:400]} for i in e[1][:40]
                 if isinstance(i, (list, tuple)) and len(i) == 2 and all(isinstance(x, str) for x in i)]
        if items:
            out.append({"date": e[0][:10], "items": items})
    return out[:100]


def entries():
    return clean(CHANGES)


def parse(text):
    """CHANGES из текста этого файла (версия с GitHub) — только литерал, код не выполняется. Не вышло — []."""
    try:
        for n in ast.parse(text).body:
            if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "CHANGES" for t in n.targets):
                return clean(ast.literal_eval(n.value))
    except (SyntaxError, ValueError, TypeError, MemoryError, RecursionError):
        pass
    return []


def newer(remote, local=None):
    """Пункты новой версии, которых нет в установленной (по английскому и украинскому тексту), — по датам, свежие сверху."""
    have = {x for e in (entries() if local is None else local) for i in e["items"] for x in (i["en"], i["uk"])}
    out = []
    for e in remote:
        items = [i for i in e["items"] if i["en"] not in have and i["uk"] not in have]
        if items:
            out.append({"date": e["date"], "items": items})
    return out
