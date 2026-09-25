# trayock

A tiny tray app for **Linux** that puts a padlock in your **top bar / system
tray** and lets you switch the **keyboard, the mouse, or both** off with one
click — kernel-level, via `evdev` exclusive grabs.

- **Three lock modes**, chosen from the tray menu (tick the one you want):
  *Lock keyboard + mouse*, *Lock keyboard only*, *Lock mouse only*.
- **Tray icon**: AppIndicator (AyatanaAppIndicator3 or AppIndicator3) — works
  on GNOME (top bar, with the AppIndicator extension), KDE, Cinnamon, XFCE,
  MATE and LXQt; falls back to `pystray` (StatusNotifier/XEmbed) otherwise.
- Left-click (or the menu item) toggles locked ↔ unlocked; the icon is a red
  closed padlock while locked, a green open padlock while unlocked.
- **Blocking**: `EvdevDevice.grab()` on every detected keyboard and pointer
  device — all input events are consumed by trayock and never reach the
  desktop.
- **Safety escape hatch**: hold **`Ctrl+Alt+Shift+L` for 2 seconds** at any
  time to force-unlock — works in every mode, on X11 and on Wayland.
- Pure Python, single package, no C extensions of its own.

## How it works

```
lock()   → evdev.list_devices() → classify capabilities → dev.grab() on each
           reader thread consumes every event (nothing reaches the desktop)
unlock() → dev.ungrab() + close on every device
escape   → the reader thread watches the grabbed keyboards; when
           Ctrl+Alt+Shift+L is held ≥ 2 s it releases everything
```

Device classification (`trayock/classify.py`):

| capability                                   | treated as |
|----------------------------------------------|------------|
| `EV_KEY` with letter keys (`KEY_A`, …)       | keyboard   |
| `EV_REL` with `REL_X` (mouse)                | pointer    |
| `EV_ABS` with `ABS_X` (touchpad/touchscreen)  | pointer    |
| power/sleep buttons, lid switches, …         | ignored    |

## Requirements

- Linux with `/dev/input/event*`
- Python ≥ 3.9
- A desktop with a system tray (GNOME needs the AppIndicator extension;
  KDE / Cinnamon / XFCE / MATE / LXQt have one built in)

### One command installs everything

```bash
./install.sh
```

It installs the system packages (dnf / apt / pacman / zypper), pip-installs
trayock for your user, adds you to the `input` group, installs the launcher
and autostart entry, enables the GNOME tray extension and starts the app.

### Manual package list per distro

<details>
<summary>Fedora / RHEL / openSUSE</summary>

```bash
# Fedora
sudo dnf install -y python3-gobject python3-evdev gcc python3-devel \
    python3-pip libayatana-appindicator-gtk3 gnome-shell-extension-appindicator

# openSUSE
sudo zypper install -y python3-gobject python3-evdev gcc python3-devel \
    python3-pip libayatana-appindicator3-tools typelib-1_0-AyatanaAppIndicator3-0_1
```

</details>

<details>
<summary>Ubuntu / Debian</summary>

```bash
sudo apt install -y python3-gi python3-evdev gcc python3-dev python3-pip \
    gir1.2-ayatanaappindicator3-0.1 gnome-shell-extension-appindicator
```

</details>

<details>
<summary>Arch / Manjaro</summary>

```bash
sudo pacman -S --needed python-gobject python-evdev gcc python-pip \
    libayatana-appindicator gnome-shell-extension-appindicator
```

</details>

Then, on **GNOME only**, make tray icons show up in the top bar (once per
system):

```bash
gnome-extensions enable appindicatorsupport@rgcjonas.gmail.com
# then log out and back in (or restart GNOME Shell on X11: Alt+F2, `r`)
```

## Installation

```bash
git clone https://github.com/Ramimaalej/trayock.git
cd trayock
python3 -m venv --system-site-packages ~/.local/share/trayock
~/.local/share/trayock/bin/pip install .
```

(`--system-site-packages` lets pip reuse the `python3-gobject`/`python3-evdev`
RPMs so nothing has to be compiled. Without it, pip will build PyGObject and
evdev from source — that works too, it just needs
`gcc python3-devel gobject-introspection-devel gtk3-devel cairo-devel
pkgconf` installed.)

Directly from GitHub:

```bash
pip install "git+https://github.com/Ramimaalej/trayock.git"
```

Optional pystray fallback (only needed if AyatanaAppIndicator3 is not
installed):

```bash
pip install "trayock[pystray]"
```

## Permissions (read this — root is *not* the answer)

`EVIOCGRAB` needs read access to `/dev/input/event*`. On Fedora those nodes
are owned `root:input` with mode `0660`, so **being in the `input` group is
enough** — do *not* run trayock as root.

```bash
sudo usermod -aG input $USER
# log out and back in, then verify:
groups                       # should now include: input
ls -l /dev/input/event*      # crw-rw---- root input ...
trayock --list-devices     # every row should say "ok"
```

If your system's udev rules don't already set the `input` group, install
this rule:

```bash
sudo tee /etc/udev/rules.d/99-trayock.rules <<'EOF'
# allow members of group "input" to access event devices
KERNEL=="event*", SUBSYSTEM=="input", GROUP="input", MODE="0660"
EOF
sudo udevadm control --reload && sudo udevadm trigger
```

When permissions are missing, trayock **refuses to fake a lock**: it shows
an error dialog with the exact devices it could not open, and stays
unlocked. `trayock --list-devices` prints the same diagnosis.

## Usage

```bash
trayock                     # start the tray app
trayock --mode keyboard     # default mode: grab keyboards only
trayock --mode mouse        # default mode: grab pointers only
trayock --list-devices      # diagnose devices/permissions, then exit
trayock --keyboard-only     # alias for --mode keyboard
trayock --escape-seconds 3  # change the escape hold time
trayock -v                  # debug logging
```

`--list-devices` reads every `/dev/input/event*` node (not just the ones you
may open), so devices you cannot access show up as `PERMISSION DENIED` with
the exact command that fixes them — their kind (keyboard/pointer) is read from
the world-readable sysfs capability masks. It exits non-zero when something is
wrong.

### Lock modes

Open the padlock menu in the top bar and tick one of:

| menu item                 | grabs                       | still usable |
|---------------------------|-----------------------------|--------------|
| *Lock keyboard + mouse*   | every keyboard + pointer    | nothing (combo only) |
| *Lock keyboard only*      | keyboards                   | mouse → tray stays clickable |
| *Lock mouse only*         | pointers                    | keyboard → the escape combo still works |

Selecting a mode locks immediately with it; selecting it again (or *Unlock
input*) releases everything. Picking a different mode while locked switches
the grab on the fly.

### Getting your input back

1. **Escape combo (always available):** hold **`Ctrl+Alt+Shift+L` for
   2 seconds**. trayock reads it itself from the evdev devices — no
   desktop shortcut needed — so it works on X11 and Wayland, in every mode.
   In *mouse only* mode it reads (but does not grab) the keyboards, so typing
   keeps working while the mouse is off.
2. **Tray menu:** click *Unlock input* — this works whenever the mouse is
   not grabbed (*keyboard + mouse* mode is the one where the icon cannot be
   clicked, which is what the combo is for).
3. **Last resort:** `pkill -f trayock` from a TTY, Ctrl+Alt+F3, or another
   machine — a released grab always returns input, even if trayock crashes.

## Desktop integration

Everything below writes into your own XDG directories — no root involved:

```bash
trayock --install-desktop   # app-grid / menu launcher + padlock icon
trayock --remove-desktop    # ... and take it away again
trayock --install-autostart # start trayock automatically at login
trayock --remove-autostart  # ... and stop
```

The same actions are available through `make desktop`, `make desktop-off`,
`make autostart` and `make autostart-off`.

`packaging/` holds the ready-made files for a system-wide install (RPM, manual
copy into `/usr/share/applications`, …):

- `packaging/trayock.desktop` — launcher with a plain `Exec=trayock`
- `packaging/99-trayock.rules` — udev rule granting the `input` group access
  (`sudo make udev`, or install it by hand as shown in [Permissions](#permissions))

## Wayland notes

- The lock itself is **kernel-level** (`EVIOCGRAB` on the evdev nodes), so it
  works the same on **X11 and Wayland**. Mutter/GNOME reads input through
  libinput on those same devices, so a grabbed device is invisible to the
  compositor too.
- The **escape combo is processed by trayock itself** (it reads the grabbed
  devices), not by the desktop — so it works regardless of compositor.
- Caveats under GNOME/Wayland:
  - Inputs that never go through the evdev nodes we grabbed still work:
    power/lid buttons, VT switching (`Ctrl+Alt+F1`…, handled inside the
    kernel's VT layer), SysRq, and any device we had no permission to open.
  - uinput-based virtual keyboards (e.g. `ydotool`, on-screen keyboards) are
    separate devices and are only blocked if trayock grabs them too
    (they are grabbed on the next 2-second rescan if they look like a
    keyboard/pointer).
  - A GNOME shortcut is never "half delivered": grabbed events never reach
    the compositor at all.
  - If trayock **crashes or is killed**, the kernel closes the file
    descriptors and releases all grabs automatically — input cannot stay
    stuck locked. The same happens if every grabbed device disappears
    (all keyboards unplugged): trayock unlocks itself instead of claiming
    to be locked while nothing is grabbed.
  - The tray icon needs the AppIndicator GNOME extension (top bar). On
    X11 without the extension you'd get nothing visible; that's a GNOME
    desktop limitation, not a Wayland one.

## Fallback tray (pystray)

If `AyatanaAppIndicator3` can't be imported, trayock tries `pystray`
(install with `pip install 'trayock[pystray]'`), which supports
AppIndicator/StatusNotifier/XEmbed depending on your desktop. The padlock
image for that path is drawn at runtime with Pillow — no extra assets.

## Development

```bash
make dev      # python3 -m venv --system-site-packages .venv + editable install
make check    # ruff + unittest
make dist     # sdist + wheel into dist/
```

Equivalent without make:

```bash
python3 -m venv --system-site-packages .venv
.venv/bin/pip install -e .
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/ruff check .
```

Project layout:

```
pyproject.toml
Makefile                 dev / test / lint / dist / desktop / autostart / udev
packaging/
    trayock.desktop    ready-made launcher for system-wide installs
    99-trayock.rules   udev rule for the 'input' group
src/trayock/
    __init__.py          version
    __main__.py          python -m trayock
    app.py               CLI + wiring (tray ↔ blocker)
    blocker.py           evdev grab/ungrab, reader thread, escape combo
    classify.py          pure capability classification (unit-tested)
    desktop.py           .desktop / autostart installers (unit-tested)
    tray.py              AyatanaAppIndicator3 tray, pystray fallback
    data/trayock.desktop   template the installer fills in
    icons/*.svg          open/closed padlock icons
tests/                   unittest suite (no hardware needed)
```

## Troubleshooting

| Symptom | Fix |
|---|---|
| No icon in the top bar | Install/enable `gnome-shell-extension-appindicator` (see Requirements). |
| `--list-devices` shows `PERMISSION DENIED` | Your user is not in the `input` group yet (see Permissions), then log out/in. |
| `permission denied` on `/dev/input/event*` | Join the `input` group and log in again (see Permissions). |
| `grab failed (Device or resource busy)` | Another tool (input-remapper, `evtest --grab`) holds an exclusive grab. |
| Icon there but click does nothing | Use the menu item, or `--keyboard-only`; on GNOME left-click may open the menu instead of emitting `activate`. |
| pystray fallback not visible | GNOME needs the same AppIndicator extension; install `trayock[pystray]` only provides the library, not the shell support. |

## License

MIT — see [LICENSE](LICENSE).
