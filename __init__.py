"""Package Daddy: inspect explicitly packaged MO2 additions in Downloads."""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
from urllib.parse import urlparse
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath


# MO2 loads folder plugins straight from __init__.py and sometimes forgets to
# add the folder to sys.path. Give Daddy one local import root. No global-path
# bullshit, no hard-coded install path.
PLUGIN_DIR = Path(__file__).parent
PACKAGE_DADDY_ICON = PLUGIN_DIR / "icon.png"
NEXUS_DOWNLOAD_ICON = PLUGIN_DIR / "nexus_download.png"
NEXUS_PAGE_ICON = PLUGIN_DIR / "nexus_page.png"
DOWNLOADS_FOLDER_ICON = PLUGIN_DIR / "downloads_folder.png"
REFRESH_ICON = PLUGIN_DIR / "refresh.png"
UNINSTALL_ICON = PLUGIN_DIR / "uninstall.png"
DELETE_DOWNLOAD_ICON = PLUGIN_DIR / "delete_download.png"
CATALOG_FETCH_ICON = PLUGIN_DIR / "catalog_fetch.png"
NEXUS_BROWSE_ICON = PLUGIN_DIR / "nexus_browse.png"
INSTALLED_ICON = PLUGIN_DIR / "installed.png"
ARCHIVE_ICON = PLUGIN_DIR / "archive.png"
METADATA_ICON = PLUGIN_DIR / "metadata.png"
GITHUB_ICON = PLUGIN_DIR / "github.png"
DISCORD_ICON = PLUGIN_DIR / "discord.png"
WIKI_ICON = PLUGIN_DIR / "wiki.png"
INSTALL_ICON = PLUGIN_DIR / "install.png"
UPDATE_ICON = PLUGIN_DIR / "update.png"
REFRESH_WAIT_ICON = PLUGIN_DIR / "refresh_wait.png"
WAITING_PATIENTLY_GIF = PLUGIN_DIR / "waiting-patiently.gif"
NEXUS_DADDY_ICON = PLUGIN_DIR.parent / "nexus_daddy" / "icon.png"
if str(PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(PLUGIN_DIR))

import mobase
import daddy_paths
import plugin_daddy
import theme_daddy
import tool_daddy
from PyQt6.QtCore import QCoreApplication, QDateTime, QEventLoop, QSettings, QSize, QTimer, Qt, QUrl
from PyQt6.QtGui import QColor, QDesktopServices, QFont, QIcon, QMovie, QPainter, QPixmap, QTransform
from PyQt6.QtWidgets import (QAbstractItemView, QDialog, QHBoxLayout, QLabel,
                             QCheckBox, QComboBox, QDialogButtonBox, QLineEdit, QPushButton, QStyle, QTableWidget, QTableWidgetItem,
                             QVBoxLayout, QMessageBox, QWidget, QToolButton)


MANIFEST_NAME = "package_daddy.package.json"
CATALOG_NAME = "package_daddy_catalog.json"
REMOTE_CATALOG_NAME = "package_daddy_catalog.remote.json"
REMOTE_CATALOG_URL = "https://raw.githubusercontent.com/outtamercy/Package-Daddy-Catalog/main/catalog/package_daddy_catalog.json"
REMOTE_CATALOG_MAX_BYTES = 2 * 1024 * 1024
INSTALL_LEDGER_NAME = "package_daddy_installs.json"
WINDOW_STATE_NAME = "package_daddy_window.json"
SCAN_CACHE_NAME = "package_daddy_scan_cache.json"
NEXUS_LEADS_NAME = "package_daddy_nexus_leads.json"
ROOTDNA_MARKER = "root_dna.module.json"
ROOTDNA_COUPLING = "rootdna_coupling.json"
COUPLED_PACKAGE_IDS = frozenset(("file-watchdog", "root-dna"))
DECLARED_READY = "ready for install review"
CATALOG_READY = "approved catalog ZIP — ready for install review"
logger = logging.getLogger("PackageDaddy")
if not logger.handlers:
    handler = logging.FileHandler(PLUGIN_DIR / "package_daddy_debug.log", mode="w", encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)


@dataclass(frozen=True)
class PackageCandidate:
    archive: Path
    name: str
    package_id: str
    kind: str
    version: str
    status: str
    manifest_path: str = ""
    install_root: str = ""
    files: tuple[str, ...] = ()
    nexus_url: str = ""
    source_url: str = ""
    discord_url: str = ""
    wiki_url: str = ""
    nexus_file_id: int = 0
    archive_prefix: str = ""


@dataclass(frozen=True)
class ApprovedPackage:
    package_id: str
    name: str
    kind: str
    terms: tuple[str, ...]
    nexus_url: str = ""
    source_url: str = ""
    discord_url: str = ""
    wiki_url: str = ""
    compatibility: str = "unknown"
    release_date: str = ""
    release_version: str = ""
    compatibility_note: str = ""
    nexus_file_id: int = 0
    archive_aliases: tuple[tuple[str, ...], ...] = ()
    install_root: str = ""


def _version_key(value: object) -> tuple[int, ...]:
    """Compare MO2-ish versions without trusting arbitrary archive filenames."""
    pieces = re.findall(r"\d+", str(value))
    return tuple(int(piece) for piece in pieces) if pieces else ()


def _version_in_range(current: str, minimum: object, maximum: object) -> bool:
    current_key = _version_key(current)
    minimum_key = _version_key(minimum)
    maximum_key = _version_key(maximum)
    if not current_key:
        return False
    return (not minimum_key or current_key >= minimum_key) and (not maximum_key or current_key <= maximum_key)


def _select_release(record: dict, mo2_version: str) -> tuple[str, str, str, str]:
    """Return release version/date/compatibility/note for this MO2 host.

    Catalog release records deliberately mirror Plugin Finder's working/support
    distinction.  A release date is only a useful review clue; Min/MaxWorking
    is what decides whether Daddy calls it compatible.
    """
    compatibility = record.get("compatibility", "unknown")
    if compatibility == "known-incompatible":
        return "", "", compatibility, str(record.get("reason", "Known incompatible."))
    releases = record.get("releases", [])
    if not isinstance(releases, list) or not releases:
        return "", str(record.get("release_date", "")), str(compatibility), ""
    candidates = []
    for release in releases:
        if not isinstance(release, dict) or not isinstance(release.get("version"), str):
            continue
        if not _version_in_range(mo2_version, release.get("min_working", ""), release.get("max_working", "")):
            continue
        supported = _version_in_range(mo2_version, release.get("min_support", ""), release.get("max_support", ""))
        candidates.append((release, supported))
    if not candidates:
        return "", "", "known-incompatible", f"No catalogued release is known working on MO2 {mo2_version}."
    release, supported = max(candidates, key=lambda item: _version_key(item[0].get("version", "")))
    label = "verified-current" if supported else "working-not-supported"
    return str(release["version"]), str(release.get("released", "")), label, ""


def _safe_relative(value: str) -> bool:
    path = PurePosixPath(value.replace("\\", "/"))
    return bool(value) and not path.is_absolute() and ".." not in path.parts and ":" not in value


def _safe_install_root(value: object) -> bool:
    """A package may own one folder directly beneath MO2's plugins directory."""
    if not isinstance(value, str) or not value.strip() or not _safe_relative(value):
        return False
    return len(PurePosixPath(value.replace("\\", "/")).parts) == 1


def _safe_web_url(value: object) -> str:
    if not isinstance(value, str):
        return ""
    value = value.strip()
    return value if value.startswith(("https://", "http://")) else ""


def _nexus_target(value: str) -> tuple[str, int] | None:
    """Parse only ordinary Nexus mod-page URLs; never treat a link as code."""
    match = re.search(r"nexusmods\.com/(?:games/)?([a-z0-9_-]+)/mods/(\d+)", value or "", re.IGNORECASE)
    if match is None:
        return None
    try:
        return match.group(1).lower(), int(match.group(2))
    except ValueError:
        return None


def _github_target(value: str) -> tuple[str, str] | None:
    """Accept a normal public GitHub repository URL, not a random API URL."""
    match = re.search(r"github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)(?:/|$)", value or "")
    return (match.group(1), match.group(2).removesuffix(".git")) if match else None


def _github_latest_release(owner: str, repo: str) -> dict:
    """Read one public GitHub release through Qt, with no token or scraper."""
    from PyQt6.QtCore import QEventLoop
    from PyQt6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
    manager = QNetworkAccessManager()
    request = QNetworkRequest(QUrl(f"https://api.github.com/repos/{owner}/{repo}/releases/latest"))
    request.setRawHeader(b"User-Agent", b"Package-Daddy-MO2/0.3")
    request.setRawHeader(b"Accept", b"application/vnd.github+json")
    reply = manager.get(request)
    loop = QEventLoop()
    timeout = QTimer(); timeout.setSingleShot(True)
    timeout.timeout.connect(loop.quit); reply.finished.connect(loop.quit)
    timeout.start(12000); loop.exec()
    if not reply.isFinished():
        reply.abort()
        raise RuntimeError("GitHub request timed out after 12 seconds")
    if reply.error() != QNetworkReply.NetworkError.NoError:
        raise RuntimeError(reply.errorString())
    payload = json.loads(bytes(reply.readAll()).decode("utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError("GitHub returned an invalid release response")
    return payload


def _manifest_member(names: list[str]) -> str | None:
    """Allow the manifest at archive root or under one wrapper folder."""
    matches = []
    for name in names:
        parts = PurePosixPath(name).parts
        if parts and parts[-1].lower() == MANIFEST_NAME and len(parts) <= 2:
            matches.append(name)
    return matches[0] if len(matches) == 1 else None


def _catalog_path() -> Path:
    return PLUGIN_DIR / CATALOG_NAME


def _remote_catalog_path() -> Path:
    return PLUGIN_DIR / REMOTE_CATALOG_NAME


def _read_catalog_records(path: Path, *, remote: bool) -> list[dict]:
    """Read one catalog only if it has the expected, boring JSON shape."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict) or not isinstance(raw.get("packages"), list):
            raise ValueError("missing packages list")
        # The bundled fallback predates the public catalog. A downloaded file
        # must be format 2; random JSON doesn't get to declare itself boss.
        if remote and raw.get("format") != 2:
            raise ValueError("public catalog is not format 2")
        return [record for record in raw["packages"] if isinstance(record, dict)]
    except (OSError, ValueError, TypeError, UnicodeDecodeError) as exc:
        logger.warning("Couldn't load %s catalog %s: %s", "public" if remote else "bundled", path, exc)
        return []


def _refresh_remote_catalog() -> int:
    """Fetch the one pinned public catalog on explicit user request.

    This never executes or extracts anything. It accepts only a small format-2
    JSON document, then replaces the cache atomically so the bundled catalog is
    always available if GitHub is offline or the update is malformed.
    """
    try:
        # MO2 runs its own embedded Python, and some builds are missing urllib
        # HTTPS even though Qt works fine. Use MO2's network stack; users
        # shouldn't have to debug our weird runtime at 2am.
        from PyQt6.QtCore import QEventLoop
        from PyQt6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest

        manager = QNetworkAccessManager()
        request = QNetworkRequest(QUrl(REMOTE_CATALOG_URL))
        request.setRawHeader(b"User-Agent", b"Package-Daddy-MO2/0.3")
        reply = manager.get(request)
        loop = QEventLoop()
        timeout = QTimer()
        timeout.setSingleShot(True)
        timeout.timeout.connect(loop.quit)
        reply.finished.connect(loop.quit)
        timeout.start(12000)
        loop.exec()
        if not reply.isFinished():
            reply.abort()
            raise RuntimeError("catalog request timed out after 12 seconds")
        if reply.error() != QNetworkReply.NetworkError.NoError:
            raise RuntimeError(reply.errorString())
        payload = bytes(reply.readAll())
        if len(payload) > REMOTE_CATALOG_MAX_BYTES:
            raise ValueError("catalog is larger than the 2 MiB safety limit")
        raw = json.loads(payload.decode("utf-8"))
        if not isinstance(raw, dict) or raw.get("format") != 2 or not isinstance(raw.get("packages"), list):
            raise ValueError("public catalog is not a format-2 package catalog")
        destination = _remote_catalog_path()
        descriptor, temporary = tempfile.mkstemp(prefix="package_daddy_catalog_", suffix=".json", dir=destination.parent)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(payload)
            os.replace(temporary, destination)
        except OSError:
            try:
                os.unlink(temporary)
            except OSError:
                pass
            raise
        return len(raw["packages"])
    except (ImportError, OSError, RuntimeError, ValueError, UnicodeDecodeError) as exc:
        raise RuntimeError(f"could not refresh the public catalog: {exc}") from exc


def _ledger_path() -> Path:
    return PLUGIN_DIR / INSTALL_LEDGER_NAME


def _read_ledger(*, strict: bool = False) -> dict[str, dict]:
    """Read Daddy's own receipts.  A malformed receipt is never a deletion permit."""
    path = _ledger_path()
    try:
        raw = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"format": 1, "packages": {}}
        packages = raw.get("packages") if isinstance(raw, dict) else None
        if raw.get("format") != 1 or not isinstance(packages, dict):
            raise ValueError("ledger has an invalid shape")
        return {key: value for key, value in packages.items() if isinstance(key, str) and isinstance(value, dict)}
    except (OSError, ValueError, TypeError) as exc:
        logger.warning("Couldn't read Package Daddy's install ledger: %s", exc)
        if strict:
            raise RuntimeError(f"won't replace an unreadable install ledger: {exc}") from exc
        return {}


def _write_ledger(ledger: dict[str, dict]) -> None:
    path = _ledger_path()
    descriptor, temp_name = tempfile.mkstemp(prefix="package_daddy_", suffix=".json", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            json.dump({"format": 1, "packages": ledger}, stream, indent=2, sort_keys=True)
            stream.write("\n")
        os.replace(temp_name, path)
    except OSError:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def _write_ledger_entry(candidate: PackageCandidate, archive_names: list[str]) -> None:
    """Record only a completed install; write atomically so a broken receipt is never trusted."""
    current = _read_ledger(strict=True)
    current[candidate.package_id] = {
        "name": candidate.name,
        "version": candidate.version,
        "archive": candidate.archive.name,
        "installed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "install_root": candidate.install_root,
        "files": archive_names,
        "target": "tools" if tool_daddy.accepts(candidate.kind) else "plugins",
    }
    _write_ledger(current)


def _load_catalog(mo2_version: str) -> list[ApprovedPackage]:
    """Catalog entries are identification hints, never permission to install."""
    bundled = _read_catalog_records(_catalog_path(), remote=False)
    public = _read_catalog_records(_remote_catalog_path(), remote=True) if _remote_catalog_path().is_file() else []
    # Public records beat bundled records with the same stable ID. Bundled-only
    # stuff stays useful when the catalog is growing or the internet eats it.
    by_id = {record.get("id"): record for record in bundled if isinstance(record.get("id"), str)}
    for record in public:
        package_id = record.get("id")
        if isinstance(package_id, str):
            # Public catalog data wins where it actually says something. Don't
            # let an older skinny public row wipe out a real local source/Nexus
            # link just because the catalog hadn't caught up yet. That was
            # making the damn thing look like an outtamercy showroom.
            fallback = by_id.get(package_id, {})
            merged = dict(fallback) if isinstance(fallback, dict) else {}
            merged.update(record)
            if isinstance(fallback, dict):
                for field in ("install_root",):
                    if fallback.get(field):
                        merged[field] = fallback[field]
            by_id[package_id] = merged
    records = list(by_id.values())
    approved: list[ApprovedPackage] = []
    for record in records:
        if not isinstance(record, dict):
            continue
        package_id = record.get("id")
        name = record.get("name")
        kind = record.get("kind")
        terms = record.get("archive_terms")
        aliases = record.get("archive_aliases", [])
        install_root = record.get("install_root", "")
        nexus_url = _safe_web_url(record.get("nexus_url"))
        source_url = _safe_web_url(record.get("source_url"))
        discord_url = _safe_web_url(record.get("discord_url"))
        wiki_url = _safe_web_url(record.get("wiki_url"))
        pinned_file_id = record.get("nexus_file_id", 0)
        if not isinstance(pinned_file_id, int) or isinstance(pinned_file_id, bool) or pinned_file_id < 1:
            pinned_file_id = 0
        if (
            not isinstance(package_id, str)
            or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{1,63}", package_id)
            or not isinstance(name, str)
            or not name.strip()
            or kind not in {"mo2-plugin", "mo2-theme", "mo2-bridge", "mo2-tool", "mo2-mod"}
            or not isinstance(terms, list)
            or not terms
            or not all(isinstance(term, str) and term.strip() for term in terms)
            or not isinstance(aliases, list)
            or not all(isinstance(alias, list) and alias and all(isinstance(term, str) and term.strip() for term in alias) for alias in aliases)
            or (install_root and not _safe_install_root(install_root))
        ):
            logger.warning("Skipping busted approved catalog entry: %r", record)
            continue
        release_version, release_date, compatibility, compatibility_note = _select_release(record, mo2_version)
        approved.append(ApprovedPackage(
            package_id, name.strip(), kind, tuple(_normalize(term) for term in terms),
            nexus_url, source_url, discord_url, wiki_url,
            compatibility, release_date, release_version, compatibility_note, pinned_file_id,
            tuple(tuple(_normalize(term) for term in alias) for alias in aliases),
            install_root,
        ))
    return approved


def _normalize(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def _archive_label(archive: Path) -> str:
    """Give Daddy the useful bits MO2 shows, not just a sad naked filename."""
    try:
        facts = archive.stat()
    except OSError:
        return archive.name
    size = facts.st_size
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            size_text = f"{size:.0f} {unit}" if unit == "B" else f"{size:.2f} {unit}"
            break
        size /= 1024
    stamp = datetime.fromtimestamp(facts.st_mtime).strftime("%m/%d/%Y %I:%M %p")
    return f"{archive.name}\n{size_text}  •  {stamp}"


def _matches_catalog_terms(entry: ApprovedPackage, value: str) -> bool:
    """A record can keep its normal name plus honest alternate archive names."""
    sets = (entry.terms, *entry.archive_aliases)
    return any(all(term in value for term in term_set) for term_set in sets)


def _catalog_match_specificity(entry: ApprovedPackage, value: str) -> int:
    """Prefer a specific package name over a broad one. 'Resources' beats plain DynDOLOD."""
    matched = [len(term_set) for term_set in (entry.terms, *entry.archive_aliases) if all(term in value for term in term_set)]
    return max(matched, default=0)


def _compatibility_label(entry: ApprovedPackage | None) -> tuple[str, str]:
    """Human label and tooltip for the catalog's independent compatibility fact."""
    if entry is None:
        return "Unknown", "No catalog compatibility record for this package."
    date_note = f" Latest catalogued release: {entry.release_version} ({entry.release_date})." if entry.release_version else ""
    if entry.compatibility == "verified-current":
        return "Supported", "Catalogued release is working and supported on this MO2 version." + date_note
    if entry.compatibility == "working-not-supported":
        return "Works; unsupported", "Catalogued release is known working, but its author does not support this MO2 version." + date_note
    if entry.compatibility == "likely-current":
        return "Likely current", "Release date is recent, but this is not an actual compatibility verification." + date_note
    if entry.compatibility == "known-incompatible":
        return "Not compatible", entry.compatibility_note or "No catalogued release is known working on this MO2 version."
    return "Unknown", "No working/support range has been catalogued yet."


def _catalog_candidate(archive: Path, approved: list[ApprovedPackage]) -> PackageCandidate | None:
    """Match explicit catalog terms only. Archive contents do not make a package approved."""
    archive_name = _normalize(archive.stem)
    matches = [entry for entry in approved if _matches_catalog_terms(entry, archive_name)]
    if not matches:
        return None
    strongest = max(_catalog_match_specificity(entry, archive_name) for entry in matches)
    matches = [entry for entry in matches if _catalog_match_specificity(entry, archive_name) == strongest]
    if len(matches) != 1:
        return None
    entry = matches[0]
    if entry.compatibility == "known-incompatible":
        state = "incompatible: " + (entry.compatibility_note or "catalog marks this package incompatible")
    elif entry.compatibility == "working-not-supported":
        state = "working, but not supported on this MO2 version — inspect before install"
    elif entry.compatibility == "likely-current":
        state = "likely current by release date — inspect before install"
    elif entry.compatibility == "verified-current":
        state = "verified current — inspect before install"
    else:
        state = "approved catalog match — compatibility not catalogued; inspect before install"
    return PackageCandidate(
        archive,
        entry.name,
        entry.package_id,
        entry.kind,
        entry.release_version or "—",
        state,
        nexus_url=entry.nexus_url,
        source_url=entry.source_url,
        discord_url=entry.discord_url,
        wiki_url=entry.wiki_url,
        nexus_file_id=entry.nexus_file_id,
        install_root=entry.install_root,
    )


def _installed_catalog_ids(plugins_root: Path, approved: list[ApprovedPackage], tools_root: Path | None = None) -> set[str]:
    """Recognize installed packages locally; never treat a download as installable twice."""
    try:
        installed_names = [_normalize(item.name) for item in plugins_root.iterdir()]
    except OSError as exc:
        logger.warning("Couldn't inspect MO2's plugins folder %s: %s", plugins_root, exc)
        return set()
    installed = {
        entry.package_id
        for entry in approved
        if not tool_daddy.accepts(entry.kind)
        if any(_matches_catalog_terms(entry, name) for name in installed_names)
    }
    if tools_root is None:
        return installed
    try:
        tool_names = [_normalize(item.name) for item in tools_root.iterdir() if item.is_dir()]
    except OSError:
        tool_names = []
    installed.update(
        entry.package_id
        for entry in approved
        if tool_daddy.accepts(entry.kind)
        and any(_matches_catalog_terms(entry, name) for name in tool_names)
    )
    return installed


def _catalog_zip_install_candidate(candidate: PackageCandidate) -> PackageCandidate:
    """Turn one *known* catalog ZIP into an install candidate after a structural review.

    The catalog identifies a package; this check still refuses anything except a
    single, ordinary plugin folder with a Python entry point.  We never extract
    it here and never grant that privilege to an unknown archive.
    """
    # Compatibility came from the curated release record before we opened the
    # archive. A tidy ZIP can't magically make a known-bad host combo safe.
    if candidate.status.startswith("incompatible:"):
        return candidate
    if theme_daddy.accepts(candidate.kind):
        try:
            if theme_daddy.reviewable(candidate.archive):
                return PackageCandidate(
                    candidate.archive, candidate.name, candidate.package_id, candidate.kind,
                    candidate.version, theme_daddy.READY,
                    nexus_url=candidate.nexus_url, source_url=candidate.source_url,
                    discord_url=candidate.discord_url, wiki_url=candidate.wiki_url,
                    nexus_file_id=candidate.nexus_file_id,
                )
        except (OSError, zipfile.BadZipFile) as exc:
            logger.warning("Couldn't review approved theme ZIP %s: %s", candidate.archive, exc)
        return candidate
    try:
        with zipfile.ZipFile(candidate.archive) as bundle:
            files = [name for name in bundle.namelist() if not name.endswith("/")]
            pyqt5_sources = []
            for name in files:
                if name.lower().endswith(".py"):
                    # Tiny compatibility sniff, not code execution. MO2 is on
                    # PyQt6 now; old PyQt5 plugins crash the party.
                    source = bundle.read(name)[:2 * 1024 * 1024]
                    if b"PyQt5" in source:
                        pyqt5_sources.append(name)
    except (OSError, zipfile.BadZipFile) as exc:
        logger.warning("Couldn't review approved catalog ZIP %s: %s", candidate.archive, exc)
        return candidate
    if not files or any(not _safe_relative(name) for name in files):
        return candidate
    roots = {PurePosixPath(name).parts[0] for name in files if PurePosixPath(name).parts}
    if len(roots) != 1:
        return candidate
    install_root = next(iter(roots))
    if not _safe_install_root(install_root):
        return candidate
    relative_files = tuple(str(PurePosixPath(name).relative_to(install_root)) for name in files)
    if any(not item or not _safe_relative(item) for item in relative_files):
        return candidate
    if tool_daddy.accepts(candidate.kind):
        # A catalogued tool only gets through when it lives under one wrapper
        # folder and has something runnable. No loose EXEs sprayed through MO2.
        runnable = any(item.lower().endswith((".exe", ".bat", ".cmd")) for item in relative_files)
        if not runnable or not tool_daddy.safe_file_list(relative_files):
            return candidate
        return PackageCandidate(
            candidate.archive, candidate.name, candidate.package_id, candidate.kind,
            candidate.version, CATALOG_READY, "", install_root, relative_files,
            candidate.nexus_url, candidate.source_url, candidate.discord_url, candidate.wiki_url,
            candidate.nexus_file_id,
        )
    # This stays narrow: reviewed MO2 Python plugins only, not every archive
    # that happens to share a catalog name.
    if not plugin_daddy.structurally_reviewed(relative_files):
        return candidate
    if pyqt5_sources:
        return PackageCandidate(
            candidate.archive, candidate.name, candidate.package_id, candidate.kind,
            candidate.version, "incompatible: archive imports PyQt5 (MO2 uses PyQt6)", "", install_root, relative_files,
            candidate.nexus_url, candidate.source_url, candidate.discord_url, candidate.wiki_url,
            candidate.nexus_file_id,
        )
    return PackageCandidate(
        candidate.archive, candidate.name, candidate.package_id, candidate.kind,
        candidate.version, CATALOG_READY, "", install_root, relative_files,
        candidate.nexus_url, candidate.source_url, candidate.discord_url, candidate.wiki_url,
        candidate.nexus_file_id,
    )


def _catalog_tool_archive_candidate(candidate: PackageCandidate, mo2_root: Path) -> PackageCandidate:
    """Review a catalogued tool ZIP/7Z without giving it free rein over MO2.

    Tools sometimes ship as one wrapper folder and sometimes (ReSaver is the
    classic example) put their runnable files at archive root.  The catalog
    must name the owned destination for the latter; we do not guess.
    """
    if not tool_daddy.accepts(candidate.kind) or candidate.status.startswith("incompatible:"):
        return candidate
    try:
        files = tuple(name.replace("\\", "/") for name in tool_daddy.archive_files(candidate.archive, mo2_root))
    except (OSError, RuntimeError, zipfile.BadZipFile) as exc:
        logger.warning("Couldn't review catalogued tool archive %s: %s", candidate.archive, exc)
        return candidate
    if not files or not tool_daddy.safe_file_list(files):
        return candidate
    roots = {PurePosixPath(name).parts[0] for name in files if PurePosixPath(name).parts}
    prefix = ""
    install_root = candidate.install_root
    if len(roots) == 1 and _safe_install_root(next(iter(roots))):
        wrapper = next(iter(roots))
        relative_files = tuple(str(PurePosixPath(name).relative_to(wrapper)) for name in files)
        # Root-level payloads like ReSaver need a curator-provided folder name.
        # A real wrapper folder can own itself.
        if not install_root:
            install_root = wrapper
        prefix = wrapper + "/"
    elif install_root and _safe_install_root(install_root):
        relative_files = files
    else:
        return candidate
    if not relative_files or any(not item or not _safe_relative(item) for item in relative_files):
        return candidate
    if not any(item.lower().endswith((".exe", ".bat", ".cmd")) for item in relative_files):
        return candidate
    return PackageCandidate(
        candidate.archive, candidate.name, candidate.package_id, candidate.kind,
        candidate.version, CATALOG_READY, "", install_root, relative_files,
        candidate.nexus_url, candidate.source_url, candidate.discord_url, candidate.wiki_url,
        candidate.nexus_file_id, prefix,
    )


def _write_theme_ledger_entry(candidate: PackageCandidate, files: tuple[str, ...], hashes: dict[str, str]) -> None:
    """Record the exact stylesheet files Daddy created, including their hashes."""
    current = _read_ledger(strict=True)
    current[candidate.package_id] = {
        "name": candidate.name,
        "version": candidate.version,
        "archive": candidate.archive.name,
        "installed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "target": "stylesheets",
        "files": list(files),
        "hashes": hashes,
    }
    _write_ledger(current)


def _parse_package(archive: Path, approved: list[ApprovedPackage]) -> PackageCandidate | None:
    """Read ZIP names plus a tiny manifest. Never extract or execute archive code."""
    try:
        with zipfile.ZipFile(archive) as bundle:
            member = _manifest_member(bundle.namelist())
            if member is None:
                candidate = _catalog_candidate(archive, approved)
                return _catalog_zip_install_candidate(candidate) if candidate is not None else None
            raw = json.loads(bundle.read(member).decode("utf-8"))
            package_id = raw.get("id", "")
            name = raw.get("name", "")
            kind = raw.get("kind", "")
            version = raw.get("version", "")
            install_root = raw.get("install_root", "")
            files = raw.get("files")
            nexus_url = _safe_web_url(raw.get("nexus_url"))
            source_url = _safe_web_url(raw.get("source_url"))
            discord_url = _safe_web_url(raw.get("discord_url"))
            wiki_url = _safe_web_url(raw.get("wiki_url"))
            if raw.get("format") != 2:
                status = "unsupported manifest format"
            elif not re.fullmatch(r"[a-z0-9][a-z0-9_-]{1,63}", package_id):
                status = "invalid package id"
            elif not isinstance(name, str) or not name.strip() or kind not in {"mo2-plugin", "mo2-theme", "mo2-bridge", "mo2-tool"}:
                status = "invalid package metadata"
            elif not isinstance(version, str) or not version.strip():
                status = "missing version"
            elif not _safe_install_root(install_root):
                status = "unsafe or missing install root"
            elif not isinstance(files, list) or not files or not all(isinstance(item, str) and _safe_relative(item) for item in files):
                status = "unsafe or missing file list"
            elif len(set(files)) != len(files):
                status = "duplicate file in manifest"
            else:
                wrapper = str(PurePosixPath(member).parent)
                prefix = "" if wrapper == "." else wrapper + "/"
                archive_names = set(bundle.namelist())
                missing = [item for item in files if prefix + item not in archive_names]
                status = DECLARED_READY if not missing else "declared file missing: " + missing[0]
            return PackageCandidate(
                archive, str(name), str(package_id), str(kind), str(version), status,
                member, str(install_root), tuple(str(item) for item in files) if isinstance(files, list) else (), nexus_url, source_url, discord_url, wiki_url,
            )
    except (OSError, zipfile.BadZipFile, UnicodeDecodeError, ValueError, TypeError) as exc:
        logger.warning("Couldn't inspect %s: %s", archive, exc)
        return None


class PackageDaddyWindow(QDialog):
    HEADERS = ("Package", "Kind", "Compatibility", "Version", "Archive", "State / action")

    def __init__(self, downloads: Path, mo2_root: Path, mo2_version: str, organizer, ini_path: Path | None = None):
        super().__init__()
        self.downloads = downloads
        # The running MO2 exe is the truth for portable and global installs.
        # Don't infer install roots from somebody's Downloads path. Ever.
        self.mo2_root = mo2_root
        self.plugins_root = self.mo2_root / "plugins"
        self.ini_path = ini_path
        self.mo2_version = mo2_version
        self.organizer = organizer
        self._catalog_notice = ""
        # Only fill this when the user asks for updates. Pin results to the
        # actual package row; don't fling a separate report window at them.
        self._update_results: dict[str, dict[str, str]] = {}
        self._candidates: list[PackageCandidate] = []
        self._refresh_rotation = 0
        self._refresh_source_pixmap = QPixmap(str(REFRESH_ICON)).scaled(
            22, 22, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
        )
        self._refresh_notice: QDialog | None = None
        self._refresh_notice_text: QLabel | None = None
        self._refresh_notice_movie: QMovie | None = None
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setInterval(90)
        self._refresh_timer.timeout.connect(self._advance_refresh_icon)
        self.setWindowTitle("Package Daddy — Downloads Package Manager")
        self.resize(1200, 620)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Packages misbehaving in Downloads? Daddy puts them where they belong."))
        layout.addWidget(QLabel("Local Daddy: declared packages and structurally reviewed approved-catalog ZIPs can install. Public Catalog Daddy updates only when you ask it to."))
        filter_row = QHBoxLayout()
        filter_row.addWidget(QLabel("Find:"))
        self.search_box = QLineEdit(self)
        self.search_box.setPlaceholderText("Search package, archive, version, or compatibility…")
        self.search_box.setClearButtonEnabled(True)
        self.search_box.setToolTip("Filter the visible list as you type. Nothing is hidden permanently; Daddy is just helping you find the damn thing.")
        filter_row.addWidget(self.search_box, 1)
        filter_row.addWidget(QLabel("Show:"))
        self.kind_filter = QComboBox(self)
        self.kind_filter.addItem("All packages", "")
        self.kind_filter.addItem("Plugins", "mo2-plugin")
        self.kind_filter.addItem("Themes", "mo2-theme")
        self.kind_filter.addItem("Tools", "mo2-tool")
        self.kind_filter.addItem("Mods (download only)", "mo2-mod")
        self.kind_filter.setToolTip("Narrow the list by package type.")
        filter_row.addWidget(self.kind_filter)
        layout.addLayout(filter_row)
        self.table = QTableWidget(0, len(self.HEADERS), self)
        self.table.setHorizontalHeaderLabels(self.HEADERS)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setColumnWidth(0, 260)
        self.table.setColumnWidth(1, 150)
        self.table.setColumnWidth(2, 135)
        self.table.setColumnWidth(3, 90)
        self.table.setColumnWidth(4, 330)
        self.table.setColumnWidth(5, 238)
        self.table.horizontalHeader().setSectionsClickable(True)
        self.table.horizontalHeader().setSortIndicatorShown(True)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.verticalHeader().setDefaultSectionSize(42)
        layout.addWidget(self.table)
        self.search_box.textChanged.connect(self._apply_filters)
        self.kind_filter.currentIndexChanged.connect(self._apply_filters)
        self.status = QLabel("Scan MO2 Downloads to find packages.", self)
        self.status.setWordWrap(True)
        # This is the plain-English receipt for scans/downloads. It needs to be
        # readable without pressing your face against the damn monitor.
        status_font = QFont(self.status.font())
        status_font.setPointSize(max(status_font.pointSize() + 2, 11))
        self.status.setFont(status_font)
        layout.addWidget(self.status)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        refresh_catalog = self._action_button(
            QIcon(str(CATALOG_FETCH_ICON)),
            "Fetch the public Package Daddy catalog from GitHub, validate it, then rescan. No packages are downloaded or installed.",
            handler=self.refresh_public_catalog,
            large=True,
        )
        buttons.addWidget(refresh_catalog)
        open_downloads = self._action_button(
            QIcon(str(DOWNLOADS_FOLDER_ICON)),
            "Open MO2's Downloads folder in Windows Explorer.",
            handler=lambda _checked=False: QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.downloads))),
            large=True,
        )
        buttons.addWidget(open_downloads)
        browse_nexus = self._action_button(
            QIcon(str(NEXUS_BROWSE_ICON)),
            "Browse the public Nexus MO2 Plugins/Themes feed. This is a no-key, site-only discovery list; cross-game entries still come from Daddy's reviewed catalog.",
            handler=self.browse_public_nexus,
            large=True,
        )
        buttons.addWidget(browse_nexus)
        check_updates = QPushButton("Check updates", self)
        check_updates.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_BrowserReload))
        check_updates.setToolTip("Check current public Nexus and GitHub versions for catalogued packages. No API key, download, or install needed.")
        check_updates.clicked.connect(self.check_nexus_updates)
        buttons.addWidget(check_updates)
        refresh = self._action_button(
            QIcon(str(REFRESH_ICON)),
            "Scan MO2 Downloads again using the bundled catalog and any cached public catalog. This remains read-only.",
            handler=self.scan_with_refresh,
        )
        self._refresh_button = refresh
        self._refresh_needs_scan = True
        self._set_refresh_needed(True)
        buttons.addWidget(refresh)
        close = QPushButton("Close", self)
        close.clicked.connect(self.accept)
        buttons.addWidget(close)
        layout.addLayout(buttons)
        self._restore_window_state()
        self.finished.connect(self._save_window_state)
        self.scan()

    def _window_state_path(self) -> Path:
        """Keep UI preferences with MO2's plugin data, not inside the mod."""
        try:
            return Path(self.organizer.pluginDataPath()) / "package_daddy" / WINDOW_STATE_NAME
        except Exception:
            return PLUGIN_DIR / WINDOW_STATE_NAME

    def _nexus_leads_path(self) -> Path:
        """Keep discovered pages with the MO2 instance, not in a mod archive."""
        try:
            return Path(self.organizer.pluginDataPath()) / "package_daddy" / NEXUS_LEADS_NAME
        except Exception:
            return PLUGIN_DIR / NEXUS_LEADS_NAME

    def _scan_cache_path(self) -> Path:
        """Keep one disposable scan cache beside this MO2 instance's UI state."""
        try:
            return Path(self.organizer.pluginDataPath()) / "package_daddy" / SCAN_CACHE_NAME
        except Exception:
            return PLUGIN_DIR / SCAN_CACHE_NAME

    @staticmethod
    def _file_stamp(path: Path) -> list[object]:
        try:
            stat = path.stat()
            return [path.name, stat.st_size, stat.st_mtime_ns]
        except OSError:
            return [path.name, -1, -1]

    def _scan_fingerprint(self, archives: list[Path]) -> dict[str, object]:
        return {
            "format": 1,
            "mo2_version": self.mo2_version,
            "downloads": str(self.downloads.resolve()),
            "archives": [self._file_stamp(archive) for archive in archives],
            "inputs": [
                self._file_stamp(_catalog_path()),
                self._file_stamp(_remote_catalog_path()),
                self._file_stamp(_ledger_path()),
                self._file_stamp(self._nexus_leads_path()),
            ],
        }

    def _quick_scan_fingerprint(self) -> dict[str, object]:
        """Cheap change check. Don't inspect five thousand archives just to open."""
        return {
            "format": 1,
            "mo2_version": self.mo2_version,
            "downloads": str(self.downloads.resolve()),
            "downloads_folder": self._file_stamp(self.downloads),
            "inputs": [
                self._file_stamp(_catalog_path()),
                self._file_stamp(_remote_catalog_path()),
                self._file_stamp(_ledger_path()),
                self._file_stamp(self._nexus_leads_path()),
            ],
        }

    @staticmethod
    def _candidate_cache_record(candidate: PackageCandidate) -> dict[str, object]:
        return {
            "archive": str(candidate.archive), "name": candidate.name,
            "package_id": candidate.package_id, "kind": candidate.kind,
            "version": candidate.version, "status": candidate.status,
            "manifest_path": candidate.manifest_path, "install_root": candidate.install_root,
            "files": list(candidate.files), "nexus_url": candidate.nexus_url,
            "source_url": candidate.source_url, "discord_url": candidate.discord_url,
            "wiki_url": candidate.wiki_url, "nexus_file_id": candidate.nexus_file_id,
            "archive_prefix": candidate.archive_prefix,
        }

    def _candidate_from_cache_record(self, record: object) -> PackageCandidate | None:
        if not isinstance(record, dict):
            return None
        try:
            archive = Path(str(record["archive"])).resolve()
            archive.relative_to(self.downloads.resolve())
            kind = str(record["kind"])
            if kind not in {"mo2-plugin", "mo2-bridge", "mo2-theme", "mo2-tool", "mo2-mod"}:
                return None
            files = record.get("files", [])
            if not isinstance(files, list) or not all(isinstance(item, str) for item in files):
                return None
            return PackageCandidate(
                archive=archive, name=str(record["name"]), package_id=str(record["package_id"]),
                kind=kind, version=str(record["version"]), status=str(record["status"]),
                manifest_path=str(record.get("manifest_path") or ""), install_root=str(record.get("install_root") or ""),
                files=tuple(files), nexus_url=_safe_web_url(record.get("nexus_url")),
                source_url=_safe_web_url(record.get("source_url")), discord_url=_safe_web_url(record.get("discord_url")),
                wiki_url=_safe_web_url(record.get("wiki_url")), nexus_file_id=int(record.get("nexus_file_id") or 0),
                archive_prefix=str(record.get("archive_prefix") or ""),
            )
        except (KeyError, OSError, TypeError, ValueError):
            return None

    def _read_scan_cache(self, quick_fingerprint: dict[str, object]) -> tuple[list[PackageCandidate], dict[str, int], bool] | None:
        try:
            raw = json.loads(self._scan_cache_path().read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                return None
            records = raw.get("candidates")
            if not isinstance(records, list):
                return None
            candidates = [self._candidate_from_cache_record(record) for record in records]
            stats = raw.get("stats")
            if not isinstance(stats, dict):
                stats = {}
            numeric_stats = {key: int(value) for key, value in stats.items() if isinstance(value, int) and value >= 0}
            if candidates and all(candidate is not None for candidate in candidates):
                return candidates, numeric_stats, raw.get("quick_fingerprint") == quick_fingerprint
            return None
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return None

    def _write_scan_cache(self, fingerprint: dict[str, object], quick_fingerprint: dict[str, object], candidates: list[PackageCandidate], stats: dict[str, int]) -> None:
        path = self._scan_cache_path()
        payload = {
            "format": 2, "fingerprint": fingerprint, "quick_fingerprint": quick_fingerprint,
            "stats": stats, "candidates": [self._candidate_cache_record(candidate) for candidate in candidates],
        }
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix(".new")
            temporary.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            os.replace(temporary, path)
        except OSError as exc:
            logger.debug("Couldn't save scan cache: %s", exc)

    def _read_nexus_leads(self) -> list[dict]:
        try:
            raw = json.loads(self._nexus_leads_path().read_text(encoding="utf-8"))
            leads = raw.get("leads") if isinstance(raw, dict) else None
            return [lead for lead in leads if isinstance(lead, dict)] if isinstance(leads, list) else []
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return []

    def _write_nexus_leads(self, leads: list[dict]) -> None:
        path = self._nexus_leads_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix="package_daddy_nexus_", suffix=".json", dir=path.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
                json.dump({"format": 1, "leads": leads}, stream, indent=2, sort_keys=True)
                stream.write("\n")
            os.replace(temporary, path)
        except OSError:
            try:
                os.unlink(temporary)
            except OSError:
                pass
            raise

    def _remember_nexus_lead(self, mod: dict) -> None:
        """Queue a public browse result for catalog review, never for auto-install."""
        try:
            mod_id = int(mod.get("modId"))
        except (TypeError, ValueError):
            return
        category = str((mod.get("modCategory") or {}).get("name") or "")
        kind = "mo2-theme" if category == "Mod Organizer 2 Themes" else "mo2-plugin"
        lead = {
            "id": f"nexus-site-{mod_id}",
            "name": str(mod.get("name") or f"Nexus site mod {mod_id}").strip(),
            "kind": kind,
            "nexus_url": f"https://www.nexusmods.com/site/mods/{mod_id}",
            "author": str((mod.get("uploader") or {}).get("name") or "").strip(),
            "updated": str(mod.get("updatedAt") or "").strip(),
        }
        if not lead["name"]:
            return
        leads = [item for item in self._read_nexus_leads() if item.get("id") != lead["id"]]
        leads.append(lead)
        self._write_nexus_leads(leads)
        logger.info("Saved Nexus lead %s (%s).", lead["name"], lead["id"])

    def _restore_window_state(self) -> None:
        try:
            saved = json.loads(self._window_state_path().read_text(encoding="utf-8"))
            width, height = int(saved.get("width")), int(saved.get("height"))
            if 760 <= width <= 3840 and 480 <= height <= 2160:
                self.resize(width, height)
            widths = saved.get("columns")
            if isinstance(widths, list) and len(widths) == self.table.columnCount():
                for column, width in enumerate(widths):
                    if isinstance(width, int) and width >= 40:
                        self.table.setColumnWidth(column, width)
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            pass

    def _save_window_state(self, *_args) -> None:
        path = self._window_state_path()
        payload = {
            "width": self.width(),
            "height": self.height(),
            "columns": [self.table.columnWidth(column) for column in range(self.table.columnCount())],
        }
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix(".new")
            temporary.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            os.replace(temporary, path)
        except OSError as exc:
            logger.warning("Couldn't save Package Daddy window state: %s", exc)

    def _apply_filters(self, *_args) -> None:
        """Hide only rows that miss the current search/type view; scan data stays put."""
        needle = self.search_box.text().strip().casefold()
        wanted_kind = str(self.kind_filter.currentData() or "")
        for row in range(self.table.rowCount()):
            values = [self.table.item(row, column).text() if self.table.item(row, column) is not None else "" for column in range(5)]
            kind = values[1]
            is_plugin = kind in {"mo2-plugin", "mo2-bridge"}
            kind_match = not wanted_kind or kind == wanted_kind or (wanted_kind == "mo2-plugin" and is_plugin)
            haystack = " ".join((
                *values,
                self.table.cellWidget(row, 5).toolTip() if self.table.cellWidget(row, 5) is not None else "",
            )).casefold()
            self.table.setRowHidden(row, not (kind_match and (not needle or needle in haystack)))

    def _advance_refresh_icon(self) -> None:
        """Give the manual scan control a little life while Daddy is working."""
        self._refresh_rotation = (self._refresh_rotation + 30) % 360
        rotated = self._refresh_source_pixmap.transformed(
            QTransform().rotate(self._refresh_rotation), Qt.TransformationMode.SmoothTransformation
        )
        self._refresh_button.setIcon(QIcon(rotated))

    def _refresh_icon(self, needs_refresh: bool) -> QIcon:
        pixmap = QPixmap(str(REFRESH_ICON)).scaled(
            28, 28, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
        )
        if needs_refresh:
            painter = QPainter(pixmap)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setBrush(QColor("#e53935"))
            painter.setPen(QColor("#fff3e0"))
            painter.drawEllipse(16, 16, 12, 12)
            font = QFont(painter.font())
            font.setBold(True)
            font.setPointSize(9)
            painter.setFont(font)
            painter.drawText(16, 15, 12, 14, Qt.AlignmentFlag.AlignCenter, "!")
            painter.end()
        return QIcon(pixmap)

    def _set_refresh_needed(self, needs_refresh: bool) -> None:
        self._refresh_needs_scan = needs_refresh
        if not self._refresh_timer.isActive():
            self._refresh_button.setIcon(self._refresh_icon(needs_refresh))
        self._refresh_button.setToolTip(
            "❗ Needs refreshed — Downloads or Daddy data changed. Click to scan now."
            if needs_refresh else
            "Scan MO2 Downloads again using the bundled catalog and any cached public catalog. This remains read-only."
        )

    def _show_refresh_notice(self, message: str) -> None:
        if self._refresh_notice is None:
            dialog = QDialog(self)
            dialog.setWindowTitle("Package Daddy — working")
            dialog.setModal(False)
            dialog.setFixedSize(220, 170)
            layout = QVBoxLayout(dialog)
            image = QLabel(dialog)
            image.setAlignment(Qt.AlignmentFlag.AlignCenter)
            if WAITING_PATIENTLY_GIF.is_file():
                movie = QMovie(str(WAITING_PATIENTLY_GIF))
                movie.setScaledSize(QSize(88, 88))
                image.setMovie(movie)
                self._refresh_notice_movie = movie
            else:
                image.setPixmap(QPixmap(str(REFRESH_WAIT_ICON)).scaled(
                    88, 88, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
                ))
            layout.addWidget(image)
            text = QLabel(dialog)
            text.setAlignment(Qt.AlignmentFlag.AlignCenter)
            text.setWordWrap(True)
            layout.addWidget(text)
            self._refresh_notice = dialog
            self._refresh_notice_text = text
        assert self._refresh_notice_text is not None
        self._refresh_notice_text.setText(message)
        if self._refresh_notice_movie is not None:
            self._refresh_notice_movie.start()
        self._refresh_notice.show()
        self._refresh_notice.raise_()
        QCoreApplication.processEvents()

    def _start_refresh_spin(self, message: str = "Refreshing MO2 Downloads…") -> None:
        self._refresh_rotation = 0
        self._refresh_button.setEnabled(False)
        self._refresh_timer.start()
        self._show_refresh_notice(message)
        QCoreApplication.processEvents()

    def _stop_refresh_spin(self) -> None:
        self._refresh_timer.stop()
        self._refresh_button.setIcon(self._refresh_icon(self._refresh_needs_scan))
        self._refresh_button.setEnabled(True)
        if self._refresh_notice is not None:
            self._refresh_notice.hide()
        if self._refresh_notice_movie is not None:
            self._refresh_notice_movie.stop()

    def scan_with_refresh(self, _checked: bool = False) -> None:
        """Schedule a scan after Qt has painted the spinning refresh icon."""
        self._start_refresh_spin()
        QTimer.singleShot(0, self._scan_after_refresh_spin)

    def _scan_after_refresh_spin(self) -> None:
        try:
            self.scan(force=True)
        finally:
            self._stop_refresh_spin()

    def scan(self, force: bool = False):
        # Build first, then let Qt sort. Sorting during inserts turns the
        # little action widgets into a proper mess.
        self.table.setSortingEnabled(False)
        self.table.setRowCount(0)
        self._candidates = []
        if not self.downloads.is_dir():
            self.status.setText("MO2 handed Package Daddy a Downloads path that does not exist. Nothing was created.")
            return
        quick_fingerprint = self._quick_scan_fingerprint()
        cache = None if force else self._read_scan_cache(quick_fingerprint)
        cached_candidates, cached_stats, cache_fresh = cache if cache is not None else (None, {}, False)
        archives = [] if cached_candidates is not None else sorted(
            (item for item in self.downloads.iterdir() if item.is_file() and item.suffix.lower() in {".zip", ".7z", ".rar"}),
            key=lambda item: item.name.lower(),
        )
        fingerprint = self._scan_fingerprint(archives) if cached_candidates is None else {}
        approved = _load_catalog(self.mo2_version)
        approved_by_id = {entry.package_id: entry for entry in approved}
        installed = _installed_catalog_ids(self.plugins_root, approved, tool_daddy.tools_root(self.mo2_root))
        receipts = _read_ledger()
        for package_id, receipt in receipts.items():
            if receipt.get("target") == "stylesheets":
                files = receipt.get("files")
                stylesheets = theme_daddy.destination_root(self.mo2_root)
                if isinstance(files, list) and files and all((stylesheets / PurePosixPath(str(item))).is_file() for item in files):
                    installed.add(package_id)
                continue
            if receipt.get("target") == "tools":
                root = receipt.get("install_root")
                if _safe_install_root(root) and (tool_daddy.destination(self.mo2_root, root)).is_dir() and not (tool_daddy.destination(self.mo2_root, root)).is_symlink():
                    installed.add(package_id)
                continue
            root = receipt.get("install_root")
            if _safe_install_root(root) and (self.plugins_root / root).is_dir() and not (self.plugins_root / root).is_symlink():
                installed.add(package_id)
        matches: list[PackageCandidate] = []
        name_only = 0
        for archive_number, archive in enumerate(archives if cached_candidates is None else ()):
            if archive_number % 24 == 0:
                # Let QMovie paint another frame while the big Downloads pass
                # is chewing through thousands of archives. Otherwise the GIF
                # is technically playing but visually dead as a doornail.
                QCoreApplication.processEvents(QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents)
            # Known packages can be named from filenames. Unknown .7z/.rar
            # files wait for our current bundled 7-Zip reader. No guessing.
            if archive.suffix.lower() == ".zip":
                candidate = _parse_package(archive, approved)
                if candidate is not None and tool_daddy.accepts(candidate.kind):
                    candidate = _catalog_tool_archive_candidate(candidate, self.mo2_root)
            else:
                candidate = _catalog_candidate(archive, approved)
                if candidate is not None:
                    if tool_daddy.accepts(candidate.kind) and archive.suffix.lower() == ".7z":
                        reviewed = _catalog_tool_archive_candidate(candidate, self.mo2_root)
                        if reviewed.status == CATALOG_READY:
                            candidate = reviewed
                        else:
                            candidate = PackageCandidate(
                                candidate.archive, candidate.name, candidate.package_id,
                                candidate.kind, candidate.version,
                                "approved catalog match — manifest check pending bundled 7-Zip",
                                install_root=candidate.install_root,
                                nexus_url=candidate.nexus_url, source_url=candidate.source_url,
                                discord_url=candidate.discord_url, wiki_url=candidate.wiki_url,
                                nexus_file_id=candidate.nexus_file_id,
                            )
                            name_only += 1
                    else:
                        candidate = PackageCandidate(
                            candidate.archive, candidate.name, candidate.package_id,
                            candidate.kind, candidate.version,
                            "approved catalog match — manifest check pending bundled 7-Zip",
                            install_root=candidate.install_root,
                            nexus_url=candidate.nexus_url,
                            source_url=candidate.source_url,
                            discord_url=candidate.discord_url,
                            wiki_url=candidate.wiki_url,
                            nexus_file_id=candidate.nexus_file_id,
                        )
                        name_only += 1
            if candidate is not None and candidate.kind == "mo2-mod":
                # Mods still belong to MO2's normal installer. Daddy can keep
                # the catalog link and download button handy, but it does not
                # pretend a mod archive is a plugin or an external tool.
                candidate = PackageCandidate(
                    candidate.archive, candidate.name, candidate.package_id,
                    candidate.kind, candidate.version,
                    "MO2 mod archive — install it from MO2 Downloads",
                    nexus_url=candidate.nexus_url, source_url=candidate.source_url,
                    discord_url=candidate.discord_url, wiki_url=candidate.wiki_url,
                    nexus_file_id=candidate.nexus_file_id,
                )
            if candidate is not None:
                matches.append(candidate)
        # Downloads hoards every revision. Daddy shows one actionable row per
        # package: the newest downloaded/cached archive.
        newest_by_package: dict[str, PackageCandidate] = (
            {candidate.package_id or f"archive:{candidate.archive.resolve()}": candidate for candidate in cached_candidates}
            if cached_candidates is not None else {}
        )
        if cached_candidates is None:
            for candidate in matches:
                key = candidate.package_id or f"archive:{candidate.archive.resolve()}"
                current = newest_by_package.get(key)
                if current is None or candidate.archive.stat().st_mtime > current.archive.stat().st_mtime:
                    newest_by_package[key] = candidate
        # A receipt is Daddy's promise it can uninstall safely. Keep that row
        # visible even when its archive got deduped, renamed, or deleted.
        # Hiding the only uninstall button would be clown behavior.
        for package_id, receipt in receipts.items():
            if package_id in newest_by_package:
                continue
            entry = approved_by_id.get(package_id)
            if entry is not None and theme_daddy.accepts(entry.kind) and receipt.get("target") == "stylesheets":
                files = receipt.get("files") if isinstance(receipt, dict) else None
                if not isinstance(files, list):
                    continue
                archive_name = Path(str(receipt.get("archive") or "")).name
                newest_by_package[package_id] = PackageCandidate(
                    self.downloads / archive_name,
                    str(receipt.get("name") or entry.name), package_id, entry.kind,
                    str(receipt.get("version") or entry.release_version or "—"),
                    "installed — receipt retained for uninstall", nexus_url=entry.nexus_url,
                    source_url=entry.source_url, discord_url=entry.discord_url, wiki_url=entry.wiki_url,
                    nexus_file_id=entry.nexus_file_id,
                )
                continue
            root = receipt.get("install_root") if isinstance(receipt, dict) else None
            files = receipt.get("files") if isinstance(receipt, dict) else None
            if entry is None or not _safe_install_root(root) or not isinstance(files, list):
                continue
            target = str(receipt.get("target") or "plugins")
            if target == "tools" and not tool_daddy.accepts(entry.kind):
                continue
            if target not in {"plugins", "tools"}:
                continue
            archive_name = Path(str(receipt.get("archive") or "")).name
            newest_by_package[package_id] = PackageCandidate(
                self.downloads / archive_name,
                str(receipt.get("name") or entry.name),
                package_id,
                entry.kind,
                str(receipt.get("version") or entry.release_version or "—"),
                "installed — receipt retained for uninstall",
                "",
                str(root),
                tuple(str(item) for item in files),
                entry.nexus_url,
                entry.source_url,
                entry.discord_url,
                entry.wiki_url,
            )

        # A catalog is useless if it ghosts the second its ZIP gets cleaned out
        # of Downloads. Keep known rows visible, but they get zero install
        # privilege until the local review is done.
        catalog_only = 0
        for entry in approved:
            if entry.package_id in newest_by_package:
                continue
            newest_by_package[entry.package_id] = PackageCandidate(
                self.downloads / f"{entry.package_id}.zip",
                entry.name,
                entry.package_id,
                entry.kind,
                entry.release_version or "—",
                "catalog entry — not downloaded",
                nexus_url=entry.nexus_url,
                source_url=entry.source_url,
                discord_url=entry.discord_url,
                wiki_url=entry.wiki_url,
                nexus_file_id=entry.nexus_file_id,
            )
            catalog_only += 1

        # Opening a public Nexus result saves a local lead, not an approval.
        # Handy after the browser closes, but no manifest means no install.
        known_urls = {entry.nexus_url for entry in approved if entry.nexus_url}
        for lead in self._read_nexus_leads():
            package_id = lead.get("id")
            name = lead.get("name")
            kind = lead.get("kind")
            nexus_url = _safe_web_url(lead.get("nexus_url"))
            if (
                not isinstance(package_id, str)
                or not isinstance(name, str)
                or kind not in {"mo2-plugin", "mo2-theme"}
                or not nexus_url
                or nexus_url in known_urls
                or package_id in newest_by_package
            ):
                continue
            author = str(lead.get("author") or "").strip()
            updated = str(lead.get("updated") or "").strip()
            note = "Nexus discovery lead — awaiting Daddy catalog review"
            if author or updated:
                note += f" ({author or 'unknown author'}; {updated or 'no update date'})"
            newest_by_package[package_id] = PackageCandidate(
                self.downloads / f"{package_id}.zip", name.strip(), package_id, kind, "—", note,
                nexus_url=nexus_url,
            )

        def candidate_mtime(candidate: PackageCandidate) -> float:
            try:
                return candidate.archive.stat().st_mtime
            except OSError:
                return 0.0

        candidates = sorted(
            newest_by_package.values(),
            key=candidate_mtime,
            reverse=True,
        )
        computed_suppressed = max(0, len(matches) - len(newest_by_package))
        archive_count = cached_stats.get("archive_count", len(archives)) if cached_candidates is not None else len(archives)
        raw_match_count = cached_stats.get("raw_match_count", len(matches)) if cached_candidates is not None else len(matches)
        suppressed_count = cached_stats.get("suppressed_count", 0) if cached_candidates is not None else computed_suppressed
        name_only_count = cached_stats.get("name_only_count", name_only) if cached_candidates is not None else name_only
        catalog_only_count = cached_stats.get("catalog_only_count", catalog_only) if cached_candidates is not None else catalog_only
        if cached_candidates is None:
            self._write_scan_cache(
                fingerprint, quick_fingerprint, candidates,
                {
                    "archive_count": archive_count,
                    "raw_match_count": raw_match_count,
                    "suppressed_count": suppressed_count,
                    "name_only_count": name_only_count,
                    "catalog_only_count": catalog_only_count,
                },
            )
        for candidate in candidates:
            row = self.table.rowCount()
            self.table.insertRow(row)
            self.table.setRowHeight(row, 48)
            self._candidates.append(candidate)
            state = "installed — not offered for install" if candidate.package_id in installed else candidate.status
            compatibility, compatibility_tip = _compatibility_label(approved_by_id.get(candidate.package_id))
            archive_label = _archive_label(candidate.archive) if candidate.archive.is_file() else "Not downloaded — catalog entry"
            values = (candidate.name, candidate.kind, compatibility, candidate.version, archive_label)
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(
                    "This catalog entry has no local archive yet. Use the download icon to fetch it into MO2 Downloads."
                    if column == 4 and candidate.status == "catalog entry — not downloaded"
                    else (str(candidate.archive) if column == 4 else (compatibility_tip if column == 2 else value))
                )
                self.table.setItem(row, column, item)
            state_label = QLabel(self)
            details_action = self._action_button(
                QStyle.StandardPixmap.SP_MessageBoxInformation,
                "Show what Package Daddy knows about this archive.",
                handler=lambda _checked=False, selected=candidate: self.show_candidate_details(selected),
            )
            open_action = self._action_button(
                QIcon(str(ARCHIVE_ICON)),
                "Open this archive in its default app.",
                handler=lambda _checked=False, selected=candidate: QDesktopServices.openUrl(QUrl.fromLocalFile(str(selected.archive))),
            ) if candidate.archive.is_file() else None
            nexus_action = self._action_button(
                QIcon(str(NEXUS_PAGE_ICON)),
                "Open this package's Nexus page.",
                handler=lambda _checked=False, selected=candidate: QDesktopServices.openUrl(QUrl(selected.nexus_url)),
            ) if candidate.nexus_url else None
            nexus_daddy_action = self._action_button(
                QIcon(str(METADATA_ICON)),
                "Ask Nexus Daddy for live public Nexus metadata. No API key is requested or sent.",
                handler=lambda _checked=False, selected=candidate: self.lookup_with_nexus_daddy(selected),
            ) if candidate.nexus_url and _nexus_target(candidate.nexus_url) and self._nexus_daddy_present() else None
            unreviewed_lead = candidate.status.startswith("Nexus discovery lead")
            nexus_download_action = self._action_button(
                QIcon(str(NEXUS_DOWNLOAD_ICON)),
                "Download Nexus's newest MAIN file into MO2 Downloads. Requires your Nexus Daddy key permission.",
                handler=lambda _checked=False, selected=candidate: self.download_with_nexus_daddy(selected),
            ) if not unreviewed_lead and candidate.nexus_url and _nexus_target(candidate.nexus_url) and self._nexus_daddy_present() else None
            nexus_download_install_action = self._action_button(
                QIcon(str(NEXUS_DADDY_ICON)),
                "Download Nexus's newest MAIN file, validate it again, then install or update it. One confirmation; requires Nexus Daddy permission.",
                handler=lambda _checked=False, selected=candidate: self.download_with_nexus_daddy(selected, install_after=True),
            ) if (
                not unreviewed_lead
                and candidate.kind != "mo2-mod"
                and candidate.nexus_url
                and _nexus_target(candidate.nexus_url)
                and self._nexus_daddy_present()
            ) else None
            source_action = self._action_button(
                QIcon(str(GITHUB_ICON)),
                "Open this package's source page.",
                handler=lambda _checked=False, selected=candidate: QDesktopServices.openUrl(QUrl(selected.source_url)),
            ) if candidate.source_url else None
            discord_action = self._action_button(
                QIcon(str(DISCORD_ICON)),
                "Open this package's Discord in the desktop app when it is installed; otherwise use the browser.",
                handler=lambda _checked=False, selected=candidate: self.open_discord(selected.discord_url),
            ) if candidate.discord_url else None
            wiki_action = self._action_button(
                QIcon(str(WIKI_ICON)),
                "Open this package's Wiki entry.",
                handler=lambda _checked=False, selected=candidate: QDesktopServices.openUrl(QUrl(selected.wiki_url)),
            ) if candidate.wiki_url else None
            receipt = self._theme_receipt_for(candidate, receipts) if theme_daddy.accepts(candidate.kind) else self._receipt_for(candidate, receipts)
            install_action = None
            update_action = None
            uninstall_action = None
            discard_download_action = None
            if candidate.package_id not in installed and self._is_installable(candidate):
                install_action = self._action_button(
                    QIcon(str(INSTALL_ICON)),
                    "Choose and install this reviewed theme payload into MO2's stylesheets folder."
                    if theme_daddy.accepts(candidate.kind) else "Install this reviewed package into MO2's plugins folder.",
                    handler=lambda _checked=False, selected=candidate: self.install_candidate(selected),
                )
            if candidate.package_id in installed and tool_daddy.accepts(candidate.kind) and self._is_installable(candidate):
                update_action = self._action_button(
                    QIcon(str(UPDATE_ICON)),
                    "Replace this installed tool with the reviewed archive. Daddy keeps the previous folder under tools\\.package-daddy-backups.",
                    handler=lambda _checked=False, selected=candidate: self.update_tool_candidate(selected),
                )
            if receipt is not None:
                uninstall_action = self._action_button(
                    QIcon(str(UNINSTALL_ICON)),
                    "Uninstall only the stylesheet files recorded by Theme Daddy."
                    if theme_daddy.accepts(candidate.kind) else "Uninstall this folder using Package Daddy's recorded receipt.",
                    handler=(lambda _checked=False, selected=candidate: self.uninstall_theme_candidate(selected))
                    if theme_daddy.accepts(candidate.kind) else (lambda _checked=False, selected=candidate: self.uninstall_candidate(selected)),
                )
            if self._is_owned_download(candidate.archive):
                discard_download_action = self._action_button(
                    QIcon(str(DELETE_DOWNLOAD_ICON)),
                    "Delete this downloaded archive from MO2 Downloads. It does not uninstall or touch any installed files.",
                    handler=lambda _checked=False, selected=candidate: self.discard_download(selected),
                )
            update = self._update_results.get(candidate.package_id)
            if update and update.get("state") == "Update available":
                state_label.setText("❗")
                warning_font = QFont(state_label.font())
                warning_font.setPointSize(max(warning_font.pointSize() + 2, 13))
                state_label.setFont(warning_font)
                state_label.setToolTip(
                    f"Update available from {update.get('source', 'upstream')}: "
                    f"{update.get('local', '—')} → {update.get('remote', '—')}. "
                    f"Upstream says it changed: {update.get('updated', '—')}."
                )
            elif candidate.package_id in installed:
                state_label.setPixmap(QIcon(str(INSTALLED_ICON)).pixmap(20, 20))
                state_label.setToolTip("Installed locally." + (" A reviewed update is ready." if update_action is not None else " Package Daddy will not offer to install it again."))
            elif candidate.status == "catalog entry — not downloaded":
                state_label.setPixmap(self.style().standardIcon(QStyle.StandardPixmap.SP_ArrowDown).pixmap(16, 16))
                state_label.setToolTip("Catalogued but not downloaded yet. Use the download icon to fetch it from Nexus.")
            elif state.startswith("incompatible:"):
                state_label.setPixmap(self.style().standardIcon(QStyle.StandardPixmap.SP_BrowserStop).pixmap(16, 16))
                state_label.setToolTip("Not compatible with this MO2 version.")
            elif "pending bundled 7-Zip" in state:
                state_label.setPixmap(self.style().standardIcon(QStyle.StandardPixmap.SP_MessageBoxWarning).pixmap(16, 16))
                state_label.setToolTip("Recognized by the approved catalog, but its .7z/.rar manifest needs bundled current 7-Zip validation first.")
            elif self._is_installable(candidate):
                state_label.setPixmap(self.style().standardIcon(QStyle.StandardPixmap.SP_ArrowDown).pixmap(16, 16))
                state_label.setToolTip("Reviewed package is ready for explicit install review.")
            else:
                state_label.setPixmap(self.style().standardIcon(QStyle.StandardPixmap.SP_MessageBoxCritical).pixmap(16, 16))
                state_label.setToolTip(f"Needs attention: {state}")
            state_widget = QWidget(self)
            state_layout = QHBoxLayout(state_widget)
            state_layout.setContentsMargins(4, 0, 4, 0)
            state_layout.setSpacing(5)
            state_layout.addWidget(state_label)
            if install_action is not None:
                state_layout.addWidget(install_action)
            if update_action is not None:
                state_layout.addWidget(update_action)
            if uninstall_action is not None:
                state_layout.addWidget(uninstall_action)
            if discard_download_action is not None:
                state_layout.addWidget(discard_download_action)
            state_layout.addWidget(details_action)
            state_layout.addWidget(open_action)
            for action in (nexus_action, nexus_daddy_action, nexus_download_action, nexus_download_install_action, source_action, discord_action, wiki_action):
                if action is not None:
                    state_layout.addWidget(action)
            state_layout.addStretch(1)
            self.table.setCellWidget(row, 5, state_widget)
        self.table.setSortingEnabled(True)
        self._apply_filters()
        declared = sum(bool(candidate.manifest_path) for candidate in candidates)
        reviewed_catalog = sum(candidate.status == CATALOG_READY for candidate in candidates)
        cataloged = len(candidates) - declared
        installed_count = sum(candidate.package_id in installed for candidate in candidates)
        logger.info(
            "Scan complete: %d archives, %d raw matches, %d catalog-only rows, %d shown, %d older duplicates hidden, %d installed, %d name-only .7z/.rar matches.",
            archive_count, raw_match_count, catalog_only_count, len(candidates), suppressed_count, installed_count, name_only_count,
        )
        pending_note = f" {name_only_count} .7z/.rar match(es) are name-identified until bundled 7-Zip validates their manifests." if name_only_count else ""
        catalog_source = "bundled catalog"
        if _remote_catalog_path().is_file():
            catalog_source += " + cached public catalog"
        if cached_candidates is None:
            cache_note = ""
            self._set_refresh_needed(False)
        elif cache_fresh:
            cache_note = "Reused the last scan; Downloads, catalogs, and receipts have not changed. "
            self._set_refresh_needed(False)
        else:
            cache_note = "Showing the last scan; Downloads or Daddy data changed. Hit the ❗ refresh button when you want the list rebuilt. "
            self._set_refresh_needed(True)
        notice = ((self._catalog_notice + " ") if self._catalog_notice else "") + cache_note
        self.status.setText(notice + f"Checked {archive_count} ZIP/7Z/RAR archive(s) in MO2 Downloads using the {catalog_source}. Showing {declared} declared package(s), {cataloged} catalog row(s), and {catalog_only_count} not-downloaded catalog entry/entries; newest local archives first. {reviewed_catalog} catalog ZIP(s) passed Daddy's payload review and can be explicitly installed. Hid {suppressed_count} older duplicate(s). {installed_count} shown as already installed, never as install targets.{pending_note}")

    @staticmethod
    def _nexus_daddy_present() -> bool:
        try:
            from nexus_daddy import api as nexus_daddy_api
            return hasattr(nexus_daddy_api, "client")
        except ImportError:
            return False

    def browse_public_nexus(self):
        """Borrow Plugin Browser's useful public *discovery* feed, nothing more.

        Nexus hosts some MO2 extensions under the special ``site`` game.  That
        feed is handy for discovering new things, but it absolutely is not a
        complete cross-game index: themes and tools can live under Skyrim,
        Fallout, OpenMW, or wherever their author put them.  Package Daddy's
        catalog stays the cross-game source of truth and this window never
        silently promotes a random browse result into an installable package.
        """
        if not self._nexus_daddy_present():
            QMessageBox.information(
                self,
                "Package Daddy — Nexus discovery",
                "Nexus Daddy is needed for the public browse request. No API key is needed or requested.",
            )
            return
        try:
            from nexus_daddy import api as nexus_daddy_api
            query = """
            query($count: Int, $filter: ModsFilter, $sort: [ModsSort!]) {
              mods(count: $count, filter: $filter, sort: $sort) {
                totalCount
                nodes { modId name summary updatedAt modCategory { name } uploader { name } }
              }
            }
            """
            # Same public category feed Plugin Browser uses, but the label in
            # our UI says exactly what it is so nobody assumes it searched all
            # of Nexus.  The catalog carries cross-game records instead.
            variables = {
                "count": 48,
                "sort": [{"updatedAt": {"direction": "DESC"}}],
                "facets": {"status": ["published"]},
                "filter": {
                    "gameDomainName": [{"op": "EQUALS", "value": "site"}],
                    "filter": {
                        "op": "OR",
                        "categoryName": [
                            {"op": "EQUALS", "value": "Mod Organizer 2 Plugins"},
                            {"op": "EQUALS", "value": "Mod Organizer 2 Themes"},
                        ],
                    },
                },
            }
            data = nexus_daddy_api.client(self.organizer, "Package Daddy", require_key=False).graphql(query, variables)
            mods = (data.get("mods") or {}).get("nodes") or []
            if not isinstance(mods, list):
                raise nexus_daddy_api.NexusDaddyError("Nexus returned an unexpected public browse response.")
            dialog = QDialog(self)
            dialog.setWindowTitle("Package Daddy — public Nexus MO2 browse")
            dialog.resize(920, 560)
            layout = QVBoxLayout(dialog)
            total = (data.get("mods") or {}).get("totalCount", len(mods))
            intro = QLabel(
                f"Nexus has {total} published item(s) in its site-hosted MO2 Plugins/Themes feed. "
                "This is discovery only — it does not cover items hosted under Skyrim or other games, and it does not download or install anything.",
                dialog,
            )
            intro.setWordWrap(True)
            layout.addWidget(intro)
            table = QTableWidget(0, 4, dialog)
            table.setHorizontalHeaderLabels(("Name", "Kind", "Author", "Updated"))
            table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
            table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
            table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
            table.horizontalHeader().setStretchLastSection(True)
            table.setColumnWidth(0, 290)
            table.setColumnWidth(1, 210)
            table.setColumnWidth(2, 160)
            mods_by_id: dict[int, dict] = {}
            for row, mod in enumerate(mods):
                if not isinstance(mod, dict):
                    continue
                try:
                    mod_id = int(mod.get("modId"))
                except (TypeError, ValueError):
                    continue
                mods_by_id[mod_id] = mod
                table.insertRow(table.rowCount())
                values = (
                    str(mod.get("name") or "Unnamed Nexus entry"),
                    str((mod.get("modCategory") or {}).get("name") or "—"),
                    str((mod.get("uploader") or {}).get("name") or "—"),
                    str(mod.get("updatedAt") or "—"),
                )
                for column, value in enumerate(values):
                    item = QTableWidgetItem(value)
                    item.setData(Qt.ItemDataRole.UserRole, mod_id)
                    item.setToolTip(str(mod.get("summary") or value))
                    table.setItem(table.rowCount() - 1, column, item)
            def open_selected():
                row = table.currentRow()
                item = table.item(row, 0) if row >= 0 else None
                if item is not None:
                    mod_id = int(item.data(Qt.ItemDataRole.UserRole))
                    self._remember_nexus_lead(mods_by_id[mod_id])
                    self._catalog_notice = f"Saved {item.text()} as an unreviewed Nexus discovery lead. It is browse-only until Daddy gets real package rules for it."
                    self.scan(force=True)
                    QDesktopServices.openUrl(QUrl(f"https://www.nexusmods.com/site/mods/{mod_id}"))
            table.itemDoubleClicked.connect(lambda _item: open_selected())
            layout.addWidget(table)
            actions = QHBoxLayout()
            open_page = QPushButton("Open selected on Nexus", dialog)
            open_page.clicked.connect(open_selected)
            close = QPushButton("Close", dialog)
            close.clicked.connect(dialog.accept)
            actions.addWidget(open_page)
            actions.addStretch(1)
            actions.addWidget(close)
            layout.addLayout(actions)
            dialog.exec()
        except (ImportError, AttributeError) as exc:
            QMessageBox.warning(self, "Package Daddy — Nexus discovery", f"Nexus Daddy is unavailable: {exc}")
        except Exception as exc:
            logger.warning("Nexus browse faceplanted: %s", exc)
            QMessageBox.warning(self, "Package Daddy — Nexus discovery", str(exc))

    @staticmethod
    def open_discord(url: str) -> None:
        """Prefer Discord's app protocol; browser is the boring fallback."""
        parsed = urlparse(url)
        parts = [part for part in parsed.path.split("/") if part]
        host = parsed.netloc.casefold()
        invite = parts[-1] if parts and (host in {"discord.gg", "www.discord.gg"} or "discord.com" in host) else ""
        if invite and QDesktopServices.openUrl(QUrl(f"discord://-/invite/{invite}")):
            return
        QDesktopServices.openUrl(QUrl(url))

    def _known_local_versions(self) -> dict[str, str]:
        """Return versions Daddy can prove, never guesses based on a folder name."""
        versions: dict[str, str] = {}
        for package_id, receipt in _read_ledger().items():
            version = receipt.get("version") if isinstance(receipt, dict) else None
            if isinstance(version, str) and version.strip():
                versions[package_id] = version.strip()
        for root in (self.plugins_root, tool_daddy.tools_root(self.mo2_root)):
            try:
                folders = list(root.iterdir())
            except OSError:
                continue
            for folder in folders:
                manifest = folder / MANIFEST_NAME
                try:
                    raw = json.loads(manifest.read_text(encoding="utf-8"))
                    package_id, version = raw.get("id"), raw.get("version")
                    if isinstance(package_id, str) and isinstance(version, str) and version.strip():
                        versions[package_id] = version.strip()
                except (OSError, ValueError, TypeError, json.JSONDecodeError):
                    continue
        return versions

    def check_nexus_updates(self):
        """Compare proven local versions with each catalogued public upstream."""
        try:
            catalog = _load_catalog(self.mo2_version)
            nexus_entries = []
            for entry in catalog:
                target = _nexus_target(entry.nexus_url)
                if target is not None:
                    nexus_entries.append((entry, *target))
            # Nexus is the first choice when it exists, but it damn well isn't
            # the only choice.  Some public records are flaky, moved, or just
            # take Nexus a minute to admit they exist.  Keep GitHub around as
            # the fallback instead of silently dropping the date on the floor.
            github_by_id = {
                entry.package_id: (entry, *_github_target(entry.source_url))
                for entry in catalog if _github_target(entry.source_url) is not None
            }
            if not nexus_entries and not github_by_id:
                QMessageBox.information(self, "Package Daddy — update check", "No catalog entries have a public Nexus or GitHub release source yet.")
                return
            rows: list[tuple[ApprovedPackage, str, str, str, str, str, str]] = []
            local_versions = self._known_local_versions()
            # Nexus is public metadata through Nexus Daddy's Qt client. A key
            # is still unnecessary here; it only matters for direct downloads.
            nexus = None
            if nexus_entries and self._nexus_daddy_present():
                from nexus_daddy import api as nexus_daddy_api
                nexus = nexus_daddy_api.client(self.organizer, "Package Daddy", require_key=False)
            elif nexus_entries:
                logger.info("Nexus update checks need Nexus Daddy; trying GitHub fallbacks where possible.")
            game_ids: dict[str, int] = {}
            if nexus is not None:
                for domain in sorted({domain for _, domain, _ in nexus_entries}):
                    game_id = nexus.game_id(domain)
                    if game_id is not None:
                        game_ids[domain] = game_id
            requested: list[tuple[int, ApprovedPackage, str, int]] = []
            failed_nexus_ids: set[str] = set()
            for index, (entry, domain, mod_id) in enumerate(nexus_entries):
                game_id = game_ids.get(domain)
                if game_id is None:
                    failed_nexus_ids.add(entry.package_id)
                    logger.info("Nexus could not resolve the game domain for %s; trying its fallback source.", entry.name)
                    continue
                requested.append((index, entry, domain, mod_id))
            # One deleted/moved Nexus page used to poison the whole batched
            # GraphQL request. Ask one at a time so a stale catalog link gets
            # a quiet unavailable row while the rest still report updates.
            query = "query($game: ID!, $mod: ID!) { mod(gameId: $game, modId: $mod) { name version updatedAt } }"
            remote_rows: dict[int, dict] = {}
            unavailable: set[int] = set()
            for index, _entry, domain, mod_id in requested:
                try:
                    assert nexus is not None
                    game_id = game_ids[domain]
                    remote = nexus.graphql(query, {"game": str(game_id), "mod": str(mod_id)}).get("mod")
                    remote_rows[index] = remote if isinstance(remote, dict) else {}
                except Exception as exc:
                    unavailable.add(index)
                    logger.info("Skipped stale/dead Nexus record %s/%s: %s", domain, mod_id, exc)
            for index, entry, domain, mod_id in requested:
                remote = remote_rows.get(index, {})
                nexus_version = str(remote.get("version") or "—").strip() or "—"
                local = local_versions.get(entry.package_id, "local version unknown")
                if index in unavailable:
                    failed_nexus_ids.add(entry.package_id)
                    continue
                elif local == "local version unknown":
                    state = "Installed outside Daddy / not versioned"
                elif nexus_version == "—":
                    state = "Nexus gave no version"
                elif _version_key(nexus_version) > _version_key(local):
                    state = "Update available"
                elif _version_key(nexus_version) == _version_key(local):
                    state = "Current"
                else:
                    state = "Local version is newer"
                rows.append((entry, local, nexus_version, state, str(remote.get("updatedAt") or "—"), entry.nexus_url, "Nexus"))
            successful_nexus_ids = {entry.package_id for entry, *_ in requested if entry.package_id not in failed_nexus_ids}
            github_entries = [
                github for package_id, github in github_by_id.items()
                if package_id not in successful_nexus_ids
            ]
            for entry, owner, repo in github_entries:
                local = local_versions.get(entry.package_id, "local version unknown")
                try:
                    remote = _github_latest_release(owner, repo)
                    remote_version = str(remote.get("tag_name") or remote.get("name") or "—").strip() or "—"
                    updated = str(remote.get("published_at") or remote.get("created_at") or "—")
                    if local == "local version unknown":
                        state = "Installed outside Daddy / not versioned"
                    elif remote_version == "—":
                        state = "GitHub gave no release tag"
                    elif _version_key(remote_version) > _version_key(local):
                        state = "Update available"
                    elif _version_key(remote_version) == _version_key(local):
                        state = "Current"
                    else:
                        state = "Local version is newer"
                except Exception as exc:
                    logger.info("Skipped unavailable GitHub repo %s/%s: %s", owner, repo, exc)
                    remote_version, updated, state = "—", "—", "GitHub record unavailable"
                rows.append((entry, local, remote_version, state, updated, entry.source_url, "GitHub"))
            github_ids = set(github_by_id)
            for entry, _domain, _mod_id in nexus_entries:
                if entry.package_id in successful_nexus_ids or entry.package_id in github_ids:
                    continue
                local = local_versions.get(entry.package_id, "local version unknown")
                state = "Nexus update check unavailable" if nexus is None else "Nexus record unavailable"
                rows.append((entry, local, "—", state, "—", entry.nexus_url, "Nexus"))
            rows.sort(key=lambda item: (item[3] != "Update available", item[0].name.casefold()))
            self._update_results = {
                entry.package_id: {
                    "local": local, "remote": remote, "state": state,
                    "updated": updated, "url": url, "source": source,
                }
                for entry, local, remote, state, updated, url, source in rows
            }
            updates = sum(row[3] == "Update available" for row in rows)
            unavailable = sum("unavailable" in row[3].lower() for row in rows)
            self._catalog_notice = (
                f"Update check: {len(rows)} public source(s), {updates} update(s) available. "
                "Rows marked ❗ have a newer upstream version; hover the icon for the version details."
                + (f" {unavailable} source(s) could not be reached." if unavailable else "")
            )
            self.scan(force=True)
        except (ImportError, AttributeError) as exc:
            QMessageBox.warning(self, "Package Daddy — update check", f"A public update source is unavailable: {exc}")
        except Exception as exc:
            logger.warning("Public update check went sideways: %s", exc)
            QMessageBox.warning(self, "Package Daddy — update check", str(exc))

    def lookup_with_nexus_daddy(self, candidate: PackageCandidate):
        target = _nexus_target(candidate.nexus_url)
        if target is None:
            return
        try:
            from nexus_daddy import api as nexus_daddy_api
            nexus = nexus_daddy_api.client(self.organizer, "Package Daddy", require_key=False)
            game_domain, mod_id = target
            game_id = nexus.game_id(game_domain)
            if game_id is None:
                raise nexus_daddy_api.NexusDaddyError(f"Nexus did not recognize the game domain '{game_domain}'.")
            response = nexus.graphql(
                "query($game: ID!, $mod: ID!) { mod(gameId: $game, modId: $mod) { modId name version adult modCategory { name } } }",
                {"game": game_id, "mod": mod_id},
            )
            record = response.get("mod") or {}
            if not record:
                raise nexus_daddy_api.NexusDaddyError("Nexus did not return a mod record for this page.")
            QMessageBox.information(
                self,
                "Package Daddy — live Nexus metadata",
                f"Name: {record.get('name') or '—'}\n"
                f"Version: {record.get('version') or '—'}\n"
                f"Category: {(record.get('modCategory') or {}).get('name') or '—'}\n"
                f"Adult: {'yes' if record.get('adult') else 'no'}\n"
                f"Nexus ID: {record.get('modId') or mod_id}\n\n"
                "This was a public metadata lookup. Package Daddy did not download, install, or authenticate anything.",
            )
        except (ImportError, AttributeError) as exc:
            QMessageBox.warning(self, "Package Daddy", f"Nexus Daddy is unavailable: {exc}")
        except Exception as exc:
            logger.warning("Nexus Daddy lookup choked on %s: %s", candidate.name, exc)
            QMessageBox.warning(self, "Package Daddy — Nexus Daddy", str(exc))

    def _mo2_game_short_name(self, fallback: str) -> str:
        """Ask MO2 for its own game key; Nexus's URL domain is only backup."""
        try:
            game = self.organizer.managedGame()
            for name in ("gameShortName", "gameName", "name"):
                value = getattr(game, name, None)
                value = value() if callable(value) else value
                if isinstance(value, str) and value.strip():
                    return value.strip()
        except Exception as exc:
            logger.info("Couldn't ask MO2 for its game key; using Nexus domain instead: %s", exc)
        return fallback

    def _write_mo2_nexus_metadata(
        self, archive: Path, candidate: PackageCandidate, game_domain: str, mod_id: int, file_id: int, record: dict,
    ) -> None:
        """Give MO2 the small .meta receipt it expects beside a Nexus download.

        The CDN URL is deliberately not saved: it has short-lived, account-ish
        junk in it and is useless after the download anyway. The stable Nexus
        page plus game/mod/file IDs are what MO2 needs to recognize the file.
        """
        sidecar = Path(str(archive) + ".meta")
        settings = QSettings(str(sidecar), QSettings.Format.IniFormat)
        prefix = "General/"
        display_name = str(record.get("name") or archive.stem).strip() or archive.stem
        version = str(record.get("version") or record.get("file_version") or "").strip()
        try:
            category_id = int(record.get("category_id") or 0)
        except (TypeError, ValueError):
            category_id = 0
        settings.setValue(prefix + "gameName", self._mo2_game_short_name(game_domain))
        settings.setValue(prefix + "modID", int(mod_id))
        settings.setValue(prefix + "fileID", int(file_id))
        settings.setValue(prefix + "url", candidate.nexus_url)
        settings.setValue(prefix + "name", display_name)
        settings.setValue(prefix + "modName", candidate.name)
        settings.setValue(prefix + "version", version)
        settings.setValue(prefix + "newestVersion", version)
        settings.setValue(prefix + "fileCategory", category_id)
        settings.setValue(prefix + "repository", "Nexus")
        settings.setValue(prefix + "installed", False)
        settings.setValue(prefix + "uninstalled", False)
        settings.setValue(prefix + "paused", False)
        settings.setValue(prefix + "removed", False)
        try:
            uploaded = int(record.get("uploaded_timestamp") or 0)
        except (TypeError, ValueError):
            uploaded = 0
        if uploaded > 0:
            settings.setValue(prefix + "fileTime", QDateTime.fromSecsSinceEpoch(uploaded, Qt.TimeSpec.UTC))
        settings.sync()
        logger.info("Wrote MO2 download metadata for %s (%s).", archive.name, sidecar.name)

    @staticmethod
    def _remove_mo2_download_metadata(archive: Path) -> None:
        """Remove only the paired MO2 sidecar when its archive is consumed."""
        sidecar = Path(str(archive) + ".meta")
        try:
            if sidecar.is_file():
                sidecar.unlink()
        except OSError as exc:
            logger.warning("Couldn't remove stale MO2 metadata %s: %s", sidecar.name, exc)

    def download_with_nexus_daddy(self, candidate: PackageCandidate, install_after: bool = False):
        """List Nexus files and download exactly the one the user selects.

        Package Daddy never falls back to browser scraping or a bypass when
        Nexus declines a direct link.  Nexus Daddy must first have a user key
        and an explicit allowance for this requester.
        """
        target = _nexus_target(candidate.nexus_url)
        if target is None:
            return
        logger.info("Nexus download asked for %s.", candidate.name)
        try:
            from nexus_daddy import api as nexus_daddy_api
            nexus = nexus_daddy_api.client(self.organizer, "Package Daddy", require_key=True)
            if nexus is None:
                QMessageBox.information(
                    self,
                    "Package Daddy — Nexus download permission",
                    "Nexus Daddy needs an API key permission before it can ask Nexus for download links.\n\n"
                    "Open Nexus Daddy, store your own key, select Package Daddy in its permission list, and click Allow selected. Then come back here and choose the file again.",
                )
                return
            game_domain, mod_id = target
            files = nexus.files(game_domain, mod_id)
            logger.info("Nexus handed back %d file record(s) for %s (%s/%s).", len(files), candidate.name, game_domain, mod_id)
            if not files:
                QMessageBox.information(self, "Package Daddy — Nexus Daddy", "Nexus did not list any downloadable files for this mod.")
                return
            usable = []
            for entry in files:
                try:
                    file_id = int(entry.get("file_id"))
                except (TypeError, ValueError):
                    continue
                usable.append(entry)
            if not usable:
                QMessageBox.warning(self, "Package Daddy — Nexus Daddy", "Nexus returned file records without usable file IDs.")
                return
            # The row represents one package, not a mini file browser. Prefer
            # the newest MAIN file (Nexus category 1); if Nexus provides no
            # MAIN file, use its newest usable entry. The confirmation below
            # names the exact file before any bytes move.
            main_files = [
                entry for entry in usable
                if entry.get("category_id") == 1
                or str(entry.get("category_name") or "").strip().upper() == "MAIN"
            ]

            def newest_key(entry):
                try:
                    return (int(entry.get("uploaded_timestamp") or 0), int(entry.get("file_id") or 0))
                except (TypeError, ValueError):
                    return (0, 0)

            # The catalog may remember the release file ID it was last checked
            # against, but authors can publish updates whenever they damn well
            # please.  Keep Nexus's newest MAIN file as the actual target.
            selected = max(main_files or usable, key=newest_key)
            logger.info("Nexus picked its newest %s file for %s; no picker needed.", "MAIN" if main_files else "available", candidate.name)
            try:
                file_id = int(selected.get("file_id"))
            except (TypeError, ValueError):
                QMessageBox.warning(self, "Package Daddy — Nexus Daddy", "That Nexus file no longer has a usable ID.")
                return
            filename = Path(str(selected.get("name") or f"nexus-{mod_id}-{file_id}.zip")).name
            # Nexus occasionally returns an extensionless display name such
            # as ``Plugin 1.0.0``. Windows/Python then call the final ``.0``
            # an extension. Make version dots filename-safe before Nexus
            # Daddy adds the actual archive suffix from the file signature.
            filename = re.sub(r"(?<=\d)\.(?=\d)", "_", filename)
            if not filename or filename in {".", ".."}:
                raise RuntimeError("Nexus returned an unsafe file name.")
            destination = self.downloads / filename
            if destination.exists():
                QMessageBox.information(self, "Package Daddy — Nexus Daddy", f"Already in MO2 Downloads:\n{destination.name}\n\nDaddy will not overwrite it.")
                return
            answer = QMessageBox.question(
                self,
                "Package Daddy — download from Nexus",
                (
                    f"Download this Nexus file into MO2 Downloads?\n\n{filename}\n→ {destination}\n\nThis does not install it."
                    if not install_after else
                    f"Download and install/update this Nexus file?\n\n{filename}\n→ {destination}\n→ install location decided after Daddy re-checks the ZIP\n\nDaddy will validate the downloaded ZIP again before it touches anything. If validation fails, it stays in Downloads and nothing is installed."
                ),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                logger.info("Nexus download cancelled for %s (%s).", candidate.name, filename)
                return
            logger.info("Nexus download confirmed for %s: file %s → %s", candidate.name, file_id, destination)
            links = nexus.download_links(game_domain, mod_id, file_id)
            logger.info("Nexus returned %d allowed download link(s) for %s.", len(links), filename)
            saved = nexus.download_to(links[0], destination)
            self._write_mo2_nexus_metadata(saved, candidate, game_domain, mod_id, file_id, selected)
            logger.info("Nexus download landed for %s: %s", candidate.name, saved)
            if install_after:
                approved = _load_catalog(self.mo2_version)
                # Nexus does not promise ZIPs. DynDOLOD, for one, quite
                # reasonably ships a .7z. The Downloads scan already knows
                # how to review that with bundled 7-Zip, so use the same
                # damn path here instead of downloading it and then acting
                # surprised that it isn't a ZIP.
                if saved.suffix.lower() == ".zip":
                    reviewed = _parse_package(saved, approved)
                    if reviewed is not None and tool_daddy.accepts(reviewed.kind):
                        reviewed = _catalog_tool_archive_candidate(reviewed, self.mo2_root)
                elif saved.suffix.lower() == ".7z":
                    reviewed = _catalog_candidate(saved, approved)
                    if reviewed is not None:
                        reviewed = _catalog_tool_archive_candidate(reviewed, self.mo2_root)
                else:
                    reviewed = None
                if reviewed is None or not self._is_installable(reviewed):
                    self._catalog_notice = f"Downloaded {saved.name}, but Daddy refused to install it: it did not pass the post-download archive review."
                    logger.warning("Downloaded archive flunked the post-download review: %s", saved)
                    self.scan(force=True)
                    return
                logger.info("Downloaded archive passed the post-download review: %s", saved)
                installed_now = _installed_catalog_ids(
                    self.plugins_root,
                    _load_catalog(self.mo2_version),
                    tool_daddy.tools_root(self.mo2_root),
                )
                if reviewed.package_id in installed_now and tool_daddy.accepts(reviewed.kind):
                    # This route is also how an already-installed external
                    # tool gets its safe replacement flow. Hiding the damn
                    # button just because it exists locally made updates look
                    # broken, which they kinda were.
                    self.update_tool_candidate(reviewed)
                    return
                self.install_candidate(reviewed, confirmed=True)
                return
            # The final confirmation already named the exact archive and
            # destination.  Put the receipt in the persistent, readable
            # status line instead of making the user dismiss a victory popup.
            self._catalog_notice = f"Downloaded {saved.name} to MO2 Downloads with its Nexus metadata. Daddy did not install it."
            self.scan(force=True)
        except (ImportError, AttributeError) as exc:
            QMessageBox.warning(self, "Package Daddy", f"Nexus Daddy is unavailable: {exc}")
        except Exception as exc:
            logger.warning("Nexus Daddy download went sideways for %s: %s", candidate.name, exc)
            if "(403)" in str(exc) and candidate.nexus_url:
                answer = QMessageBox.question(
                    self,
                    "Package Daddy — Nexus blocked the app download",
                    "Nexus refused the direct download link (403). Daddy will not try to bypass that.\n\n"
                    "Open the normal Nexus page instead? Your browser can use its own logged-in session and Nexus's usual download flow.",
                    QMessageBox.StandardButton.Open | QMessageBox.StandardButton.Cancel,
                )
                if answer == QMessageBox.StandardButton.Open:
                    QDesktopServices.openUrl(QUrl(candidate.nexus_url))
                return
            QMessageBox.warning(self, "Package Daddy — Nexus Daddy", str(exc))

    def refresh_public_catalog(self):
        self._start_refresh_spin("Refreshing the public Package Daddy catalog…")
        try:
            count = _refresh_remote_catalog()
            self._catalog_notice = f"Public catalog refreshed: {count} package record(s) cached locally."
            logger.info("Public catalog refreshed; got %d packages.", count)
            self.scan(force=True)
        except RuntimeError as exc:
            logger.warning("Public catalog refresh ate it: %s", exc)
            QMessageBox.warning(
                self,
                "Package Daddy — public catalog",
                f"{exc}\n\nThe bundled catalog was left alone; no package was downloaded or changed.",
            )
        finally:
            self._stop_refresh_spin()

    @staticmethod
    def _is_installable(candidate: PackageCandidate) -> bool:
        return candidate.status in {DECLARED_READY, CATALOG_READY, theme_daddy.READY}

    def _destination_for(self, candidate: PackageCandidate) -> Path:
        """One package owns one folder in the location its kind is allowed to use."""
        if tool_daddy.accepts(candidate.kind):
            return tool_daddy.destination(self.mo2_root, candidate.install_root)
        return plugin_daddy.destination(self.mo2_root, candidate.install_root)

    def _receipt_for(self, candidate: PackageCandidate, receipts: dict[str, dict] | None = None) -> dict | None:
        receipt = (receipts if receipts is not None else _read_ledger()).get(candidate.package_id)
        if not isinstance(receipt, dict):
            return None
        root, files = receipt.get("install_root"), receipt.get("files")
        if not _safe_install_root(root) or not isinstance(files, list) or not files:
            return None
        if not all(isinstance(item, str) and _safe_relative(item) for item in files):
            return None
        destination = tool_daddy.destination(self.mo2_root, root) if receipt.get("target") == "tools" else self.plugins_root / root
        return receipt if destination.is_dir() and not destination.is_symlink() else None

    def _theme_receipt_for(self, candidate: PackageCandidate, receipts: dict[str, dict] | None = None) -> dict | None:
        receipt = (receipts if receipts is not None else _read_ledger()).get(candidate.package_id)
        if not isinstance(receipt, dict):
            return None
        try:
            theme_daddy.removable_files(receipt, theme_daddy.destination_root(self.mo2_root))
            return receipt
        except (OSError, RuntimeError):
            return None

    def _action_button(self, icon: QStyle.StandardPixmap | QIcon, tooltip: str, handler=None, enabled: bool = True, large: bool = False) -> QToolButton:
        button = QToolButton(self)
        # Auto-raise leaves the base, hover, disabled, and focus treatment to
        # the active MO2 stylesheet instead of forcing bright native buttons.
        button.setAutoRaise(True)
        if large:
            button.setFixedSize(46, 38)
        else:
            button.setFixedSize(34, 30)
        button.setIcon(icon if isinstance(icon, QIcon) else self.style().standardIcon(icon))
        button.setIconSize(QSize(28, 28) if large else QSize(22, 22))
        button.setToolTip(tooltip)
        button.setEnabled(enabled)
        if handler is not None:
            button.clicked.connect(handler)
        return button

    def show_candidate_details(self, candidate: PackageCandidate):
        declared = "yes" if candidate.manifest_path else "no"
        approval = "declared manifest" if candidate.manifest_path else (
            "approved catalog + reviewed ZIP payload" if candidate.status == CATALOG_READY else "catalog identification only"
        )
        file_list = "\n".join(f"  • {item}" for item in candidate.files) if candidate.files else "  (none)"
        update = self._update_results.get(candidate.package_id)
        upstream = "No live upstream check has been run this session."
        if update:
            upstream = (
                f"{update.get('source', 'upstream')}: {update.get('state', '—')}\n"
                f"Local: {update.get('local', '—')}\n"
                f"Upstream version: {update.get('remote', '—')}\n"
                f"Upstream updated: {update.get('updated', '—')}"
            )
        QMessageBox.information(
            self,
            "Package Daddy — package details",
            f"Name: {candidate.name}\n"
            f"Kind: {candidate.kind}\n"
            f"Version: {candidate.version}\n"
            f"Archive: {candidate.archive.name}\n"
            f"Declared manifest: {declared}\n"
            f"Install authorization: {approval}\n"
            f"Install root: {candidate.install_root or '—'}\n"
            f"State: {candidate.status}\n\n"
            f"Live update check:\n{upstream}\n\n"
            f"Install files:\n{file_list}",
        )

    def install_candidate(self, candidate: PackageCandidate, confirmed: bool = False):
        if not self._is_installable(candidate):
            return
        if theme_daddy.accepts(candidate.kind):
            self.install_theme_candidate(candidate)
            return
        destination = self._destination_for(candidate)
        if destination.exists() or destination.is_symlink():
            QMessageBox.warning(self, "Package Daddy", f"Refused: install destination already exists:\n{destination}\n\nDaddy will not trample an existing package.")
            return
        install_queue = [candidate]
        couple_after_install = False
        partner = self._ready_coupling_partner(candidate) if plugin_daddy.accepts(candidate.kind) else None
        if partner is not None:
            prompt = QMessageBox(self)
            prompt.setWindowTitle("Package Daddy — FW + RootDNA")
            prompt.setIcon(QMessageBox.Icon.Question)
            prompt.setText("Both File Watchdog and RootDNA are ready in Downloads.")
            prompt.setInformativeText(
                "They can stay separate, or you can explicitly couple them. Coupling only makes RootDNA appear as a File Watchdog tab; it does not merge, overwrite, or make either plugin dependent on the other."
            )
            both_coupled = prompt.addButton("Install both + couple", QMessageBox.ButtonRole.AcceptRole)
            both_separate = prompt.addButton("Install both, keep separate", QMessageBox.ButtonRole.ActionRole)
            only_this = prompt.addButton(f"Only install {candidate.name}", QMessageBox.ButtonRole.ActionRole)
            prompt.addButton(QMessageBox.StandardButton.Cancel)
            prompt.exec()
            clicked = prompt.clickedButton()
            if clicked is both_coupled:
                install_queue.append(partner); couple_after_install = True
            elif clicked is both_separate:
                install_queue.append(partner)
            elif clicked is not only_this:
                return
        source_note = (
            "Only declared files will be unpacked. Existing files are not overwritten."
            if all(item.manifest_path for item in install_queue)
            else "This approved catalog ZIP passed Daddy's structural review: one safe plugin folder and a Python entry point. Only the reviewed files will be unpacked. Existing files are not overwritten."
        )
        question = (
            "Install the following reviewed package(s)?\n\n" + "\n".join(
                f"• {item.name} {item.version}\n  {item.archive.name}\n  → {self._destination_for(item)}"
                for item in install_queue
            ) + "\n\n" + source_note
        )
        if not confirmed:
            answer = QMessageBox.question(self, "Package Daddy", question, QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel)
            if answer != QMessageBox.StandardButton.Yes:
                return
        installed_now = []
        try:
            for item in install_queue:
                item_destination = self._destination_for(item)
                if item_destination.exists() or item_destination.is_symlink():
                    raise RuntimeError(f"install destination already exists: {item_destination}")
                installer = tool_daddy if tool_daddy.accepts(item.kind) else plugin_daddy
                installer.install(item, item_destination, _write_ledger_entry)
                installed_now.append(item)
        except Exception as exc:
            logger.exception("Install bailed on %s: %s", candidate.archive, exc)
            completed = "\n".join(f"• {item.name}" for item in installed_now) or "• none"
            QMessageBox.critical(self, "Package Daddy", f"Install chain stopped. Nothing was overwritten.\n\nCompleted before the failure:\n{completed}\n\nProblem:\n{exc}")
            return
        installed_coupled_pair_member = any(item.package_id in COUPLED_PACKAGE_IDS for item in installed_now)
        for item in installed_now:
            # A successful install replaced the thing the old update check was
            # bitching about. Don't make every plugin carry a stale ❗ around.
            self._update_results.pop(item.package_id, None)
        if couple_after_install:
            self._set_fw_rootdna_coupling(True)
        elif installed_coupled_pair_member and self._fw_and_rootdna_installed():
            self._offer_coupling_choice()
        installed_names = ", ".join(item.name for item in installed_now)
        logger.info("Installed the reviewed package(s): %s", installed_names)
        removed_archives = []
        for item in installed_now:
            # The receipt, not the archive, is Daddy's uninstall authority.
            # Once both the folder and its receipt exist, keeping the consumed
            # download around is just clutter.  Stay in MO2 Downloads and only
            # delete a plain file -- no wandering off to eat someone's homework.
            try:
                if (
                    item.archive.is_file()
                    and item.archive.parent.resolve() == self.downloads.resolve()
                ):
                    item.archive.unlink()
                    self._remove_mo2_download_metadata(item.archive)
                    removed_archives.append(item.archive.name)
            except OSError as exc:
                logger.warning("Installed %s, but couldn't clean up consumed archive %s: %s", item.name, item.archive, exc)
        note = "\nFW and RootDNA were explicitly coupled." if couple_after_install else ""
        if removed_archives:
            note += "\nRemoved the consumed download" + ("s" if len(removed_archives) > 1 else "") + "."
        restart = QMessageBox(self)
        restart.setWindowTitle("Package Daddy — restart required")
        restart.setIcon(QMessageBox.Icon.Information)
        restart.setText(f"Installed {installed_names}.{note}")
        restart.setInformativeText("MO2 must restart before it can load the new plugin or theme. Daddy can request a normal MO2 shutdown, wait for it to close, then reopen the same MO2 executable. No force-killing, no surprise data loss.")
        restart_now = restart.addButton("Restart MO2 now", QMessageBox.ButtonRole.AcceptRole)
        restart.addButton("I'll restart it myself", QMessageBox.ButtonRole.ActionRole)
        restart.exec()
        if restart.clickedButton() is restart_now:
            try:
                self._request_graceful_restart()
                return
            except Exception as exc:
                logger.exception("Couldn't queue MO2 restart: %s", exc)
                QMessageBox.warning(self, "Package Daddy", f"The package installed, but Daddy could not queue a restart. Please restart MO2 yourself.\n\n{exc}")
        self.scan(force=True)

    def install_theme_candidate(self, candidate: PackageCandidate):
        """Let the user choose reviewed stylesheet payloads from one bundle."""
        try:
            choices = theme_daddy.choices(candidate.archive)
        except (OSError, zipfile.BadZipFile) as exc:
            QMessageBox.warning(self, "Theme Daddy", f"Could not review {candidate.archive.name}.\n\n{exc}")
            return
        if not choices:
            QMessageBox.warning(self, "Theme Daddy", "No safe stylesheet payload was found in this archive. Nothing was installed.")
            return
        picker = QDialog(self)
        picker.setWindowTitle(f"Theme Daddy — choose {candidate.name} payloads")
        layout = QVBoxLayout(picker)
        intro = QLabel(
            "Choose the theme payloads you want. Theme Daddy reads only paths below an archive's stylesheets folder; bundled plugins and other junk stay out of this install.",
            picker,
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)
        boxes: list[tuple[QCheckBox, theme_daddy.ThemeChoice]] = []
        for choice in choices:
            box = QCheckBox(f"{choice.label}  ({len(choice.files)} reviewed file(s))", picker)
            box.setToolTip(f"Archive payload: {choice.prefix}/stylesheets")
            layout.addWidget(box)
            boxes.append((box, choice))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel, parent=picker)
        install = buttons.addButton("Install selected", QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.rejected.connect(picker.reject)
        buttons.accepted.connect(picker.accept)
        layout.addWidget(buttons)
        picker.resize(650, min(760, 170 + len(boxes) * 30))
        if picker.exec() != QDialog.DialogCode.Accepted:
            return
        selected = tuple(choice for box, choice in boxes if box.isChecked())
        if not selected:
            QMessageBox.information(self, "Theme Daddy", "Nothing selected; nothing installed.")
            return
        stylesheets = theme_daddy.destination_root(self.mo2_root)
        labels = "\n".join(f"• {choice.label}" for choice in selected)
        answer = QMessageBox.question(
            self,
            "Theme Daddy — install selected payloads",
            f"Install these reviewed stylesheet payload(s)?\n\n{labels}\n\n→ {stylesheets}\n\nExisting files will not be overwritten. Theme Daddy will record every created file for safe removal later.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            installed_files = theme_daddy.install(
                candidate.archive, selected, stylesheets,
                lambda files, hashes: _write_theme_ledger_entry(candidate, files, hashes),
            )
        except Exception as exc:
            logger.exception("Theme install ate shit for %s: %s", candidate.name, exc)
            QMessageBox.critical(self, "Theme Daddy", f"Theme install stopped. No existing stylesheet was overwritten.\n\n{exc}")
            return
        try:
            if candidate.archive.is_file() and candidate.archive.parent.resolve() == self.downloads.resolve():
                candidate.archive.unlink()
                self._remove_mo2_download_metadata(candidate.archive)
        except OSError as exc:
            logger.warning("Installed theme %s, but couldn't clean up archive %s: %s", candidate.name, candidate.archive, exc)
        logger.info("Installed %s theme file(s) from %s", len(installed_files), candidate.name)
        self._update_results.pop(candidate.package_id, None)
        restart = QMessageBox(self)
        restart.setWindowTitle("Theme Daddy — restart required")
        restart.setIcon(QMessageBox.Icon.Information)
        restart.setText(f"Installed {candidate.name} ({len(installed_files)} stylesheet file(s)).")
        restart.setInformativeText("MO2 must restart before it sees newly installed themes. The consumed download was removed after Daddy wrote its uninstall receipt.")
        restart_now = restart.addButton("Restart MO2 now", QMessageBox.ButtonRole.AcceptRole)
        restart.addButton("I'll restart it myself", QMessageBox.ButtonRole.ActionRole)
        restart.exec()
        if restart.clickedButton() is restart_now:
            try:
                self._request_graceful_restart()
                return
            except Exception as exc:
                logger.exception("Couldn't queue MO2 restart after theme install: %s", exc)
                QMessageBox.warning(self, "Theme Daddy", f"The theme installed, but Daddy could not queue a restart. Please restart MO2 yourself.\n\n{exc}")
        self.scan(force=True)

    def uninstall_theme_candidate(self, candidate: PackageCandidate):
        receipts = _read_ledger(strict=True)
        receipt = self._theme_receipt_for(candidate, receipts)
        stylesheets = theme_daddy.destination_root(self.mo2_root)
        if receipt is None:
            QMessageBox.warning(self, "Theme Daddy", "Refused: Daddy does not have an intact receipt for this theme. Nothing was removed.")
            return
        try:
            files = theme_daddy.removable_files(receipt, stylesheets)
        except RuntimeError as exc:
            QMessageBox.warning(self, "Theme Daddy", f"Refused: {exc}\n\nNothing was removed.")
            return
        answer = QMessageBox.question(
            self,
            "Theme Daddy — uninstall",
            f"Remove {candidate.name}?\n\nDaddy will remove only its {len(files)} recorded stylesheet file(s) below:\n{stylesheets}\n\nModified files are refused, not silently blown away. If MO2 is using this theme, Daddy will switch it to another installed stylesheet first so it does not reload in default white.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            switched_to = theme_daddy.switch_active_stylesheet(
                self.ini_path,
                stylesheets,
                tuple(str(item.relative_to(stylesheets).as_posix()) for item in files),
            )
        except RuntimeError as exc:
            QMessageBox.warning(self, "Theme Daddy", f"Refused: {exc}\n\nNothing was removed.")
            return
        staging_parent = Path(tempfile.mkdtemp(prefix="theme_daddy_uninstall_", dir=stylesheets.parent))
        staged: list[tuple[Path, Path]] = []
        try:
            for source in files:
                target = staging_parent / source.relative_to(stylesheets)
                target.parent.mkdir(parents=True, exist_ok=True)
                os.replace(source, target)
                staged.append((source, target))
            updated = dict(receipts)
            updated.pop(candidate.package_id, None)
            try:
                _write_ledger(updated)
            except Exception:
                for source, target in reversed(staged):
                    source.parent.mkdir(parents=True, exist_ok=True)
                    os.replace(target, source)
                raise
            shutil.rmtree(staging_parent)
            theme_daddy.prune_empty_parents([source for source, _ in staged], stylesheets)
        except Exception as exc:
            logger.exception("Theme uninstall went sideways for %s: %s", candidate.name, exc)
            QMessageBox.critical(self, "Theme Daddy", f"Could not finish uninstalling {candidate.name}.\n\n{exc}")
            return
        logger.info("Uninstalled theme %s%s", candidate.name, f"; switched active stylesheet to {switched_to}" if switched_to else "")
        restart = QMessageBox(self)
        restart.setWindowTitle("Theme Daddy — restart required")
        restart.setIcon(QMessageBox.Icon.Information)
        restart.setText(f"Removed {candidate.name}.")
        restart.setInformativeText("MO2 must restart to forget the removed stylesheet files. Daddy can close it normally and reopen the same executable.")
        restart_now = restart.addButton("Restart MO2 now", QMessageBox.ButtonRole.AcceptRole)
        restart.addButton("I'll restart it myself", QMessageBox.ButtonRole.ActionRole)
        restart.exec()
        if restart.clickedButton() is restart_now:
            try:
                self._request_graceful_restart()
                return
            except Exception as exc:
                logger.exception("Couldn't queue MO2 restart after theme uninstall: %s", exc)
                QMessageBox.warning(self, "Theme Daddy", f"The theme was removed, but Daddy could not queue a restart. Please restart MO2 yourself.\n\n{exc}")
        self.scan(force=True)

    def update_tool_candidate(self, candidate: PackageCandidate):
        """Replace an installed tool only after one plain-English confirmation."""
        if not tool_daddy.accepts(candidate.kind) or not self._is_installable(candidate):
            return
        destination = self._destination_for(candidate)
        if not destination.is_dir() or destination.is_symlink():
            QMessageBox.warning(self, "Tool Daddy", f"Refused: the installed tool folder is unavailable:\n{destination}")
            return
        answer = QMessageBox.question(
            self,
            "Tool Daddy — update tool",
            f"Replace the installed tool with this reviewed archive?\n\n"
            f"• {candidate.name} {candidate.version}\n  {candidate.archive.name}\n  → {destination}\n\n"
            "Daddy will move the existing folder to tools\\.package-daddy-backups first. If the replacement fails, it restores the old folder. No MO2 restart is needed for an external tool.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            backup = tool_daddy.update(candidate, destination, _write_ledger_entry)
        except Exception as exc:
            logger.exception("Tool update faceplanted for %s: %s", candidate.archive, exc)
            QMessageBox.critical(self, "Tool Daddy", f"Could not update {candidate.name}. The old folder was kept or restored.\n\n{exc}")
            return
        logger.info("Updated tool %s; old folder is parked at %s", candidate.name, backup)
        # That warning described the old installed copy. Once the replacement
        # made it onto disk, leaving the ❗ there is just lying to people.
        self._update_results.pop(candidate.package_id, None)
        self._catalog_notice = f"Updated {candidate.name}. Cleared its stale update warning; the old copy is parked under Tool Daddy backups."
        QMessageBox.information(
            self, "Tool Daddy — updated",
            f"Updated {candidate.name}.\n\nThe previous copy is safely parked here:\n{backup}",
        )
        self.scan(force=True)

    def _is_owned_download(self, archive: Path) -> bool:
        """Only offer archive deletion for a plain file right in Downloads."""
        try:
            return archive.is_file() and archive.parent.resolve() == self.downloads.resolve()
        except OSError:
            return False

    def discard_download(self, candidate: PackageCandidate):
        """Delete one downloaded archive, never an installed package or some random path."""
        archive = candidate.archive
        if not self._is_owned_download(archive):
            QMessageBox.warning(self, "Package Daddy", "That archive is not a plain file in MO2 Downloads, so Daddy will leave it alone.")
            return
        answer = QMessageBox.question(
            self,
            "Package Daddy — delete download",
            f"Delete this downloaded archive?\n\n{archive.name}\n→ {archive}\n\nThis only removes the download. It does not uninstall or change any installed package.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            archive.unlink()
            self._remove_mo2_download_metadata(archive)
        except OSError as exc:
            logger.warning("Couldn't delete downloaded archive %s: %s", archive, exc)
            QMessageBox.warning(self, "Package Daddy", f"Could not delete {archive.name}.\n\n{exc}")
            return
        self._catalog_notice = f"Deleted {archive.name} from MO2 Downloads. Installed files were left alone."
        logger.info("Deleted downloaded archive %s; installed files were not touched.", archive)
        self.scan(force=True)

    def uninstall_candidate(self, candidate: PackageCandidate):
        """Remove only a complete folder Daddy can prove it installed.

        A receipt is not a blank cheque: unexpected files make Daddy stop and
        leave the folder alone so a user can inspect it instead of losing work.
        """
        receipts = _read_ledger(strict=True)
        receipt = self._receipt_for(candidate, receipts)
        if receipt is None:
            QMessageBox.warning(self, "Package Daddy", "Refused: Daddy does not have a valid receipt for this installed folder. Nothing was removed.")
            return
        destination = (
            tool_daddy.destination(self.mo2_root, receipt["install_root"])
            if receipt.get("target") == "tools"
            else self.plugins_root / receipt["install_root"]
        )
        recorded = {PurePosixPath(item).as_posix() for item in receipt["files"]}
        actual = {
            path.relative_to(destination).as_posix()
            for path in destination.rglob("*") if path.is_file() or path.is_symlink()
        }
        # Python can create bytecode after Daddy installs a plugin.  It is not
        # user content and must not permanently block a clean reversal.
        extras = {
            item for item in actual - recorded
            if not (item.startswith("__pycache__/") and item.endswith(".pyc"))
        }
        if extras:
            preview = "\n".join(f"• {item}" for item in sorted(extras)[:12])
            suffix = "\n• …" if len(extras) > 12 else ""
            QMessageBox.warning(
                self, "Package Daddy",
                f"Refused: {candidate.name}'s folder contains files Daddy did not install.\n\n{preview}{suffix}\n\nNothing was removed. Move or inspect those files first."
            )
            return
        answer = QMessageBox.question(
            self, "Package Daddy — uninstall",
            f"Remove {candidate.name} from MO2?\n\nDaddy will remove only this folder:\n{destination}\n\nIt matches Daddy's install receipt ({len(recorded)} recorded file(s)). This cannot be undone from Daddy.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        staging_parent = Path(tempfile.mkdtemp(prefix="package_daddy_uninstall_", dir=self.plugins_root.parent))
        staged = staging_parent / destination.name
        try:
            os.replace(destination, staged)
            updated = dict(receipts)
            updated.pop(candidate.package_id, None)
            try:
                _write_ledger(updated)
            except Exception:
                os.replace(staged, destination)
                raise
            shutil.rmtree(staged)
            shutil.rmtree(staging_parent, ignore_errors=True)
        except Exception as exc:
            logger.exception("Uninstall bailed on %s: %s", candidate.name, exc)
            QMessageBox.critical(self, "Package Daddy", f"Could not finish uninstalling {candidate.name}.\n\n{exc}\n\nDaddy left the original plugin folder in place whenever it could.")
            return
        logger.info("Uninstalled %s from %s", candidate.name, destination)
        restart = QMessageBox(self)
        restart.setWindowTitle("Package Daddy — restart required")
        restart.setIcon(QMessageBox.Icon.Information)
        restart.setText(f"Removed {candidate.name}.")
        restart.setInformativeText("MO2 must restart to unload the old plugin code. Daddy can request a normal shutdown, wait for MO2 to close, then reopen the same executable. No force-killing, no surprise nonsense.")
        restart_now = restart.addButton("Restart MO2 now", QMessageBox.ButtonRole.AcceptRole)
        restart.addButton("I'll restart it myself", QMessageBox.ButtonRole.ActionRole)
        restart.exec()
        if restart.clickedButton() is restart_now:
            try:
                self._request_graceful_restart()
                return
            except Exception as exc:
                logger.exception("Couldn't queue MO2 restart after uninstall: %s", exc)
                QMessageBox.warning(self, "Package Daddy", f"The package was removed, but Daddy could not queue a restart. Please restart MO2 yourself.\n\n{exc}")
        self.scan(force=True)

    def _request_graceful_restart(self):
        """Ask MO2 to quit normally, then relaunch it after this PID actually exits.

        Unlike Plugin Finder's taskkill approach, this never forces the process
        down. MO2 gets its normal close path (and any own save prompts) first.
        """
        application = QCoreApplication.instance()
        executable = Path(QCoreApplication.applicationFilePath())
        if application is None or not executable.is_file():
            raise RuntimeError("MO2 executable path is unavailable")
        pid = os.getpid()
        safe_executable = str(executable).replace('"', '""')
        script = (
            "@echo off\r\n"
            f"set \"MO2_PID={pid}\"\r\n"
            ":wait_for_mo2\r\n"
            "tasklist /FI \"PID eq %MO2_PID%\" /NH | findstr /R /C:\"[ ]%MO2_PID%[ ]\" >nul\r\n"
            "if not errorlevel 1 (\r\n"
            "  timeout /t 1 /nobreak >nul\r\n"
            "  goto wait_for_mo2\r\n"
            ")\r\n"
            f"start \"\" \"{safe_executable}\"\r\n"
            "del \"%~f0\"\r\n"
        )
        descriptor, helper_name = tempfile.mkstemp(prefix="package_daddy_restart_", suffix=".cmd")
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
                handle.write(script)
            subprocess.Popen(["cmd.exe", "/d", "/c", helper_name], close_fds=True)
        except Exception:
            try: os.unlink(helper_name)
            except OSError: pass
            raise
        logger.info("Queued a normal MO2 restart through %s", helper_name)
        QTimer.singleShot(0, application.quit)

    def _ready_coupling_partner(self, candidate):
        if candidate.package_id not in COUPLED_PACKAGE_IDS:
            return None
        wanted = next(item for item in COUPLED_PACKAGE_IDS if item != candidate.package_id)
        for item in self._candidates:
            if item.package_id == wanted and self._is_installable(item):
                destination = plugin_daddy.destination(self.mo2_root, item.install_root)
                if not destination.exists() and not destination.is_symlink():
                    return item
        return None

    def _find_installed_package_dir(self, package_id):
        try:
            for folder in self.plugins_root.iterdir():
                manifest = folder / MANIFEST_NAME
                if manifest.is_file():
                    data = json.loads(manifest.read_text(encoding="utf-8"))
                    if data.get("id") == package_id:
                        return folder
        except (OSError, ValueError, TypeError):
            return None
        return None

    def _fw_and_rootdna_installed(self):
        rootdna = self._find_installed_package_dir("root-dna")
        return bool(rootdna and (rootdna / ROOTDNA_MARKER).is_file() and self._find_installed_package_dir("file-watchdog"))

    def _set_fw_rootdna_coupling(self, coupled):
        rootdna = self._find_installed_package_dir("root-dna")
        if rootdna is None or not (rootdna / ROOTDNA_MARKER).is_file():
            raise RuntimeError("RootDNA is not installed with its declared module marker; coupling was not changed")
        path = rootdna / ROOTDNA_COUPLING
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps({"format": 1, "coupled_with_file_watchdog": bool(coupled)}, indent=2), encoding="utf-8")
        os.replace(temporary, path)

    def _offer_coupling_choice(self):
        prompt = QMessageBox(self)
        prompt.setWindowTitle("Package Daddy — Couple FW + RootDNA?")
        prompt.setIcon(QMessageBox.Icon.Question)
        prompt.setText("File Watchdog and RootDNA are both installed.")
        prompt.setInformativeText("Want File Watchdog to show RootDNA as an extra tab? They remain separate plugins either way, and you can change this later at the top of either tool.")
        couple = prompt.addButton("Couple them", QMessageBox.ButtonRole.AcceptRole)
        separate = prompt.addButton("Keep separate", QMessageBox.ButtonRole.ActionRole)
        prompt.exec()
        if prompt.clickedButton() is couple:
            self._set_fw_rootdna_coupling(True)
        elif prompt.clickedButton() is separate:
            self._set_fw_rootdna_coupling(False)

class PackageDaddyPlugin(mobase.IPluginTool):
    def __init__(self):
        super().__init__()
        self._organizer = None

    def init(self, organizer):
        self._organizer = organizer
        logger.info("Package Daddy booted. Downloads stay hands-off until an install gets approved.")
        return True

    def name(self): return "PackageDaddy"
    def author(self): return "SkyGen"
    def description(self): return "Review, install, and remove safely packaged MO2 plugins and themes from Downloads."
    def version(self): return mobase.VersionInfo("0.3.4", mobase.VersionScheme.DISCOVER)
    def isActive(self): return True
    def settings(self): return []
    def displayName(self): return "Package Daddy"
    def tooltip(self): return "Find declared plugins and themes in MO2 Downloads"
    def icon(self): return QIcon(str(PACKAGE_DADDY_ICON))
    def group(self): return mobase.PluginSettingGroup.Tools

    def display(self):
        try:
            if self._organizer is None:
                raise RuntimeError("MO2 handed Package Daddy an empty leash somehow.")
            value = self._organizer.downloadsPath()
            if hasattr(value, "absolutePath"):
                value = value.absolutePath()
            downloads = Path(str(value).strip()).resolve()
            logger.info("MO2 handed Package Daddy this Downloads path: %s", downloads)
            mo2_version = self._organizer.appVersion().canonicalString()
            paths = daddy_paths.resolve(self._organizer)
            PackageDaddyWindow(downloads, paths.install_root, mo2_version, self._organizer, paths.ini_path).exec()
        except Exception as exc:
            logger.exception("Package Daddy window shit the bed: %s", exc)


def createPlugin():
    return PackageDaddyPlugin()
