"""Package Daddy: inspect explicitly packaged MO2 additions in Downloads."""
from __future__ import annotations

import json
import logging
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

import mobase
from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtGui import QDesktopServices, QIcon
from PyQt6.QtWidgets import (QAbstractItemView, QDialog, QHBoxLayout, QLabel,
                             QPushButton, QTableWidget, QTableWidgetItem,
                             QVBoxLayout)


PLUGIN_DIR = Path(__file__).parent
MANIFEST_NAME = "package_daddy.package.json"
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


def _safe_relative(value: str) -> bool:
    path = PurePosixPath(value.replace("\\", "/"))
    return bool(value) and not path.is_absolute() and ".." not in path.parts and ":" not in value


def _manifest_member(names: list[str]) -> str | None:
    """Allow the manifest at archive root or under one wrapper folder."""
    matches = []
    for name in names:
        parts = PurePosixPath(name).parts
        if parts and parts[-1].lower() == MANIFEST_NAME and len(parts) <= 2:
            matches.append(name)
    return matches[0] if len(matches) == 1 else None


def _legacy_candidate(archive: Path, names: list[str]) -> PackageCandidate | None:
    """Conservative old-package hint; this is never install authority."""
    relative = [PurePosixPath(name) for name in names if name and not name.endswith("/")]
    # A Python utility buried in Tools/, fomod/, or some mod author's build
    # leftovers is not an MO2 plugin.  Old MO2 Python plugins reliably expose
    # a package initializer at archive root or one wrapper folder below it.
    initializers = [path for path in relative if path.name == "__init__.py" and len(path.parts) <= 2]
    stylesheets = [path for path in relative if path.suffix.lower() in {".qss", ".css"} and len(path.parts) <= 2]
    if not initializers and not stylesheets:
        return None
    kind = "possible plugin/theme" if initializers and stylesheets else ("possible MO2 plugin" if initializers else "possible MO2 theme")
    source = initializers[0] if initializers else stylesheets[0]
    label = source.parent.name if str(source.parent) not in {".", ""} else archive.stem
    return PackageCandidate(archive, label, "", kind, "—", "legacy candidate — plugin-shaped archive; review before install")


def _parse_manifest(archive: Path) -> PackageCandidate | None:
    """Read ZIP names plus a tiny manifest. Never extract or execute archive code."""
    try:
        with zipfile.ZipFile(archive) as bundle:
            member = _manifest_member(bundle.namelist())
            if member is None:
                return _legacy_candidate(archive, bundle.namelist())
            raw = json.loads(bundle.read(member).decode("utf-8"))
            package_id = raw.get("id", "")
            name = raw.get("name", "")
            kind = raw.get("kind", "")
            version = raw.get("version", "")
            files = raw.get("files")
            if raw.get("format") != 1:
                status = "unsupported manifest format"
            elif not re.fullmatch(r"[a-z0-9][a-z0-9_-]{1,63}", package_id):
                status = "invalid package id"
            elif not isinstance(name, str) or not name.strip() or kind not in {"mo2-plugin", "mo2-theme", "mo2-bridge"}:
                status = "invalid package metadata"
            elif not isinstance(version, str) or not version.strip():
                status = "missing version"
            elif not isinstance(files, list) or not files or not all(isinstance(item, str) and _safe_relative(item) for item in files):
                status = "unsafe or missing file list"
            else:
                wrapper = str(PurePosixPath(member).parent)
                prefix = "" if wrapper == "." else wrapper + "/"
                archive_names = set(bundle.namelist())
                missing = [item for item in files if prefix + item not in archive_names]
                status = "ready for install review" if not missing else "declared file missing: " + missing[0]
            return PackageCandidate(archive, str(name), str(package_id), str(kind), str(version), status, member)
    except (OSError, zipfile.BadZipFile, UnicodeDecodeError, ValueError, TypeError) as exc:
        logger.warning("Could not inspect %s: %s", archive, exc)
        return None


class PackageDaddyWindow(QDialog):
    HEADERS = ("Package", "Kind", "Version", "Archive", "Status")

    def __init__(self, downloads: Path):
        super().__init__()
        self.downloads = downloads
        self.setWindowTitle("Package Daddy — Downloads Package Manager")
        self.resize(1200, 620)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Packages misbehaving in Downloads? Daddy puts them where they belong."))
        layout.addWidget(QLabel("Read-only scan: declared packages plus review-only legacy candidates. Nothing installs until you say so."))
        self.table = QTableWidget(0, len(self.HEADERS), self)
        self.table.setHorizontalHeaderLabels(self.HEADERS)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setColumnWidth(0, 260)
        self.table.setColumnWidth(1, 150)
        self.table.setColumnWidth(2, 110)
        self.table.setColumnWidth(3, 420)
        self.table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.table)
        self.status = QLabel("Scan MO2 Downloads to find packages.", self)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        buttons = QHBoxLayout()
        scan = QPushButton("Scan Downloads", self)
        scan.clicked.connect(self.scan)
        buttons.addWidget(scan)
        open_downloads = QPushButton("Open Downloads", self)
        open_downloads.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.downloads))))
        buttons.addWidget(open_downloads)
        buttons.addStretch(1)
        close = QPushButton("Close", self)
        close.clicked.connect(self.accept)
        buttons.addWidget(close)
        layout.addLayout(buttons)
        self.scan()

    def scan(self):
        self.table.setRowCount(0)
        if not self.downloads.is_dir():
            self.status.setText("MO2 handed Package Daddy a Downloads path that does not exist. Nothing was created.")
            return
        archives = sorted(self.downloads.glob("*.zip"), key=lambda item: item.name.lower())
        candidates = [candidate for archive in archives if (candidate := _parse_manifest(archive)) is not None]
        for candidate in candidates:
            row = self.table.rowCount()
            self.table.insertRow(row)
            values = (candidate.name, candidate.kind, candidate.version, candidate.archive.name, candidate.status)
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(str(candidate.archive) if column == 3 else value)
                self.table.setItem(row, column, item)
        declared = sum(bool(candidate.manifest_path) for candidate in candidates)
        legacy = len(candidates) - declared
        self.status.setText(f"Checked {len(archives)} ZIP archive(s) in MO2 Downloads. Found {declared} declared package(s) and {legacy} possible legacy plugin/theme package(s). Archive contents were not extracted or run; no files were changed.")


class PackageDaddyPlugin(mobase.IPluginTool):
    def __init__(self):
        super().__init__()
        self._organizer = None

    def init(self, organizer):
        self._organizer = organizer
        logger.info("Package Daddy booted. Downloads stay read-only until an install is approved.")
        return True

    def name(self): return "PackageDaddy"
    def author(self): return "SkyGen"
    def description(self): return "Find explicitly packaged MO2 plugins and themes in Downloads."
    def version(self): return mobase.VersionInfo("0.1.0", mobase.VersionScheme.DISCOVER)
    def isActive(self): return True
    def settings(self): return []
    def displayName(self): return "Package Daddy"
    def tooltip(self): return "Find declared plugins and themes in MO2 Downloads"
    def icon(self): return QIcon()
    def group(self): return mobase.PluginSettingGroup.Tools

    def display(self):
        try:
            if self._organizer is None:
                raise RuntimeError("MO2 handed Package Daddy an empty leash somehow.")
            value = self._organizer.downloadsPath()
            if hasattr(value, "absolutePath"):
                value = value.absolutePath()
            downloads = Path(str(value).strip()).resolve()
            PackageDaddyWindow(downloads).exec()
        except Exception as exc:
            logger.exception("Package Daddy window failed: %s", exc)


def createPlugin():
    return PackageDaddyPlugin()
