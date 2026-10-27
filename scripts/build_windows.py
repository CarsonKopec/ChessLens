"""Build ChessLens for Windows: a PyInstaller folder build, licence notices, and an Inno Setup installer.

    .venv\\Scripts\\python -m pip install -e ".[build]"
    .venv\\Scripts\\python scripts\\build_windows.py

Writes dist\\ChessLens\\ (ChessLens.exe and its files) and, if Inno Setup 6 is
installed, dist\\ChessLens-Setup-<version>.exe. Stockfish is not bundled: it is
GPL-licensed and installed separately (the installer can run winget for it).
"""

from __future__ import annotations

import importlib.metadata
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
BUILD = ROOT / "build"
APP_DIR = DIST / "ChessLens"

# Distributions bundled into the app whose licences must travel with it.
BUNDLED = ["PySide6_Essentials", "shiboken6", "chess", "opencv-python", "numpy", "mss", "pywin32"]
ISCC_CANDIDATES = [
    Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Inno Setup 6" / "ISCC.exe",
    Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Inno Setup 6" / "ISCC.exe",
    Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Inno Setup 6" / "ISCC.exe",
]


def main() -> int:
    sys.path.insert(0, str(ROOT / "src"))
    from chesslens import __version__

    build_app()
    write_licences(APP_DIR / "licenses")
    installer = build_installer(__version__)
    print(f"\nApp folder: {APP_DIR}")
    print(f"Installer:  {installer or 'not built (Inno Setup 6 not found)'}")
    return 0


def build_app() -> None:
    import PyInstaller.__main__

    sep = os.pathsep
    PyInstaller.__main__.run(
        [
            str(ROOT / "src" / "chesslens" / "__main__.py"),
            "--name=ChessLens",
            "--windowed",
            "--noconfirm",
            "--clean",
            f"--icon={ROOT / 'assets' / 'chesslens.ico'}",
            f"--paths={ROOT / 'src'}",
            f"--distpath={DIST}",
            f"--workpath={BUILD / 'pyinstaller'}",
            f"--specpath={BUILD}",
            f"--add-data={ROOT / 'config' / 'default.toml'}{sep}config",
            f"--add-data={ROOT / 'assets' / 'chesslens.ico'}{sep}assets",
            f"--add-data={ROOT / 'docs'}{sep}docs",
            f"--add-data={ROOT / 'README.md'}{sep}.",
            # Imported inside functions; listed so the analysis can't miss them.
            "--hidden-import=chesslens.ui.main_window",
            "--hidden-import=chesslens.selftest",
            "--exclude-module=tkinter",
            "--exclude-module=pytest",
            "--exclude-module=PySide6.QtWebEngineCore",
            "--exclude-module=PySide6.QtWebEngineWidgets",
            "--exclude-module=PySide6.QtQuick",
            "--exclude-module=PySide6.QtQml",
            "--exclude-module=PySide6.Qt3DCore",
        ]
    )


def write_licences(folder: Path) -> None:
    """Copy each bundled library's licence files and write a summary."""
    if folder.exists():
        shutil.rmtree(folder)
    folder.mkdir(parents=True)
    summary = [
        "ChessLens bundles the libraries below. Their licence texts are in the folders next to this file.",
        "Stockfish (GPL-3.0) is not bundled; it is a separate program installed by the user.",
        "",
    ]
    for name in BUNDLED:
        try:
            dist = importlib.metadata.distribution(name)
        except importlib.metadata.PackageNotFoundError:
            summary.append(f"{name}: not installed in the build environment")
            continue
        meta = dist.metadata
        licence = meta.get("License-Expression") or meta.get("License") or ""
        licence = licence.splitlines()[0][:80] if licence else "see licence files"
        summary.append(f"{meta['Name']} {dist.version}: {licence}  ({meta.get('Home-page') or meta.get('Project-URL', '')})")
        target = folder / meta["Name"]
        for file in dist.files or []:
            if file.name.upper().startswith(("LICENSE", "LICENCE", "COPYING", "NOTICE")):
                source = Path(file.locate())
                if source.is_file():
                    target.mkdir(exist_ok=True)
                    shutil.copy2(source, target / file.name)
    (folder / "THIRD_PARTY_NOTICES.txt").write_text("\n".join(summary) + "\n", encoding="utf-8")


def build_installer(version: str) -> Path | None:
    iscc = next((p for p in ISCC_CANDIDATES if p.is_file()), None)
    if iscc is None:
        return None
    subprocess.run(
        [
            str(iscc),
            f"/DAppVersion={version}",
            f"/DSourceDir={APP_DIR}",
            f"/DOutputDir={DIST}",
            f"/DIconFile={ROOT / 'assets' / 'chesslens.ico'}",
            f"/DInfoFile={ROOT / 'packaging' / 'before-install.txt'}",
            str(ROOT / "packaging" / "chesslens.iss"),
        ],
        check=True,
    )
    return DIST / f"ChessLens-Setup-{version}.exe"


if __name__ == "__main__":
    raise SystemExit(main())
