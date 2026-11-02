#!/bin/bash
# Install local-llm-hotkeys for the current user: commands, config, KDE hotkeys, Dolphin menu,
# the Copilot key map. Safe to run again (after a git pull, for example).
#
#   ./install.sh               install or update
#   ./install.sh --flm         also install and start the FastFlowLM service (systemd --user)
#   ./install.sh --uninstall   remove what install.sh added (keeps your config and saved chats)
#
# Needs no root. Missing packages are listed with the command to install them.
set -u
cd "$(dirname "$(readlink -f "$0")")" || exit 1
REPO=$PWD
TOOLS="uktr eli12 fixtext ask dictate"
BIN=$HOME/.local/bin
APPS=$HOME/.local/share/applications
MENUS=$HOME/.local/share/kio/servicemenus
XKB=$HOME/.config/xkb
CONF=$HOME/.config/uktr/config
MODELS="translategemma:4b hy-mt2-flash:1.8b whisper-v3:turbo qwen3.5:9b"

ok()   { printf '  \033[32m✓\033[0m %s\n' "$*"; }
warn() { printf '  \033[33m!\033[0m %s\n' "$*"; }
step() { printf '\n\033[1m%s\033[0m\n' "$*"; }
keys() {  # the shortcuts of a .desktop file, with Meta+Shift+F23 shown as "Copilot"
    python3 - "$1" <<'PY'
import sys
line = next(l for l in open(sys.argv[1]) if l.startswith("X-KDE-Shortcuts="))
names = []
for key in line.split("=", 1)[1].strip().split(","):
    if key.endswith("Touchpad Off"):
        continue  # the same Copilot key under its other name
    if key.endswith("F23"):
        mods = [m for m in key.split("+")[:-1] if m not in ("Meta", "Shift")]
        key = "+".join(mods + ["Copilot"])
    names.append(key)
print(" or ".join(names))
PY
}

uninstall() {
    step "Removing local-llm-hotkeys"
    for t in $TOOLS; do
        if [ "$(readlink -f "$BIN/$t" 2>/dev/null)" = "$REPO/$t.py" ]; then rm "$BIN/$t" && ok "removed $BIN/$t"; fi
    done
    for f in kde/*.desktop; do
        [ -f "$APPS/$(basename "$f")" ] && rm "$APPS/$(basename "$f")" && ok "removed hotkey $(basename "$f")"
    done
    [ -f "$MENUS/uktr.desktop" ] && rm "$MENUS/uktr.desktop" && ok "removed the Dolphin menu"
    for b in kbuildsycoca6 kbuildsycoca5; do command -v $b >/dev/null && $b >/dev/null 2>&1 && break; done
    step "Kept"
    echo "  your config ($CONF) and saved chats (~/.local/share/uktr/ask/)."
    [ -f "$XKB/symbols/copilot" ] && echo "  the Copilot key map: rm $XKB/rules/evdev $XKB/symbols/copilot (then log in again)."
    exit 0
}

[ "${1:-}" = "--uninstall" ] && uninstall
case "${1:-}" in ""|--flm) ;; *) sed -n '2,9p' "$0" | sed 's/^# \{0,1\}//'; exit 1 ;; esac

step "Checking what is installed"
if python3 -c 'import sys; sys.exit(sys.version_info < (3, 9))' 2>/dev/null; then
    ok "python3 $(python3 -c 'import platform; print(platform.python_version())')"
else
    warn "python 3.9 or newer is needed"; exit 1
fi

# Which package has what, per distribution family: openSUSE, Debian/Ubuntu, Fedora, Arch.
# shellcheck source=/dev/null
. /etc/os-release 2>/dev/null
case " ${ID:-} ${ID_LIKE:-} " in
    *" suse "*|*opensuse*) family=suse; installer="sudo zypper install" ;;
    *" debian "*|*" ubuntu "*) family=debian; installer="sudo apt install" ;;
    *" fedora "*|*" rhel "*) family=fedora; installer="sudo dnf install" ;;
    *" arch "*) family=arch; installer="sudo pacman -S --needed" ;;
    *) family=other; installer="" ;;
esac
pyver=$(python3 -c 'import sys; print(f"{sys.version_info[0]}{sys.version_info[1]}")')
pkg() {  # pkg NAME: the package name in this distribution
    case "$family:$1" in
        suse:pyside6) echo "python$pyver-pyside6" ;;
        suse:notify) echo libnotify-tools ;;          debian:notify) echo libnotify-bin ;;
        *:notify) echo libnotify ;;
        debian:spectacle) echo kde-spectacle ;;       *:spectacle) echo spectacle ;;
        suse:tesseract|debian:tesseract) echo tesseract-ocr ;;
        *:tesseract) echo tesseract ;;
        suse:ukr) echo tesseract-ocr-traineddata-ukr ;; debian:ukr) echo tesseract-ocr-ukr ;;
        fedora:ukr) echo tesseract-langpack-ukr ;;    arch:ukr) echo tesseract-data-ukr ;;
        suse:pdf) echo poppler-tools ;;               arch:pdf) echo poppler ;;
        *:pdf) echo poppler-utils ;;
        debian:pandoc) echo pandoc ;;                 *:pandoc) echo pandoc-cli ;;
        arch:parec) echo libpulse ;;                  *:parec) echo pulseaudio-utils ;;
        debian:pyside6) echo python3-pyside6.qtwidgets ;; fedora:pyside6) echo python3-pyside6 ;;
        arch:pyside6) echo pyside6 ;;
        *) echo "$1" ;;
    esac
}
missing=""
need() {  # need COMMAND PACKAGE WHAT
    if command -v "$1" >/dev/null; then ok "$1"; else warn "$1 not found: $3"; missing="$missing $(pkg "$2")"; fi
}
need wl-copy wl-clipboard "needed for every hotkey (clipboard)"
need kdialog kdialog "needed for the popup windows"
need notify-send notify "needed for notifications"
need spectacle spectacle "needed for uktr shot and ask --screen"
need tesseract tesseract "needed for uktr shot (OCR)"
need pdftotext pdf "needed for PDF files"
need pandoc pandoc "needed for epub, docx, odt and html files"
need parec parec "needed for uktr listen and dictate"
if python3 -c 'import PySide6.QtWidgets' 2>/dev/null; then ok "PySide6"; else
    warn "PySide6 not found: the ask window opens in Konsole instead"; missing="$missing $(pkg pyside6)"; fi
if ! command -v tesseract >/dev/null; then missing="$missing $(pkg ukr)"  # with the Ukrainian data
elif ! tesseract --list-langs 2>/dev/null | grep -qx ukr; then
    warn "tesseract has no Ukrainian data (for uktr shot)"; missing="$missing $(pkg ukr)"; fi
if [ -n "$missing" ]; then
    if [ -n "$installer" ]; then echo "  To install the missing packages: $installer$missing"
    else echo "  Missing packages (names differ per distribution):$missing"; fi
fi
if [ -w /dev/uinput ]; then ok "/dev/uinput (dictate types into the active window)"; else
    warn "no write access to /dev/uinput: dictate leaves the text on the clipboard. To allow it"
    echo "    (this lets every program of yours type and click in any window; see README, Requirements):"
    echo "    sudo cp $REPO/udev/60-local-llm-hotkeys-uinput.rules /etc/udev/rules.d/"
    echo "    sudo udevadm control --reload && sudo udevadm trigger --sysname-match=uinput"
fi

step "Commands in $BIN"
mkdir -p "$BIN"
for t in $TOOLS; do
    if [ -e "$BIN/$t" ] && [ ! -L "$BIN/$t" ]; then warn "$BIN/$t exists and is not a link; left alone"; continue; fi
    ln -sfn "$REPO/$t.py" "$BIN/$t" && ok "$t"
done
case ":$PATH:" in *":$BIN:"*) ;; *) warn "$BIN is not in your PATH; add it, or the hotkeys cannot find the commands" ;; esac

step "Config"
if [ -f "$CONF" ]; then ok "$CONF exists (not changed)"; else
    mkdir -p "$(dirname "$CONF")" && cp config.example "$CONF" && ok "created $CONF from config.example"; fi
lang=$(python3 -c 'import sys; sys.path.insert(0, sys.argv[1]); import llm; print(llm.language()[1])' "$REPO")
ok "your language: $lang (set UKTR_LANGUAGE in the config to change it)"

SYCOCA=$(command -v kbuildsycoca6 || command -v kbuildsycoca5)
if [ -n "$SYCOCA" ]; then
    step "KDE hotkeys and Dolphin menu"
    mkdir -p "$APPS" "$MENUS"
    for f in kde/*.desktop; do
        cp "$f" "$APPS/" && ok "$(sed -n 's/^Name=//p' "$f")  [$(keys "$f")]"
    done
    cp kde/servicemenu/uktr.desktop "$MENUS/" && chmod +x "$MENUS/uktr.desktop" && ok "Dolphin: Translate (uktr), Explain like I'm 12 (eli12)"
    "$SYCOCA" >/dev/null 2>&1 && ok "KDE knows the new hotkeys"
    [ "$(basename "$SYCOCA")" = kbuildsycoca5 ] && warn "Plasma 5: not tested; the hotkeys are made for Plasma 6"
    echo "  KDE keeps a key you changed yourself. To change a key: System Settings → Shortcuts."

    step "The Copilot key"
    if diff -rq kde/xkb "$XKB" >/dev/null 2>&1; then ok "key map already in $XKB"
    elif [ -e "$XKB/rules/evdev" ] && ! cmp -s kde/xkb/rules/evdev "$XKB/rules/evdev"; then
        warn "$XKB/rules/evdev exists and is different; add this line to it yourself:"; sed -n '/copilot/p' kde/xkb/rules/evdev
    else
        mkdir -p "$XKB" && cp -r kde/xkb/* "$XKB/" && ok "key map installed in $XKB" && warn "log out and in again, so that the Copilot key works"
    fi
else
    step "KDE not found: hotkeys skipped (the commands work without them)"
fi

step "The model server (FastFlowLM on the NPU)"
FLM=$(command -v flm || echo "$BIN/flm")
if [ ! -x "$FLM" ]; then
    warn "flm not found. Get FastFlowLM (see README, 'Backend'), put flm in $BIN, then run ./install.sh --flm"
else
    if [ "${1:-}" = "--flm" ]; then
        mkdir -p "$HOME/.config/systemd/user" && cp systemd/flm.service "$HOME/.config/systemd/user/"
        systemctl --user daemon-reload && systemctl --user enable --now flm && ok "flm service enabled and started"
    fi
    listed=$("$FLM" list 2>/dev/null)
    for m in $MODELS; do
        if grep -q -- "- $m ✅" <<<"$listed"; then ok "model $m"; else warn "model $m missing: flm pull $m"; fi
    done
    url=$(python3 -c 'import sys; sys.path.insert(0, sys.argv[1]); import llm; print(llm.URL)' "$REPO")
    if python3 -c 'import sys, urllib.request; urllib.request.urlopen(sys.argv[1] + "/models", timeout=3)' "$url" 2>/dev/null; then
        ok "server answers at $url"
    else
        warn "no server at $url: systemctl --user start flm (or ./install.sh --flm)"
    fi
    if [ "$(ulimit -l)" != "unlimited" ]; then
        warn "locked memory is limited ($(ulimit -l) kB); the NPU needs more (see README, 'Backend')"; fi
fi

step "Done"
echo "  Try: echo 'Доброго ранку' | uktr     or press Copilot for the chat."
