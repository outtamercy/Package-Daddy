"""Resolve MO2-owned paths without guessing where a user installed anything."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PyQt6.QtCore import QCoreApplication


@dataclass(frozen=True)
class Mo2Paths:
    """The executable root is for shared MO2 assets; INI is instance-specific."""

    install_root: Path
    ini_path: Path | None
    instance_root: Path | None


def _as_path(value) -> Path | None:
    """MO2 API paths may be QDir-like objects or ordinary strings."""
    if value is None:
        return None
    try:
        if hasattr(value, "absolutePath"):
            value = value.absolutePath()
        text = str(value).strip()
        return Path(text).resolve() if text else None
    except (OSError, TypeError, ValueError):
        return None


def resolve(organizer) -> Mo2Paths:
    """Use MO2 APIs first, then identify the instance INI without guessing.

    Nolvus and other managed/global installs can keep an instance base separate
    from the directory holding ModOrganizer.exe. Both are meaningful: themes,
    tools and plugins belong beside the executable; instance-only settings
    belong beside the INI MO2 says it is using.
    """
    install_root = _as_path(QCoreApplication.applicationDirPath())
    if install_root is None:
        raise RuntimeError("MO2 did not provide its running application directory")

    instance_root = None
    try:
        base_path = getattr(organizer, "basePath", None)
        instance_root = _as_path(base_path() if callable(base_path) else None)
    except Exception:
        # The app folder is still a valid install root; we just can't promise
        # an instance-specific INI through the API. Annoying, but survivable.
        instance_root = None

    # The API-provided base gets first dibs. The exe-side INI is the portable
    # fallback. Don't go spelunking through random folders.
    candidates = []
    if instance_root is not None:
        candidates.append(instance_root / "ModOrganizer.ini")
    candidates.append(install_root / "ModOrganizer.ini")
    ini_path = next((path for path in candidates if path.is_file()), None)
    return Mo2Paths(install_root=install_root, ini_path=ini_path, instance_root=instance_root)
