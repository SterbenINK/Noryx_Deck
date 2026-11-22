"""Роли своих устройств: у каждого телефона / планшета — своё назначение, а не копия одного интерфейса.

  main    — основной дек: всё (так было и раньше; сам ПК — всегда main);
  panel   — игровая панель: только выбранные режимы — плитки, звук, плеер, саундпад, кнопки плагинов; без настроек и правки;
  monitor — экран мониторинга: смотреть (режимы, плеер, визуализатор, диагностика), ничего не нажимать;
  guest   — гость: выбранные режимы — плитки и плеер; без настроек, буфера обмена, диагностики, устройств.

Ограничения проверяет сервер (Deck.handle, Deck.send): страница прячет лишнее только для удобства. Роль хранится
у устройства в data/phones.json (Pairing), назначает её основной дек в окне «Устройства».
"""
ROLES = ["main", "panel", "monitor", "guest"]
LABELS = {"main": ("Основной дек", "Основний дек"), "panel": ("Игровая панель", "Ігрова панель"),
          "monitor": ("Экран мониторинга", "Екран моніторингу"), "guest": ("Гость", "Гість")}
ABOUT = {"main": ("Всё, как сейчас", "Усе, як зараз"),
         "panel": ("Только выбранные режимы: плитки, звук, плеер, саундпад; без настроек и правки",
                   "Лише вибрані режими: плитки, звук, плеєр, саундпад; без налаштувань і правки"),
         "monitor": ("Только смотреть: режимы, плеер, визуализатор, диагностика — нажимать нельзя",
                     "Лише дивитися: режими, плеєр, візуалізатор, діагностика — натискати не можна"),
         "guest": ("Выбранные режимы: плитки и плеер; без настроек, буфера обмена и диагностики",
                   "Вибрані режими: плитки й плеєр; без налаштувань, буфера обміну й діагностики")}

VIEW = {"ping", "viz", "eventsRead"}       # смотреть: открыть режим на телефоне — это только вид, без смены режима ПК
ALLOW = {
    "monitor": VIEW | {"diag"},
    "guest": VIEW | {"mode", "press", "media", "vol"},
    "panel": VIEW | {"mode", "press", "ctx", "media", "vol", "out", "pad", "padStop", "padList", "padMeter", "plugin", "pin",
                     "run", "wl", "diag", "key"},
}
MODE_MSGS = {"mode", "press", "ctx"}            # у этих — режим, он должен быть из разрешённых
# что не-основным не отправляем: буфер обмена ПК, список устройств и коды подключения, отчёты, состояние дека
HIDDEN = {"clip", "devices", "phones", "pairCode", "healthReport", "health", "catalog"}


def label(role, lang):
    r = LABELS.get(role, LABELS["main"])
    return r[1] if lang == "uk" else r[0]


def clean(v, exists=None):
    """Роль устройства из конфига / с телефона → {role, modes, start}. Непонятное — основной дек."""
    v = v if isinstance(v, dict) else {"role": v} if isinstance(v, str) else {}
    role = v.get("role") if v.get("role") in ROLES else "main"
    modes = [str(m)[:48] for m in v.get("modes") or [] if isinstance(m, str)][:32] if role != "main" else []
    if exists:
        modes = [m for m in modes if exists(m)]
    start = str(v.get("start") or "")[:48]
    if role == "main" or (modes and start not in modes):
        start = modes[0] if modes else ""
    return {"role": role, "modes": modes, "start": start}


def mode_ok(r, mode):
    return r["role"] == "main" or not r["modes"] or mode in r["modes"]


def allowed(r, kind, m):
    """Можно ли этому устройству сообщение такого вида (и в этот режим)."""
    if r["role"] == "main":
        return True
    if kind not in ALLOW.get(r["role"], ()):
        return False
    if kind in MODE_MSGS and isinstance(m, dict) and m.get("mode") is not None and not mode_ok(r, m.get("mode")):
        return False
    return True


def visible(r, msg):
    """Отправлять ли это сообщение устройству с такой ролью."""
    if r["role"] == "main" or not isinstance(msg, dict):
        return True
    if msg.get("t") in HIDDEN or msg.get("mainOnly"):
        return False
    return not (r["role"] == "guest" and msg.get("t") == "diag")
