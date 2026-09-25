#!/usr/bin/env bash
# One-command installer: system deps -> app -> desktop entries -> running tray.
set -euo pipefail

cd "$(dirname "$0")"

USER_NAME="${SUDO_USER:-${USER:-$(id -un)}}"
BIN="$HOME/.local/bin/inputlock"

note()  { printf '\n==> %s\n' "$*"; }
warn()  { printf '    ! %s\n' "$*" >&2; }

run_root() {
    if [ "$(id -u)" -eq 0 ]; then
        "$@"
    elif [ -n "${SUDO_ASKPASS:-}" ]; then
        sudo -A "$@"
    elif sudo -n true 2>/dev/null; then
        sudo -n "$@"
    else
        sudo "$@"
    fi
}

note "1/6 system packages"
if command -v dnf >/dev/null 2>&1; then
    run_root dnf install -y python3-gobject python3-evdev gcc python3-devel \
        python3-pip libayatana-appindicator-gtk3 gnome-shell-extension-appindicator
elif command -v apt-get >/dev/null 2>&1; then
    run_root apt-get update -qq
    run_root apt-get install -y python3-gi python3-evdev gcc python3-dev \
        python3-pip gir1.2-ayatanaappindicator3-0.1 gnome-shell-extension-appindicator
elif command -v pacman >/dev/null 2>&1; then
    run_root pacman -S --needed --noconfirm python-gobject python-evdev gcc \
        python-pip libayatana-appindicator gnome-shell-extension-appindicator
elif command -v zypper >/dev/null 2>&1; then
    run_root zypper --non-interactive install python3-gobject python3-evdev \
        gcc python3-devel python3-pip libayatana-appindicator3-tools \
        typelib-1_0-AyatanaAppIndicator3-0_1
else
    warn "unknown package manager: install python3-gobject, python3-evdev and an"
    warn "AppIndicator library (AyatanaAppIndicator3 / AppIndicator3) manually"
fi

note "2/6 inputlock (pip, user install)"
python3 -m pip install --user --no-deps .

note "3/6 'input' group"
if id -nG "$USER_NAME" | grep -qw input; then
    echo "    $USER_NAME is already in 'input'"
else
    run_root usermod -aG input "$USER_NAME"
    warn "added $USER_NAME to 'input' - log out and back in for it to apply"
fi

note "4/6 desktop entry + autostart"
"$BIN" --install-desktop
"$BIN" --install-autostart

note "5/6 GNOME top-bar tray (extension)"
if command -v gnome-extensions >/dev/null 2>&1; then
    gnome-extensions enable appindicatorsupport@rgcjonas.gmail.com 2>/dev/null \
        && echo "    appindicator extension enabled" \
        || warn "enable it after a shell restart: gnome-extensions enable appindicatorsupport@rgcjonas.gmail.com"
fi

note "6/6 (re)start inputlock"
run_root_pkill() { pkill -f "\.local/bin/inputlock" 2>/dev/null || true; }
run_root_pkill
sleep 1
if id -nG | grep -qw input; then
    setsid nohup "$BIN" >/tmp/inputlock.log 2>&1 </dev/null &
else
    sg input -c "setsid nohup '$BIN' >/tmp/inputlock.log 2>&1 </dev/null &"
fi
sleep 2
if pgrep -f "\.local/bin/inputlock" >/dev/null 2>&1; then
    echo "    running (pid $(pgrep -f '\.local/bin/inputlock' | head -1))"
else
    warn "could not start it yet - check /tmp/inputlock.log"
fi

note "done. devices:"
if id -nG | grep -qw input; then
    "$BIN" --list-devices | tail -3
else
    sg input -c "'$BIN' --list-devices" | tail -3
fi
echo
echo "Open the tray icon in the top bar to choose a lock mode (keyboard / mouse / both)."
echo "Escape while locked: hold Ctrl+Alt+Shift+L for 2 seconds."
