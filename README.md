<p align="center"><img src="logo.svg" width="128" alt="Noryx"></p>

# Noryx

**Turn your Android phone into a stream deck for your PC.** Windows and Linux (KDE Plasma / PipeWire).

> **Public beta.** Noryx is being tested with early users. Things may break — please
> [report bugs](../../issues/new/choose). 🇺🇦 [Українською нижче](#українською).

## What it does

- **Buttons and modes** — launch programs or groups of programs, hotkeys, macros, text typing; your own modes with
  any colour. Script buttons like AutoHotkey: key sequences with pauses, holds, repeat N times or loop.
- **Hotkeys mode** with an on-screen keyboard and numpad.
- **Dock** — master volume and per-app mixer, media player (Spotify / YouTube Music / whatever is playing),
  CPU/GPU load and temperatures, clock.
- **Soundpad** — play sounds "on air" into Discord through a virtual mic.
- **Plugins and mods** from the built-in catalog — Arduino desk lamp, monitor brightness (DDC), Discord,
  Factorio, Satisfactory, Vintage Story, second screen, looks and layouts.
- **Updates itself**; one tap to roll back.
- Interface in Ukrainian and Russian.

## Install

Download the **[latest release](../../releases/latest)** — `Noryx.zip` (Windows), `Noryx-linux.tar.gz` (Linux)
and `Noryx.apk` (phone).

**Windows 10/11 (64-bit)**
1. Extract `Noryx.zip` to a normal folder, e.g. `C:\Noryx` (not straight from the archive).
2. Run `Start.bat`. The first start downloads what it needs (~1–2 min).
3. If the phone can't connect, run `Firewall.bat` as administrator.

**Linux (KDE Plasma 6, PipeWire)**
1. Extract `Noryx-linux.tar.gz`, e.g. to `~/Noryx`.
2. `cd ~/Noryx && ./start.sh`
3. Optional, for hotkeys: `sudo ./system-setup.sh` once.

**Phone (Android 5+)** — same Wi-Fi as the PC. Install `Noryx.apk` (it's also in the PC folder); the app finds
the PC by itself. Or open `http://<PC IP>:8765` in the phone's browser.

More details: `README.txt` in the downloaded folder.

## Updates

New beta versions come out **in batches**, not after every change. Each one is a
[release](../../releases) with the list of what's new.

You don't need to download anything again: the deck checks for updates by itself and shows
«Доступне оновлення» (update available) — tap **«Оновити»** (Налаштування → Оновлення / Settings → Updates).
Settings, sounds and plugins are kept. If something broke after an update, **«Повернути попередню версію»**
in the same place returns the previous one. (The interface is in Ukrainian and Russian.)
The phone app offers its own update when a new `Noryx.apk` comes with the deck.

## Found a bug?

[Open an issue](../../issues/new/choose) and describe what you did and what happened. It helps a lot to attach the
report from the deck: tap the **CPU/GPU load block in the dock** → «Діагностика» (Diagnostics) →
**«Звіт для розробника»** (Report for the developer). It creates a zip; passwords and tokens are removed from it.

## Privacy and security

Noryx works **only in your local network**. Settings, sounds and logs stay on your PC. New phones must be paired
with a code shown on the PC. The only internet access is checking this repository for updates and the plugin
catalog (and whatever plugins you enable, e.g. Discord). Found a security problem? Please don't open a public
issue — see [SECURITY.md](SECURITY.md).

## License

© Sterben. All rights reserved — see [LICENSE](LICENSE). You may download and use Noryx for free for personal use.
The source code is visible for transparency, but it is **not** open source.

---

## Українською

**Noryx** — стрім-дек на телефоні для твого ПК (Windows і Linux). Зараз — **відкрите бета-тестування**:
щось може не працювати, пиши про це в [Issues](../../issues/new/choose).

**Встановлення.** Завантаж **[останній реліз](../../releases/latest)**.
- Windows — розпакуй `Noryx.zip` у звичайну теку (наприклад `C:\Noryx`) і запусти `Start.bat`.
  Телефон не підключається — запусти `Firewall.bat` від адміністратора.
- Linux — розпакуй `Noryx-linux.tar.gz` і запусти `./start.sh`.
- Телефон — у тому самому Wi-Fi, встанови `Noryx.apk`, застосунок сам знайде комп'ютер.

Докладно — `README_UA.txt` у теці.

**Оновлення.** Нові бета-версії виходять **пачками**, не після кожної зміни; кожна — [реліз](../../releases) зі
списком змін. Перекачувати нічого не треба: дек сам скаже «Доступне оновлення» — тисни **«Оновити»**
(або Налаштування → Оновлення). Налаштування, звуки й плагіни лишаються. Після оновлення щось зламалось —
**Налаштування → Оновлення → «Повернути попередню версію»**.

**Знайшов баг?** [Створи issue](../../issues/new/choose): що робив і що сталося. Дуже допоможе звіт із дека:
натисни **блок завантаження ЦП/ГП у доку** → «Діагностика» → **«Звіт для розробника»** — zip, паролі й токени
з нього прибрані.

Дек працює лише у твоїй локальній мережі; налаштування, звуки й логи лишаються на ПК.
Безкоштовно для особистого користування; код відкритий для перегляду, але це **не** open source (див. LICENSE).
