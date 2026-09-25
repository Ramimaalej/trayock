"""Pure classification of evdev device capabilities (no I/O, unit-testable)."""

from __future__ import annotations

from typing import Any, Dict, Optional, Set

from evdev import ecodes

#: Letter/word keys that identify a real keyboard (excludes power buttons,
#: sleep buttons, media remotes, ... which only carry KEY_POWER/KEY_VOLUMEUP).
KEYBOARD_KEY_CANDIDATES = frozenset(
    {ecodes.KEY_A, ecodes.KEY_Q, ecodes.KEY_Z, ecodes.KEY_SPACE, ecodes.KEY_ENTER}
)


def _code_set(caps: Dict[Any, Any], etype: int) -> Set[int]:
    """Return the set of event codes for ``etype`` from a capabilities dict.

    ``InputDevice.capabilities()`` can yield several shapes depending on the
    ``verbose`` flag and evdev version: ``{etype: {code: name}}``,
    ``{etype: {code, ...}}``, ``{etype: [code_or_name, ...]}`` and for axes
    ``{etype: [(code, AbsInfo), ...]}``. All of them are normalised here to a
    set of integer codes; anything unrecognised is skipped instead of raising.
    """
    raw = caps.get(etype)
    if not raw:
        return set()
    items = list(raw.keys()) if isinstance(raw, dict) else list(raw)
    codes: Set[int] = set()
    for item in items:
        if isinstance(item, (tuple, list)):
            # (ABS_X, AbsInfo(...)) style entries: only the code is wanted
            item = item[0] if item else None
        if item is None:
            continue
        if isinstance(item, str):
            code = ecodes.ecodes.get(item)
            if code is not None:
                codes.add(code)
            continue
        try:
            codes.add(int(item))
        except (TypeError, ValueError):
            continue
    return codes


def classify(caps: Dict[Any, Any]) -> Optional[str]:
    """Classify a device from its capability dict.

    Returns ``"keyboard"``, ``"pointer"`` (mouse/touchpad/touchscreen) or
    ``None`` for anything else (power buttons, lid switches, ...).
    """
    if _code_set(caps, ecodes.EV_KEY) & KEYBOARD_KEY_CANDIDATES:
        return "keyboard"
    if ecodes.REL_X in _code_set(caps, ecodes.EV_REL):
        return "pointer"
    if ecodes.ABS_X in _code_set(caps, ecodes.EV_ABS):
        return "pointer"
    return None
