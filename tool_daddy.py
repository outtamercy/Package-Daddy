"""Tool Daddy: rules for self-contained tools installed beside MO2.

Tools go to <MO2 root>/tools/<owned folder>.  If MO2 has no tools directory,
Tool Daddy may create exactly that one directory during a confirmed install.
Nothing spills elsewhere on the drive.  Ever.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path
from pathlib import PurePosixPath


KIND = "mo2-tool"


def accepts(kind: str) -> bool:
    return kind == KIND


def destination(mo2_root: Path, install_root: str) -> Path:
    return mo2_root / "tools" / install_root


def tools_root(mo2_root: Path) -> Path:
    return mo2_root / "tools"


def _seven_zip(mo2_root: Path) -> Path | None:
    """Find a deliberate 7-Zip backend; never scrape a random drive for one."""
    candidates = [
        Path(__file__).parent / "bin" / "7z.exe",  # Daddy's future bundled copy.
        mo2_root / "7z.exe",
        mo2_root / "tools" / "Mopy" / "bash" / "compiled" / "7z.exe",
    ]
    found = shutil.which("7z") or shutil.which("7z.exe")
    if found:
        candidates.append(Path(found))
    return next((path for path in candidates if path.is_file()), None)


def archive_files(archive: Path, mo2_root: Path) -> tuple[str, ...]:
    """List .zip or .7z payload names without extracting or executing them."""
    if archive.suffix.lower() == ".zip":
        with zipfile.ZipFile(archive) as bundle:
            return tuple(name.replace("\\", "/") for name in bundle.namelist() if not name.endswith("/"))
    if archive.suffix.lower() != ".7z":
        raise RuntimeError("Tool Daddy currently reviews ZIP and 7Z tool archives only")
    seven_zip = _seven_zip(mo2_root)
    if seven_zip is None:
        raise RuntimeError("Tool Daddy needs its bundled 7-Zip reader (or MO2's Mopy 7z.exe) to inspect this .7z archive")
    result = subprocess.run(
        [str(seven_zip), "l", "-slt", str(archive)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if result.returncode != 0:
        raise RuntimeError("7-Zip could not read this archive: " + (result.stderr.strip() or result.stdout.strip() or "unknown error"))
    files = []
    for block in result.stdout.split("\n\n"):
        fields = {}
        for line in block.splitlines():
            if " = " in line:
                key, value = line.split(" = ", 1)
                fields[key] = value
        name = fields.get("Path", "").replace("\\", "/")
        attributes = fields.get("Attributes", "").strip()
        # 7-Zip has three different ways to say "directory." They're real
        # entries, just not files Daddy gets to copy into a tool install.
        if name and not attributes.startswith("D") and fields.get("Folder", "").strip() != "+":
            files.append(name)
    # The preamble's first Path is the archive itself, not a package member.
    # Leave it out instead of trusting that nonsense.
    return tuple(name for name in files if "/" not in name[:3] and ":" not in name[:3])


def safe_file_list(files: tuple[str, ...]) -> bool:
    """Tools are still ordinary files, never a licence for path weirdness."""
    return all(
        bool(item)
        and not PurePosixPath(item.replace("\\", "/")).is_absolute()
        and ".." not in PurePosixPath(item.replace("\\", "/")).parts
        and ":" not in item
        for item in files
    )


def install(candidate, destination: Path, write_receipt) -> None:
    """Stage one declared tool into its own folder beneath MO2's tools root.

    The tools root is created here, after the user has explicitly confirmed an
    install. Nothing consults a drive-wide tool directory or invents one in
    AppData because that would be gross.
    """
    if not accepts(candidate.kind):
        raise RuntimeError(f"Tool Daddy cannot install package kind {candidate.kind!r}")
    root = destination.parent
    root.mkdir(parents=True, exist_ok=True)
    if not root.is_dir() or root.is_symlink():
        raise RuntimeError(f"MO2 tools folder is unavailable: {root}")
    staged, files = _stage(candidate, destination)
    staging_root = staged.parent
    try:
        os.replace(staged, destination)
        write_receipt(candidate, files)
    except Exception:
        if destination.exists():
            shutil.rmtree(destination, ignore_errors=True)
        raise
    finally:
        shutil.rmtree(staging_root, ignore_errors=True)


def update(candidate, destination: Path, write_receipt) -> Path:
    """Replace one reviewed tool, retaining the previous folder beside MO2.

    An update is still a user-requested destructive action.  The old folder is
    moved to a small Daddy backup first, so a failed replacement never turns a
    working tool into vaporware.
    """
    if not accepts(candidate.kind):
        raise RuntimeError(f"Tool Daddy cannot update package kind {candidate.kind!r}")
    if not destination.is_dir() or destination.is_symlink():
        raise RuntimeError("the existing tool folder is unavailable for a safe update")
    staged, files = _stage(candidate, destination)
    staging_root = staged.parent
    backup_root = destination.parent / ".package-daddy-backups"
    backup_root.mkdir(exist_ok=True)
    stamp = __import__("datetime").datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = backup_root / f"{destination.name}-{stamp}"
    try:
        os.replace(destination, backup)
        os.replace(staged, destination)
        write_receipt(candidate, files)
        return backup
    except Exception:
        if destination.exists() and destination != backup:
            shutil.rmtree(destination, ignore_errors=True)
        if backup.exists() and not destination.exists():
            os.replace(backup, destination)
        raise
    finally:
        shutil.rmtree(staging_root, ignore_errors=True)


def _sources(candidate, names: set[str]) -> list[tuple[str, str]]:
    if candidate.manifest_path:
        member = candidate.manifest_path
        if member not in names:
            raise RuntimeError("manifest changed since scan; scan again")
        wrapper = str(PurePosixPath(member).parent)
        prefix = "" if wrapper == "." else wrapper + "/"
    elif candidate.status == "approved catalog ZIP — ready for install review":
        prefix = getattr(candidate, "archive_prefix", "")
    else:
        raise RuntimeError("archive is not authorized for Tool Daddy installation")
    sources = [(relative, prefix + relative) for relative in candidate.files]
    missing = [source for _, source in sources if source not in names]
    if missing:
        raise RuntimeError(f"archive changed since scan; reviewed member missing: {missing[0]}")
    return sources


def _stage(candidate, destination: Path) -> tuple[Path, list[str]]:
    """Copy only reviewed members to a private sibling staging folder."""
    root = destination.parent
    if candidate.archive.suffix.lower() == ".zip":
        return _stage_zip(candidate, root)
    if candidate.archive.suffix.lower() == ".7z":
        return _stage_7z(candidate, root)
    raise RuntimeError("Tool Daddy currently installs reviewed ZIP and 7Z tool archives only")


def _stage_zip(candidate, root: Path) -> tuple[Path, list[str]]:
    with zipfile.ZipFile(candidate.archive) as bundle:
        names = {name.replace("\\", "/") for name in bundle.namelist()}
        sources = _sources(candidate, names)
        staging_name = tempfile.mkdtemp(prefix="tool_daddy_", dir=root)
        staged = Path(staging_name) / candidate.install_root
        try:
            staged.mkdir()
            for relative, source in sources:
                target = staged / PurePosixPath(relative)
                target.parent.mkdir(parents=True, exist_ok=True)
                with bundle.open(source, "r") as incoming, target.open("xb") as outgoing:
                    shutil.copyfileobj(incoming, outgoing, length=1024 * 1024)
            return staged, [relative for relative, _ in sources]
        except Exception:
            shutil.rmtree(staging_name, ignore_errors=True)
            raise


def _stage_7z(candidate, root: Path) -> tuple[Path, list[str]]:
    mo2_root = root.parent
    seven_zip = _seven_zip(mo2_root)
    if seven_zip is None:
        raise RuntimeError("Tool Daddy needs its bundled 7-Zip reader (or MO2's Mopy 7z.exe) to unpack this .7z archive")
    names = set(archive_files(candidate.archive, mo2_root))
    sources = _sources(candidate, names)
    staging_name = tempfile.mkdtemp(prefix="tool_daddy_", dir=root)
    staging_root = Path(staging_name)
    unpacked = staging_root / "unpacked"
    staged = staging_root / candidate.install_root
    try:
        result = subprocess.run(
            [str(seven_zip), "x", "-y", f"-o{unpacked}", str(candidate.archive)],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if result.returncode != 0:
            raise RuntimeError("7-Zip could not unpack this archive: " + (result.stderr.strip() or result.stdout.strip() or "unknown error"))
        staged.mkdir()
        unpacked_resolved = unpacked.resolve()
        for relative, source in sources:
            incoming = unpacked / PurePosixPath(source)
            # We reviewed the member list already, but resolve it again here.
            # Archive formats have earned our paranoia.
            if not incoming.is_file() or not incoming.resolve().is_relative_to(unpacked_resolved):
                raise RuntimeError(f"archive changed since scan; reviewed member missing: {source}")
            target = staged / PurePosixPath(relative)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(incoming, target)
        return staged, [relative for relative, _ in sources]
    except Exception:
        shutil.rmtree(staging_name, ignore_errors=True)
        raise
