"""Plugin Daddy: safety rules for MO2 Python plugin packages.

Package Daddy owns the window.  This module owns what makes a plugin package a
plugin package, and nothing else.  Keeping it here means themes and tools do
not slowly acquire a pile of plugin-only assumptions.
"""
from __future__ import annotations

import os
import shutil
import tempfile
import zipfile
from pathlib import Path, PurePosixPath


KINDS = frozenset({"mo2-plugin", "mo2-bridge"})


def accepts(kind: str) -> bool:
    return kind in KINDS


def destination(mo2_root: Path, install_root: str) -> Path:
    """Plugins live in one owned folder immediately below MO2's plugins root."""
    return mo2_root / "plugins" / install_root


def structurally_reviewed(files: tuple[str, ...]) -> bool:
    """A catalog-only plugin needs a normal Python entry point before install."""
    return "__init__.py" in files or "plugin.py" in files


def safe_file_list(files: tuple[str, ...]) -> bool:
    """Plugin package members remain relative to their one owned folder."""
    return all(
        bool(item)
        and not PurePosixPath(item.replace("\\", "/")).is_absolute()
        and ".." not in PurePosixPath(item.replace("\\", "/")).parts
        and ":" not in item
        for item in files
    )


def install(candidate, destination: Path, write_receipt) -> None:
    """Stage and atomically install one reviewed plugin package.

    ``write_receipt`` is deliberately supplied by Package Daddy's ledger
    layer. Plugin Daddy handles files; the UI owns user-facing receipts.
    """
    if not accepts(candidate.kind):
        raise RuntimeError(f"Plugin Daddy cannot install package kind {candidate.kind!r}")
    if candidate.archive.suffix.lower() != ".zip":
        raise RuntimeError("only validated ZIP packages can install until Daddy bundles current 7-Zip")
    if not destination.parent.is_dir():
        raise RuntimeError(f"MO2 plugins directory is unavailable: {destination.parent}")
    with zipfile.ZipFile(candidate.archive) as bundle:
        names = set(bundle.namelist())
        if candidate.manifest_path:
            member = candidate.manifest_path
            if member not in names:
                raise RuntimeError("manifest changed since scan; scan again")
            wrapper = str(PurePosixPath(member).parent)
            prefix = "" if wrapper == "." else wrapper + "/"
        elif candidate.status == "approved catalog ZIP — ready for install review":
            prefix = candidate.install_root + "/"
        else:
            raise RuntimeError("archive is not authorized for installation")
        sources = [(relative, prefix + relative) for relative in candidate.files]
        missing = [source for _, source in sources if source not in names]
        if missing:
            raise RuntimeError(f"archive changed since scan; declared member missing: {missing[0]}")
        with tempfile.TemporaryDirectory(prefix="package_daddy_", dir=destination.parent) as staging_name:
            staging_root = Path(staging_name) / candidate.install_root
            staging_root.mkdir()
            for relative, source in sources:
                target = staging_root / PurePosixPath(relative)
                target.parent.mkdir(parents=True, exist_ok=True)
                with bundle.open(source, "r") as incoming, target.open("xb") as outgoing:
                    shutil.copyfileobj(incoming, outgoing, length=1024 * 1024)
            if destination.exists() or destination.is_symlink():
                raise RuntimeError("install destination appeared during staging; refused to overwrite it")
            os.replace(staging_root, destination)
            try:
                write_receipt(candidate, [relative for relative, _ in sources])
            except Exception:
                shutil.rmtree(destination)
                raise
