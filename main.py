#!/usr/bin/env python3
"""
Mission Generator — PCD 点云可视化与 ROS 任务点生成器

A cross-platform desktop application for:
1. Visualizing PCD point cloud files in 3D
2. Selecting mission waypoints on the point cloud
3. Generating ROS1 launch files compatible with mission_manger.cpp

Usage:
    python main.py
"""

import sys
import os
import signal

# Ensure the project root is on the Python path
_project_root = os.path.dirname(os.path.abspath(__file__))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt, QTimer

from ui.main_window import MainWindow


def _get_icon_path() -> str:
    """Find icon.png — works both from source and inside PyInstaller bundle."""
    # When running from PyInstaller onefile, sys._MEIPASS is the temp dir
    # where bundled data files are extracted.
    bundle_dir = getattr(sys, '_MEIPASS', None)
    search_dirs = []
    if bundle_dir:
        search_dirs.append(bundle_dir)
    search_dirs.append(_project_root)

    for d in search_dirs:
        p = os.path.join(d, "icon.png")
        if os.path.exists(p):
            return p
    return ""


def main():
    """Application entry point."""

    # Enable high-DPI scaling
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )

    app = QApplication(sys.argv)
    app.setApplicationName("Mission Generator")
    app.setOrganizationName("UAV-Mission")

    # Set application icon
    _icon_path = _get_icon_path()
    if _icon_path:
        from PySide6.QtGui import QIcon
        icon = QIcon(_icon_path)
        app.setWindowIcon(icon)
    else:
        print("[app] Warning: icon.png not found", file=sys.stderr)

    # Apply dark theme stylesheet
    app.setStyle("Fusion")

    window = MainWindow()
    window.show()

    # ── Safe cleanup hook ────────────────────────────────────────
    # QApplication.aboutToQuit fires AFTER all windows close but BEFORE
    # Qt starts destroying widgets / GL contexts.  This is the only
    # safe time to detach vispy GPU resources.
    app.aboutToQuit.connect(window._canvas.cleanup)

    # ── Ctrl+C (SIGINT) handling ──────────────────────────────────
    # Qt's event loop intercepts SIGINT, so Ctrl+C won't work by
    # default. We use a QTimer to periodically check a flag set by
    # the SIGINT handler and call quit() from the main thread.
    _sigint_received = False

    def _sigint_handler(signum, frame):
        nonlocal _sigint_received
        _sigint_received = True
        # If called a second time, force exit
        if _sigint_received:
            print("\nCtrl+C — forcing exit...", file=sys.stderr)
            os._exit(1)

    # Install handler BEFORE entering event loop
    original_handler = signal.signal(signal.SIGINT, _sigint_handler)

    def _check_sigint():
        """Called periodically by QTimer; quits if SIGINT was received."""
        if _sigint_received:
            print("\nCtrl+C — shutting down...", file=sys.stderr)
            window.setProperty("force_close", True)  # skip confirmation
            app.quit()

    timer = QTimer()
    timer.timeout.connect(_check_sigint)
    timer.start(200)  # Check every 200ms
    # Store references to prevent GC
    app._sigint_timer = timer
    app._sigint_handler = _sigint_handler

    # Run the event loop
    exit_code = app.exec()

    # Restore original handler
    signal.signal(signal.SIGINT, original_handler)

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
