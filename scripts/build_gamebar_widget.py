"""Build the Xbox Game Bar widget (gamebar-widget/) and install it for the current user.

Needs Visual Studio with the "WinUI application development" workload and its
"Universal Windows Platform tools", and Windows Developer Mode turned on:

    .venv\\Scripts\\python scripts\\build_gamebar_widget.py

The Release build (.NET Native) is packaged unsigned, so instead of installing
the package, its contents are unpacked to gamebar-widget\\bin\\x64\\Release\\Layout
and registered from there, which Developer Mode allows without a certificate.
Run it again after changing the widget: the new build replaces the old one.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WIDGET = ROOT / "gamebar-widget"
PACKAGE_NAME = "ChessLens.GameBarWidget"  # Identity Name in Package.appxmanifest
LAYOUT = WIDGET / "bin" / "x64" / "Release" / "Layout"
VSWHERE = Path(r"C:\Program Files (x86)\Microsoft Visual Studio\Installer\vswhere.exe")


def main() -> int:
    msbuild = find_msbuild()
    if msbuild is None:
        print("MSBuild with the UWP tools wasn't found. In Visual Studio Installer, add the 'WinUI application "
              "development' workload with 'Universal Windows Platform tools'.", file=sys.stderr)
        return 1
    if not developer_mode():
        print("Developer Mode is off: turn it on in Settings > System > Advanced, then run this again.", file=sys.stderr)
        return 1
    subprocess.run(
        [str(msbuild), "ChessLensWidget.csproj", "/restore", "/p:Configuration=Release", "/p:Platform=x64", "/v:minimal", "/nologo"],
        cwd=WIDGET,
        check=True,
    )
    package = newest_package()
    stop_widget()
    if LAYOUT.exists():
        shutil.rmtree(LAYOUT)
    with zipfile.ZipFile(package) as z:
        for name in z.namelist():
            if name not in ("AppxBlockMap.xml", "[Content_Types].xml"):  # packaging metadata, not the app
                z.extract(name, LAYOUT)
    powershell(f"Add-AppxPackage -Register '{LAYOUT / 'AppxManifest.xml'}' -ForceApplicationShutdown")
    print(f"Installed the ChessLens widget from {LAYOUT}. Open Game Bar (Win+G) > Widgets > ChessLens analysis.")
    return 0


def find_msbuild() -> Path | None:
    if not VSWHERE.exists():
        return None
    found = subprocess.run(
        [str(VSWHERE), "-latest", "-requires", "Microsoft.VisualStudio.ComponentGroup.UWP.Support",
         "-find", r"MSBuild\**\Bin\MSBuild.exe"],
        capture_output=True, text=True,
    ).stdout.splitlines()
    return Path(found[0]) if found else None


def developer_mode() -> bool:
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\AppModelUnlock") as key:
            return winreg.QueryValueEx(key, "AllowDevelopmentWithoutDevLicense")[0] == 1
    except OSError:
        return False


def newest_package() -> Path:
    packages = sorted((WIDGET / "AppPackages").glob("*_x64_Test/*_x64.msix"), key=lambda p: p.stat().st_mtime)
    if not packages:
        raise SystemExit("The build made no package under gamebar-widget\\AppPackages.")
    return packages[-1]


def stop_widget() -> None:
    """Close a running widget so its files can be replaced (Game Bar starts it again when needed)."""
    powershell(f"Get-AppxPackage {PACKAGE_NAME} | ForEach-Object {{ Get-Process | Where-Object Path -like \"$($_.InstallLocation)*\" | Stop-Process -Force }}")


def powershell(command: str) -> None:
    subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", command], check=True)


if __name__ == "__main__":
    sys.exit(main())
