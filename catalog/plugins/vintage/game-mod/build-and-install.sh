#!/usr/bin/env bash
# =====================================================================================
#  Noryx Link — зібрати мод і встановити його в гру.
#
#  Використання:
#     ./build-and-install.sh                    — зібрати поточну версію і встановити
#     ./build-and-install.sh 0.7.12             — спершу змінити версію (у ClaudeDeckLink.csproj і modinfo.json)
#     ./build-and-install.sh --zip-backup       — додатково зробити zip-бекап коду (напр. перед великим оновленням)
#     ./build-and-install.sh 0.8.0 --zip-backup — і те, й інше
#
#  Історія коду ведеться в git (git log); zip-бекап коду — лише на вимогу.
#
#  Що робить:
#     1. (якщо вказано) змінює номер версії в ClaudeDeckLink.csproj і modinfo.json;
#     2. (з --zip-backup) робить бекап вихідного коду в "VS Mods Backups" (без bin/, obj/, release/);
#     3. компілює і пакує мод:  dotnet build -c Release  ->  release/claudedecklink_<версія>.zip;
#     4. переносить старі claudedecklink_*.zip з теки модів гри в "VS Mods Backups";
#     5. копіює новий zip у теку модів.
#  Після цього перезапусти гру (або вийди зі світу й зайди знову).
#
#  Шляхи можна перевизначити змінними середовища:
#     VS_MODS_DIR     — тека модів гри   (за замовчуванням ~/.config/VintagestoryData/Mods)
#     CDL_BACKUP_DIR  — тека бекапів     (за замовчуванням <тека завантажень>/VS Mods Backups;
#                       тека завантажень — з xdg-user-dir DOWNLOAD, інакше ~/Downloads)
# =====================================================================================
set -euo pipefail

cd "$(dirname "$(readlink -f "$0")")"
MODS="${VS_MODS_DIR:-$HOME/.config/VintagestoryData/Mods}"
# Тека завантажень у будь-якій мові системи (у тебе — ~/Завантажене). Без xdg-user-dir — ~/Downloads.
DOWNLOADS="$(xdg-user-dir DOWNLOAD 2>/dev/null || true)"
if [ -z "$DOWNLOADS" ] || [ "$DOWNLOADS" = "$HOME" ]; then DOWNLOADS="$HOME/Downloads"; fi
BACKUP="${CDL_BACKUP_DIR:-$DOWNLOADS/VS Mods Backups}"

# --- Аргументи
NEW=""
ZIP_BACKUP=0
for arg in "$@"; do
    case "$arg" in
        --zip-backup) ZIP_BACKUP=1 ;;
        *) NEW="$arg" ;;
    esac
done

# --- 1. Версія
if [[ -n "$NEW" ]]; then
    if [[ ! "$NEW" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
        echo "Версія має бути у форматі X.Y.Z (наприклад 0.7.11), а не '$NEW'." >&2
        exit 1
    fi
    sed -i -E "s|<Version>[^<]+</Version>|<Version>${NEW}</Version>|" ClaudeDeckLink.csproj
    sed -i -E "s|(\"version\":[[:space:]]*\")[^\"]+(\")|\1${NEW}\2|" modinfo.json
fi
VERSION=$(grep -oP '(?<=<Version>)[^<]+' ClaudeDeckLink.csproj)
MODINFO_VERSION=$(grep -oP '"version":\s*"\K[^"]+' modinfo.json)
if [[ "$VERSION" != "$MODINFO_VERSION" ]]; then
    echo "Версії не збігаються: ClaudeDeckLink.csproj = $VERSION, modinfo.json = $MODINFO_VERSION." >&2
    echo "Запусти скрипт із версією, напр.: ./build-and-install.sh $VERSION" >&2
    exit 1
fi
echo "==> Noryx Link $VERSION"

# --- 2. Zip-бекап вихідного коду (лише на вимогу)
mkdir -p "$BACKUP"
if [[ $ZIP_BACKUP -eq 1 ]]; then
    STAMP=$(date +%Y%m%d_%H%M)
    PROJECT_DIR=$(basename "$PWD")
    tar --exclude="$PROJECT_DIR/bin" --exclude="$PROJECT_DIR/obj" --exclude="$PROJECT_DIR/release" --exclude="$PROJECT_DIR/.git" \
        -czf "$BACKUP/ClaudeDeckLink_backup_v${VERSION}_${STAMP}.tar.gz" -C .. "$PROJECT_DIR"
    echo "==> Zip-бекап коду: $BACKUP/ClaudeDeckLink_backup_v${VERSION}_${STAMP}.tar.gz"
fi

# --- 3. Збірка
echo "==> Збірка..."
LOG=$(mktemp -t claudedecklink-build.XXXXXX.log)
trap 'rm -f "$LOG"' EXIT
if ! dotnet build -c Release -nologo -v quiet > "$LOG" 2>&1; then
    grep -E "error" "$LOG" >&2 || cat "$LOG" >&2
    echo "Збірка не вдалася — мод у грі НЕ змінено." >&2
    exit 1
fi
ZIP="release/claudedecklink_${VERSION}.zip"
if [[ ! -f "$ZIP" ]]; then
    echo "Не знайдено $ZIP після збірки." >&2
    exit 1
fi
echo "==> Зібрано: $ZIP"

# --- 4–5. Встановлення
mkdir -p "$MODS"
shopt -s nullglob
for old in "$MODS"/claudedecklink_*.zip; do
    mv -f "$old" "$BACKUP/"
    echo "==> Попередню збірку перенесено в бекапи: $(basename "$old")"
done
cp "$ZIP" "$MODS/"
echo "==> Встановлено: $MODS/$(basename "$ZIP")"
echo "Готово. Перезапусти гру (або вийди зі світу й зайди знову)."
if command -v git >/dev/null && git rev-parse --is-inside-work-tree >/dev/null 2>&1 && [[ -n "$(git status --porcelain)" ]]; then
    echo "Примітка: у git є незакомічені зміни (git status)."
fi
