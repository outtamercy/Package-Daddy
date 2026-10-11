"""Theme Daddy: rules for packages that belong in MO2's stylesheets folder.

Themes are intentionally separate from plugins.  A theme may contain a .qss
file plus images, SVGs, or fonts, but it never gets Python or executable-file
privileges just because it shares a Downloads folder with a plugin.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath


KIND = "mo2-theme"
READY = "theme bundle — choose reviewed payload"
_ALLOWED_SUFFIXES = frozenset({".qss", ".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".svg", ".ico", ".ttf", ".otf", ".woff", ".woff2"})


@dataclass(frozen=True)
class ThemeChoice:
    label: str
    prefix: str
    files: tuple[str, ...]


def accepts(kind: str) -> bool:
    return kind == KIND


def destination_root(mo2_root: Path) -> Path:
    """MO2 discovers styles from its own root; Daddy never creates a random home."""
    return mo2_root / "stylesheets"


def active_stylesheet(ini_path: Path | None) -> str:
    """Read MO2's selected stylesheet without touching the rest of its INI."""
    if ini_path is None or not ini_path.is_file():
        return ""
    in_settings = False
    try:
        for raw_line in ini_path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = raw_line.strip()
            if line.startswith("[") and line.endswith("]"):
                in_settings = line.casefold() == "[settings]"
                continue
            if in_settings and line.casefold().startswith("style="):
                return line.partition("=")[2].strip()
    except OSError:
        return ""
    return ""


def fallback_stylesheet(stylesheets_root: Path, removing: tuple[str, ...]) -> str:
    """Return a remaining root stylesheet, or nothing if removal would leave none."""
    removed = {PurePosixPath(item).name.casefold() for item in removing if item.lower().endswith(".qss")}
    available = [
        item.name for item in stylesheets_root.glob("*.qss")
        if item.is_file() and not item.is_symlink() and item.name.casefold() not in removed
    ]
    by_name = {item.casefold(): item for item in available}
    for preferred in ("night eyes.qss", "dark.qss"):
        if preferred in by_name:
            return by_name[preferred]
    return sorted(available, key=str.casefold)[0] if available else ""


def switch_active_stylesheet(ini_path: Path | None, stylesheets_root: Path, removing: tuple[str, ...]) -> str:
    """Move MO2 off a stylesheet Daddy is about to remove, preserving its INI.

    An intact active theme is left alone. If removal would leave no other theme,
    Daddy refuses rather than dumping the user back into the white default UI.
    """
    current = active_stylesheet(ini_path)
    removed = {PurePosixPath(item).name.casefold() for item in removing if item.lower().endswith(".qss")}
    if not current or Path(current).name.casefold() not in removed:
        return ""
    fallback = fallback_stylesheet(stylesheets_root, removing)
    if not fallback:
        raise RuntimeError("the active MO2 stylesheet is being removed and no other stylesheet is available; Daddy refused to fall back to default white")
    if ini_path is None or not ini_path.is_file():
        raise RuntimeError("the active MO2 stylesheet is being removed, but Daddy could not find ModOrganizer.ini to switch it safely")
    try:
        lines = ini_path.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
        in_settings = False
        changed = False
        rewritten: list[str] = []
        for raw_line in lines:
            line = raw_line.strip()
            if line.startswith("[") and line.endswith("]"):
                in_settings = line.casefold() == "[settings]"
            if in_settings and line.casefold().startswith("style="):
                ending = "\r\n" if raw_line.endswith("\r\n") else "\n" if raw_line.endswith("\n") else ""
                rewritten.append(f"style={fallback}{ending}")
                changed = True
            else:
                rewritten.append(raw_line)
        if not changed:
            raise RuntimeError("ModOrganizer.ini has no [Settings] style entry to switch")
        temporary = ini_path.with_name(ini_path.name + ".theme-daddy-new")
        with temporary.open("x", encoding="utf-8", newline="") as stream:
            stream.writelines(rewritten)
        os.replace(temporary, ini_path)
    except OSError as exc:
        raise RuntimeError(f"could not switch MO2's active stylesheet: {exc}") from exc
    return fallback


def safe_file_list(files: tuple[str, ...]) -> bool:
    """Allow ordinary stylesheet payloads, rooted only inside stylesheets."""
    if not files or not any(Path(item).suffix.lower() == ".qss" for item in files):
        return False
    for item in files:
        path = PurePosixPath(item.replace("\\", "/"))
        if not item or path.is_absolute() or ".." in path.parts or ":" in item:
            return False
        if Path(item).suffix.lower() not in _ALLOWED_SUFFIXES:
            return False
    return True


def _label(prefix: tuple[str, ...]) -> str:
    path_text = "/".join(prefix).strip()
    lowered = path_text.casefold()
    if lowered.startswith("1 place and go"):
        return "Everything (bundle default)"
    optional_prefix = "2 optional - pick your themes/"
    if lowered.startswith(optional_prefix):
        # The archive is FOMOD-ish: Gloss/Blades, No gloss/Blades, etc. Turn
        # that machinery into names humans can actually read.
        parts = path_text[len(optional_prefix):].split("/")
        if len(parts) >= 2:
            return f"{parts[1]} — {parts[0]}"
        return " — ".join(parts)
    return " / ".join(prefix).strip() or "Theme payload"


def choices(archive: Path) -> tuple[ThemeChoice, ...]:
    """Find stylesheet payloads only; plugin files in the same ZIP are ignored."""
    grouped: dict[tuple[str, ...], list[str]] = {}
    with zipfile.ZipFile(archive) as bundle:
        for name in bundle.namelist():
            if name.endswith("/") or not _safe_relative(name):
                continue
            parts = PurePosixPath(name).parts
            lowered = tuple(part.lower() for part in parts)
            if "stylesheets" not in lowered:
                continue
            index = lowered.index("stylesheets")
            relative = PurePosixPath(*parts[index + 1:]).as_posix()
            if not relative or not _safe_relative(relative) or Path(relative).suffix.lower() not in _ALLOWED_SUFFIXES:
                continue
            grouped.setdefault(parts[:index], []).append(relative)
    reviewed = []
    for prefix, files in grouped.items():
        unique = tuple(sorted(set(files), key=str.lower))
        # Some bundles (Black Hand, for one) put several selectable QSS files
        # beside one shared asset folder. That's a choice, not one mysterious
        # 300-file theme. Split them, keep the shared stuff.
        root_stylesheets = tuple(
            item for item in unique
            if PurePosixPath(item).parent == PurePosixPath(".") and Path(item).suffix.lower() == ".qss"
        )
        if not prefix and len(root_stylesheets) > 1:
            shared = tuple(item for item in unique if item not in root_stylesheets)
            for stylesheet in root_stylesheets:
                reviewed.append(ThemeChoice(Path(stylesheet).stem, "", tuple((*shared, stylesheet))))
        elif any(Path(item).suffix.lower() == ".qss" for item in unique):
            reviewed.append(ThemeChoice(_label(prefix), PurePosixPath(*prefix).as_posix() if prefix else "", unique))
    return tuple(sorted(reviewed, key=lambda item: item.label.lower()))


def reviewable(archive: Path) -> bool:
    try:
        return bool(choices(archive))
    except (OSError, zipfile.BadZipFile):
        return False


def install(archive: Path, selected: tuple[ThemeChoice, ...], stylesheets_root: Path, write_receipt) -> tuple[str, ...]:
    """Install selected theme files after a no-overwrite preflight."""
    if not selected:
        raise RuntimeError("choose at least one theme payload")
    sources: dict[str, str] = {}
    for choice in selected:
        prefix = choice.prefix + "/stylesheets/" if choice.prefix else "stylesheets/"
        for relative in choice.files:
            source = prefix + relative
            old = sources.get(relative)
            if old is not None and old != source:
                raise RuntimeError(f"selected theme choices both provide {relative}; pick one of those variants")
            sources[relative] = source
    if not sources or not any(Path(item).suffix.lower() == ".qss" for item in sources):
        raise RuntimeError("selected payload has no stylesheet")
    stylesheets_root.mkdir(parents=True, exist_ok=True)
    conflicts = [stylesheets_root / relative for relative in sources if (stylesheets_root / relative).exists()]
    if conflicts:
        preview = "\n".join(str(path) for path in conflicts[:8])
        suffix = "\n…" if len(conflicts) > 8 else ""
        raise RuntimeError(f"Theme Daddy will not overwrite existing stylesheet files:\n{preview}{suffix}")
    created: list[Path] = []
    try:
        with zipfile.ZipFile(archive) as bundle, tempfile.TemporaryDirectory(prefix="theme_daddy_", dir=stylesheets_root.parent) as staging_name:
            staging = Path(staging_name)
            available = set(bundle.namelist())
            missing = [source for source in sources.values() if source not in available]
            if missing:
                raise RuntimeError(f"archive changed since review; missing {missing[0]}")
            for relative, source in sources.items():
                staged = staging / PurePosixPath(relative)
                staged.parent.mkdir(parents=True, exist_ok=True)
                with bundle.open(source, "r") as incoming, staged.open("xb") as outgoing:
                    shutil.copyfileobj(incoming, outgoing, length=1024 * 1024)
            for relative in sources:
                staged = staging / PurePosixPath(relative)
                target = stylesheets_root / PurePosixPath(relative)
                target.parent.mkdir(parents=True, exist_ok=True)
                os.replace(staged, target)
                created.append(target)
        hashes = {path.relative_to(stylesheets_root).as_posix(): _sha256(path) for path in created}
        write_receipt(tuple(sorted(sources)), hashes)
        return tuple(sorted(sources))
    except Exception:
        for path in reversed(created):
            try:
                path.unlink()
            except OSError:
                pass
        prune_empty_parents(created, stylesheets_root)
        raise


def removable_files(receipt: dict, stylesheets_root: Path) -> tuple[Path, ...]:
    """Validate a theme receipt and refuse missing or changed files."""
    if receipt.get("target") != "stylesheets":
        raise RuntimeError("receipt is not a Theme Daddy stylesheet receipt")
    files, hashes = receipt.get("files"), receipt.get("hashes")
    if not isinstance(files, list) or not isinstance(hashes, dict) or not files:
        raise RuntimeError("theme receipt is incomplete")
    paths = []
    for relative in files:
        if not isinstance(relative, str) or not _safe_relative(relative) or Path(relative).suffix.lower() not in _ALLOWED_SUFFIXES:
            raise RuntimeError("theme receipt contains an unsafe file path")
        target = stylesheets_root / PurePosixPath(relative)
        expected = hashes.get(relative)
        if not isinstance(expected, str) or not target.is_file():
            raise RuntimeError(f"theme file is missing: {relative}")
        if _sha256(target) != expected:
            raise RuntimeError(f"theme file changed after install: {relative}")
        paths.append(target)
    return tuple(paths)


def remove(files: tuple[Path, ...], stylesheets_root: Path) -> None:
    for path in files:
        path.unlink()
    prune_empty_parents(list(files), stylesheets_root)


def _safe_relative(value: str) -> bool:
    path = PurePosixPath(value.replace("\\", "/"))
    return bool(value) and not path.is_absolute() and ".." not in path.parts and ":" not in value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def prune_empty_parents(paths: list[Path], root: Path) -> None:
    for path in paths:
        parent = path.parent
        while parent != root:
            try:
                parent.rmdir()
            except OSError:
                break
            parent = parent.parent
