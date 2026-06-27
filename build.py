#!/usr/bin/env python3
"""
PyInstaller build script for Mission Generator.

Usage:
    python build.py          # Build for current platform
    python build.py --clean  # Clean build (remove build/dist)

Output:
    dist/MissionGenerator     (Linux)
    dist/MissionGenerator.exe (Windows)
    dist/MissionGenerator.app (macOS)
"""

import sys
import os
import shutil
import platform
import subprocess

# Project paths
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
MAIN_SCRIPT = os.path.join(PROJECT_ROOT, "main.py")
ICON_PNG = os.path.join(PROJECT_ROOT, "icon.png")
NAME = "MissionGenerator"

# Platform-specific settings
SYSTEM = platform.system()

# Path separator for --add-data: ':' on Linux/macOS, ';' on Windows
_SEP = ';' if SYSTEM == 'Windows' else ':'


def _generate_ico():
    """Generate icon.ico from icon.png for Windows builds."""
    ico_path = os.path.join(PROJECT_ROOT, "icon.ico")
    if os.path.exists(ico_path):
        return ico_path
    try:
        from PIL import Image
        img = Image.open(ICON_PNG)
        # Save as .ico with multiple sizes for best results
        sizes = [(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)]
        img.save(ico_path, format='ICO', sizes=sizes)
        print(f"[build] Generated icon.ico ({sizes})")
        return ico_path
    except ImportError:
        print("[build] PIL not available — skipping .ico generation, using .png")
        return None


def build():
    """Run PyInstaller to create a standalone executable."""

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--name", NAME,
        "--onefile",
        "--noconsole",
        "--clean",
        # Include icon.png explicitly for runtime window icon
        "--add-data", f"{ICON_PNG}{_SEP}.",
        # Include core packages as data
        "--add-data", f"{os.path.join(PROJECT_ROOT, 'core')}{_SEP}core",
        "--add-data", f"{os.path.join(PROJECT_ROOT, 'ui')}{_SEP}ui",
        "--add-data", f"{os.path.join(PROJECT_ROOT, 'ros_utils')}{_SEP}ros_utils",
        # Hidden imports for vispy
        "--hidden-import", "vispy",
        "--hidden-import", "vispy.app.backends._pyside6",
        "--hidden-import", "vispy.scene",
        "--hidden-import", "vispy.visuals",
        "--hidden-import", "vispy.gloo",
        # Collect vispy data (shaders, fonts)
        "--collect-data", "vispy",
        # Exclude unnecessary large packages
        "--exclude-module", "matplotlib",
        "--exclude-module", "scipy",
        "--exclude-module", "pandas",
        "--exclude-module", "PIL",
        "--exclude-module", "tkinter",
        MAIN_SCRIPT,
    ]

    # Platform-specific additions
    if SYSTEM == "Windows":
        # Generate .ico from .png for Windows executable icon
        ico = _generate_ico()
        if ico:
            cmd.insert(3, f"--icon={ico}")
        else:
            # PyInstaller on Windows also accepts .png
            cmd.insert(3, f"--icon={ICON_PNG}")
        cmd.extend([
            "--add-binary",
            f"{sys.prefix}/Library/bin/Qt6OpenGL.dll{_SEP}.",
        ])
    elif SYSTEM == "Darwin":  # macOS
        cmd.insert(3, f"--icon={ICON_PNG}")
        cmd.extend([
            "--osx-bundle-identifier", "com.uav.missiongenerator",
        ])

    print(f"[build] Platform: {SYSTEM}")
    print(f"[build] Running: PyInstaller --onefile ...")
    print()

    result = subprocess.run(cmd, cwd=PROJECT_ROOT)
    if result.returncode != 0:
        print("[build] FAILED!")
        sys.exit(result.returncode)

    # ── Post-build: copy .desktop file and install script for Linux ──
    if SYSTEM == "Linux":
        _create_linux_desktop_file()

    print()
    print(f"[build] SUCCESS! Output: dist/{NAME}")
    if SYSTEM == "Windows":
        print(f"[build] Executable: dist/{NAME}.exe")
    elif SYSTEM == "Linux":
        print(f"[build] Executable: dist/{NAME}")
        print(f"[build] Desktop file: dist/{NAME}.desktop")
        print(f"[build] Install script: dist/install.sh")


def _create_linux_desktop_file():
    """Create a .desktop file and install script for Linux."""
    desktop = f"""[Desktop Entry]
Version=1.0
Name=Mission Generator
Name[zh_CN]=任务点生成器
Comment=PCD Point Cloud Visualizer and ROS Mission Waypoint Generator
Comment[zh_CN]=PCD 点云可视化与 ROS 任务航点生成工具
Exec={NAME}
Icon={NAME}
Terminal=false
Type=Application
Categories=Science;Robotics;Visualization;
StartupNotify=true
"""
    desktop_path = os.path.join(PROJECT_ROOT, "dist", f"{NAME}.desktop")
    with open(desktop_path, 'w') as f:
        f.write(desktop)

    # Install script
    install_sh = f"""#!/bin/bash
# Mission Generator — Linux install script
# Install the executable and desktop integration files

set -e

BINDIR="$HOME/.local/bin"
APPDIR="$HOME/.local/share/applications"
ICONDIR="$HOME/.local/share/icons/hicolor/256x256/apps"

echo "Installing Mission Generator..."
mkdir -p "$BINDIR" "$APPDIR" "$ICONDIR"

# Copy executable
cp "{NAME}" "$BINDIR/{NAME}"
chmod +x "$BINDIR/{NAME}"

# Copy icon (bundled PNG, extracted at runtime to ~/.local/share/icons)
cp "icon.png" "$ICONDIR/{NAME}.png" 2>/dev/null || echo "  (icon will be extracted on first run)"

# Install .desktop file
cp "{NAME}.desktop" "$APPDIR/{NAME}.desktop"
update-desktop-database "$APPDIR" 2>/dev/null || true

echo ""
echo "✅ Mission Generator installed!"
echo "   Run: {NAME}"
echo "   Or find it in your application launcher."
"""
    install_path = os.path.join(PROJECT_ROOT, "dist", "install.sh")
    with open(install_path, 'w') as f:
        f.write(install_sh)
    os.chmod(install_path, 0o755)

    # Also copy icon to dist/ for the install script
    import shutil
    shutil.copy2(ICON_PNG, os.path.join(PROJECT_ROOT, "dist", "icon.png"))

    print("[build] Created .desktop file and install script")


def clean():
    """Remove build artifacts."""
    for d in ["build", "dist"]:
        path = os.path.join(PROJECT_ROOT, d)
        if os.path.exists(path):
            shutil.rmtree(path)
            print(f"[clean] Removed: {d}")

    spec_file = os.path.join(PROJECT_ROOT, f"{NAME}.spec")
    if os.path.exists(spec_file):
        os.remove(spec_file)
        print(f"[clean] Removed: {NAME}.spec")


if __name__ == "__main__":
    if "--clean" in sys.argv:
        clean()
    else:
        if "--help" in sys.argv or "-h" in sys.argv:
            print(__doc__)
            sys.exit(0)
        build()
