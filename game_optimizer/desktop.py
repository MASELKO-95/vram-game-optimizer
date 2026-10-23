from __future__ import annotations

from dataclasses import asdict, dataclass
import os
import re
import time

from .config import PLASMA_SERVICE, RAM_SERVICES
from .system import Result, System, session_type


@dataclass(frozen=True)
class Desktop:
    name: str
    label: str
    session: str
    compositor_backend: str | None = None
    shell_service: str | None = None
    ram_services: tuple[str, ...] = ()

    @property
    def summary(self) -> str:
        if self.compositor_backend == "kwin":
            return "Można wstrzymać efekty KWin oraz panel i tapetę Plasmy."
        if self.compositor_backend == "xfwm4":
            return "Można wstrzymać kompozycję Xfwm4. Panel i tapeta pozostają aktywne."
        if self.compositor_backend == "marco":
            return "Można wstrzymać kompozycję Marco. Panel i tapeta pozostają aktywne."
        if self.shell_service:
            return "Można zatrzymać panel i tapetę Plasmy. Kompozytor Waylanda pozostaje aktywny."
        if self.session == "wayland":
            return "Kompozytor Waylanda pozostaje aktywny; dostępne są priorytety gry, GameMode i wybrane aplikacje RAM."
        return "Dostępne są priorytety gry, GameMode i wybrane aplikacje RAM; powłoka pulpitu pozostaje aktywna."

    def to_dict(self) -> dict:
        return {**asdict(self), "summary": self.summary}


def detect_desktop(session: str | None = None) -> Desktop:
    session = session or session_type()
    raw = os.environ.get("XDG_CURRENT_DESKTOP", "")
    # XDG_CURRENT_DESKTOP may contain a list, e.g. ubuntu:GNOME or Budgie:GNOME.
    # Match whole identifiers, preferring specific shells over their base GNOME.
    tokens = {token.strip().casefold() for token in re.split(r"[:;,]", raw) if token.strip()}
    if not tokens:
        tokens = {os.environ.get("XDG_SESSION_DESKTOP", os.environ.get("DESKTOP_SESSION", "")).casefold().removesuffix(".desktop")}
    aliases = (
        ("kde", "KDE Plasma", {"kde", "plasma", "plasmax11", "plasmawayland"}),
        ("cinnamon", "Cinnamon", {"cinnamon", "x-cinnamon"}),
        ("budgie", "Budgie", {"budgie", "budgie-desktop"}),
        ("pantheon", "Pantheon", {"pantheon"}),
        ("xfce", "Xfce", {"xfce", "xfce4", "x-xfce"}),
        ("mate", "MATE", {"mate"}),
        ("lxqt", "LXQt", {"lxqt"}),
        ("lxde", "LXDE", {"lxde"}),
        ("hyprland", "Hyprland", {"hyprland"}),
        ("sway", "Sway", {"sway"}),
        ("i3", "i3", {"i3"}),
        ("openbox", "Openbox", {"openbox"}),
        ("gnome", "GNOME", {"gnome", "gnome-classic", "gnome-xorg", "gnome-wayland", "unity"}),
    )
    name, label = "unknown", "Nieznany pulpit"
    for key, title, known in aliases:
        if tokens & known:
            name, label = key, title
            break
    if name == "unknown" and os.environ.get("SWAYSOCK"):
        name, label = "sway", "Sway"
    elif name == "unknown" and os.environ.get("HYPRLAND_INSTANCE_SIGNATURE"):
        name, label = "hyprland", "Hyprland"
    backend = {"kde": "kwin", "xfce": "xfwm4", "mate": "marco"}.get(name) if session == "x11" else None
    return Desktop(name, label, session, backend,
                   PLASMA_SERVICE if name == "kde" else None,
                   RAM_SERVICES if name == "kde" else ())


def compositor_active(system: System, backend: str) -> bool | None:
    if backend == "kwin":
        return system.compositor_active()
    if backend == "xfwm4":
        result = system.run(["xfconf-query", "--channel", "xfwm4", "--property", "/general/use_compositing"])
    elif backend == "marco":
        result = system.run(["gsettings", "get", "org.mate.Marco.general", "compositing-manager"])
    else:
        raise ValueError("Nieobsługiwany kompozytor.")
    if result.ok and result.stdout in ("true", "false"):
        return result.stdout == "true"
    return None


def compositor_snapshot(system: System, backend: str) -> dict | None:
    if backend in ("xfwm4", "marco"):
        manager = system.window_manager_name()
        if manager is None or manager.casefold() != backend:
            # An Xfce/MATE session can run Compiz, Picom or a different WM.
            # Do not modify unused preferences based on the desktop name alone.
            return None
    current = compositor_active(system, backend)
    if current is None:
        return None
    action = {"kind": "compositor", "backend": backend, "before": current}
    if backend == "marco" and current:
        # Preserve an unset key as unset, rather than adding a permanent override.
        writable = system.run(["gsettings", "writable", "org.mate.Marco.general", "compositing-manager"])
        stored = system.run(["dconf", "read", "/org/mate/marco/general/compositing-manager"])
        if not writable.ok or writable.stdout != "true" or not stored.ok or stored.stdout not in ("", "true", "false"):
            return None
        action["user_value"] = None if not stored.stdout else stored.stdout == "true"
    return action


def set_compositor(system: System, backend: str, enabled: bool) -> Result:
    if backend == "kwin":
        return system.set_compositor(enabled)
    current = compositor_active(system, backend)
    if current is None:
        return Result(1, "", "Brak potwierdzonego stanu kompozytora lub wymaganego narzędzia.")
    if current == enabled:
        return Result(0)
    value = "true" if enabled else "false"
    if backend == "xfwm4":
        args = ["xfconf-query", "--channel", "xfwm4", "--property", "/general/use_compositing", "--set", value]
    elif backend == "marco":
        args = ["gsettings", "set", "org.mate.Marco.general", "compositing-manager", value]
    else:
        raise ValueError("Nieobsługiwany kompozytor.")
    result = system.run(args)
    if not result.ok:
        return result
    for _ in range(12):
        if compositor_active(system, backend) == enabled:
            return Result(0)
        time.sleep(.05)
    return Result(1, "", "Kompozytor nie potwierdził zmiany ustawienia.")


def persistent_compositor_action(action: dict) -> bool:
    return action.get("kind") == "compositor" and action.get("backend") in ("xfwm4", "marco")


def restore_compositor(system: System, action: dict) -> Result:
    # Journals created before desktop adapters refer to KWin.
    backend = action.get("backend", "kwin")
    before = action.get("before", True)
    if backend not in ("kwin", "xfwm4", "marco") or type(before) is not bool:
        raise ValueError("Nieprawidłowy kompozytor lub stan w dzienniku.")
    if backend != "marco":
        return set_compositor(system, backend, before)
    if "user_value" not in action or (action["user_value"] is not None and type(action["user_value"]) is not bool):
        raise ValueError("Brak poprzedniej wartości ustawienia Marco.")
    stored = system.run(["dconf", "read", "/org/mate/marco/general/compositing-manager"])
    if not stored.ok or stored.stdout not in ("", "true", "false"):
        return Result(1, "", "Nie można odczytać poprzedniego ustawienia Marco.")
    # Only undo our false override; preserve subsequent user changes.
    if stored.stdout != "false":
        return Result(0)
    previous = action["user_value"]
    args = (["gsettings", "reset", "org.mate.Marco.general", "compositing-manager"] if previous is None else
            ["gsettings", "set", "org.mate.Marco.general", "compositing-manager", "true" if previous else "false"])
    result = system.run(args)
    if not result.ok:
        return result
    stored = system.run(["dconf", "read", "/org/mate/marco/general/compositing-manager"])
    expected = "" if previous is None else ("true" if previous else "false")
    return Result(0) if stored.ok and stored.stdout == expected else Result(1, "", "Nie potwierdzono przywrócenia ustawienia Marco.")
