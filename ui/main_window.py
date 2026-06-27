"""
Main application window.

Integrates the 3D point cloud canvas, waypoint panel, and launch dialog
into a cohesive PySide6 QMainWindow.
"""

import os
import sys
from typing import Optional

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QSplitter, QVBoxLayout, QHBoxLayout,
    QToolBar, QFileDialog, QMessageBox, QStatusBar, QLabel,
    QApplication, QPushButton, QDialog, QFormLayout,
    QDoubleSpinBox, QComboBox, QCheckBox, QSlider, QGroupBox,
    QDialogButtonBox,
)

from PySide6.QtGui import QIcon

from core.pcd_parser import read_pcd, PCDData
from core.waypoint_manager import WaypointManager, Waypoint
from ui.point_cloud_canvas import (
    PointCloudCanvas, WaypointAddDialog, PATH_COLOR_PRESETS,
)
from ui.waypoint_panel import WaypointPanel
from ui.launch_dialog import LaunchDialog


# ── Rendering Settings Dialog ─────────────────────────────────────

class RenderingDialog(QDialog):
    """Dialog for adjusting point cloud and marker rendering."""

    def __init__(self, canvas: PointCloudCanvas, parent=None):
        super().__init__(parent)
        self._canvas = canvas
        self.setWindowTitle("渲染设置")
        self.setMinimumWidth(400)

        layout = QVBoxLayout(self)

        # ── Point Cloud ────────────────────────────────────────
        pcd_group = QGroupBox("点云渲染")
        pcd_form = QFormLayout(pcd_group)

        # Point size
        self._size_slider = QSlider(Qt.Horizontal)
        self._size_slider.setRange(1, 20)
        self._size_slider.setValue(int(canvas.point_size))
        self._size_label = QLabel(f"{canvas.point_size:.0f} px")
        self._size_slider.valueChanged.connect(
            lambda v: self._size_label.setText(f"{v:.0f} px")
        )
        size_row = QHBoxLayout()
        size_row.addWidget(self._size_slider)
        size_row.addWidget(self._size_label)
        pcd_form.addRow("点大小:", size_row)

        # Opacity
        self._opacity_slider = QSlider(Qt.Horizontal)
        self._opacity_slider.setRange(5, 100)  # 5% - 100%
        self._opacity_slider.setValue(int(canvas.point_opacity * 100))
        self._opacity_label = QLabel(f"{canvas.point_opacity:.0%}")
        self._opacity_slider.valueChanged.connect(
            lambda v: self._opacity_label.setText(f"{v}%")
        )
        opacity_row = QHBoxLayout()
        opacity_row.addWidget(self._opacity_slider)
        opacity_row.addWidget(self._opacity_label)
        pcd_form.addRow("透明度:", opacity_row)

        # Color mode
        self._color_combo = QComboBox()
        self._color_combo.addItems([
            "Z 高度 (蓝→红)", "白色", "青色", "绿色", "橙色"
        ])
        idx = self._color_combo.findText(canvas.color_mode)
        if idx >= 0:
            self._color_combo.setCurrentIndex(idx)
        pcd_form.addRow("颜色模式:", self._color_combo)

        layout.addWidget(pcd_group)

        # ── Markers ────────────────────────────────────────────
        marker_group = QGroupBox("航点 / 航线渲染")
        marker_form = QFormLayout(marker_group)

        # Waypoint size
        self._wp_size_spin = QDoubleSpinBox()
        self._wp_size_spin.setRange(4, 50)
        self._wp_size_spin.setValue(canvas.waypoint_size)
        self._wp_size_spin.setSuffix(" px")
        marker_form.addRow("航点大小:", self._wp_size_spin)

        # Path color
        self._path_color_combo = QComboBox()
        self._path_color_combo.addItems(list(PATH_COLOR_PRESETS.keys()))
        idx = self._path_color_combo.findText(canvas.path_color_name)
        if idx >= 0:
            self._path_color_combo.setCurrentIndex(idx)
        marker_form.addRow("航线颜色:", self._path_color_combo)

        # Path width
        self._path_width_spin = QDoubleSpinBox()
        self._path_width_spin.setRange(0.5, 10)
        self._path_width_spin.setValue(canvas.path_width)
        self._path_width_spin.setSingleStep(0.5)
        self._path_width_spin.setSuffix(" px")
        marker_form.addRow("航线宽度:", self._path_width_spin)

        # Pan speed (Shift+left-drag)
        self._pan_speed_slider = QSlider(Qt.Horizontal)
        self._pan_speed_slider.setRange(1, 100)
        self._pan_speed_slider.setValue(int(canvas.pan_speed))
        self._pan_label = QLabel(f"{canvas.pan_speed:.0f}×")
        self._pan_speed_slider.valueChanged.connect(
            lambda v: self._pan_label.setText(f"{v:.0f}×")
        )
        pan_row = QHBoxLayout()
        pan_row.addWidget(self._pan_speed_slider)
        pan_row.addWidget(self._pan_label)
        marker_form.addRow("Shift+左移 速度:", pan_row)

        # Depth test for markers
        self._depth_test_cb = QCheckBox("航点深度测试 (关闭=始终可见)")
        self._depth_test_cb.setChecked(canvas.waypoint_depth_test)
        self._depth_test_cb.setToolTip(
            "关闭后航点和起降点始终绘制在点云之上，不会被遮挡"
        )
        marker_form.addRow(self._depth_test_cb)

        layout.addWidget(marker_group)

        # ── Buttons ────────────────────────────────────────────
        btn_box = QDialogButtonBox()
        btn_apply = QPushButton("应用")
        btn_apply.clicked.connect(self._apply)
        btn_close = QPushButton("关闭")
        btn_close.clicked.connect(self.accept)
        btn_box.addButton(btn_apply, QDialogButtonBox.ActionRole)
        btn_box.addButton(btn_close, QDialogButtonBox.RejectRole)
        layout.addWidget(btn_box)

        # Apply on change for sliders
        self._size_slider.sliderReleased.connect(self._apply)
        self._opacity_slider.sliderReleased.connect(self._apply)
        self._color_combo.currentIndexChanged.connect(self._apply)
        self._wp_size_spin.valueChanged.connect(self._apply)
        self._path_color_combo.currentIndexChanged.connect(self._apply)
        self._path_width_spin.valueChanged.connect(self._apply)
        self._pan_speed_slider.sliderReleased.connect(self._apply)
        self._depth_test_cb.toggled.connect(self._apply)

    def _apply(self):
        """Apply all settings to the canvas."""
        self._canvas.set_point_size(float(self._size_slider.value()))
        self._canvas.set_point_opacity(self._opacity_slider.value() / 100.0)
        self._canvas.set_color_mode(self._color_combo.currentText())
        self._canvas.set_waypoint_size(self._wp_size_spin.value())
        self._canvas.set_path_color(self._path_color_combo.currentText())
        self._canvas.set_path_width(self._path_width_spin.value())
        self._canvas.pan_speed = float(self._pan_speed_slider.value())
        self._canvas.set_waypoint_depth_test(self._depth_test_cb.isChecked())
        print("[rendering] Settings applied")


# ── Main Window ───────────────────────────────────────────────────

class MainWindow(QMainWindow):
    """Mission Generator main window."""

    def __init__(self):
        super().__init__()
        print("[app] Initializing MainWindow...")

        self.setWindowTitle("Mission Generator — PCD 点云任务生成器")
        self.setMinimumSize(1200, 700)

        # Window icon — search source tree and PyInstaller bundle
        _icon_path = None
        _bundle = getattr(sys, '_MEIPASS', None)
        _search = [_bundle] if _bundle else []
        _search.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        for _d in _search:
            _p = os.path.join(_d, "icon.png")
            if os.path.exists(_p):
                _icon_path = _p
                break
        if _icon_path:
            self.setWindowIcon(QIcon(_icon_path))

        # Core managers
        self._waypoint_manager = WaypointManager(self)

        # State
        self._current_pcd: Optional[PCDData] = None
        self._current_pcd_path: str = ""
        self._last_waypoint_dir: str = os.path.expanduser("~")
        self._last_launch_dir: str = os.path.expanduser("~")

        self._setup_ui()
        self._connect_signals()
        print("[app] MainWindow initialized — ready")

    def _setup_ui(self):
        """Build the main window layout."""
        # ── Menu bar ──────────────────────────────────────────────
        menubar = self.menuBar()

        # File menu
        file_menu = menubar.addMenu("文件(&F)")

        self._act_open_pcd = QAction("打开 PCD 文件...", self)
        self._act_open_pcd.setShortcut(QKeySequence("Ctrl+O"))
        file_menu.addAction(self._act_open_pcd)

        self._act_close_pcd = QAction("关闭文件", self)
        self._act_close_pcd.setShortcut(QKeySequence("Ctrl+W"))
        self._act_close_pcd.setEnabled(False)
        file_menu.addAction(self._act_close_pcd)

        file_menu.addSeparator()

        self._act_save_waypoints = QAction("保存航点集...", self)
        self._act_save_waypoints.setShortcut(QKeySequence("Ctrl+S"))
        file_menu.addAction(self._act_save_waypoints)

        self._act_load_waypoints = QAction("加载航点集...", self)
        self._act_load_waypoints.setShortcut(QKeySequence("Ctrl+L"))
        file_menu.addAction(self._act_load_waypoints)

        file_menu.addSeparator()

        self._act_generate_launch = QAction("生成 Launch 文件...", self)
        self._act_generate_launch.setShortcut(QKeySequence("Ctrl+G"))
        file_menu.addAction(self._act_generate_launch)

        file_menu.addSeparator()

        self._act_exit = QAction("退出(&X)", self)
        self._act_exit.setShortcut(QKeySequence("Ctrl+Q"))
        file_menu.addAction(self._act_exit)

        # View menu
        view_menu = menubar.addMenu("视图(&V)")

        self._act_toggle_axis = QAction("显示/隐藏坐标轴", self)
        self._act_toggle_axis.setCheckable(True)
        self._act_toggle_axis.setChecked(True)
        view_menu.addAction(self._act_toggle_axis)

        self._act_reset_view = QAction("重置视角", self)
        self._act_reset_view.setShortcut(QKeySequence("R"))
        view_menu.addAction(self._act_reset_view)

        self._act_rendering = QAction("渲染设置...", self)
        view_menu.addAction(self._act_rendering)

        # Help menu
        help_menu = menubar.addMenu("帮助(&H)")

        self._act_about = QAction("关于", self)
        help_menu.addAction(self._act_about)

        # ── Central widget ────────────────────────────────────────
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(0, 0, 0, 0)

        # Toolbar
        toolbar = QToolBar("主工具栏")
        toolbar.setMovable(False)
        self.addToolBar(toolbar)

        self._btn_open = QPushButton("📂 打开PCD")
        self._btn_open.setToolTip("打开 PCD 点云文件 (Ctrl+O)")
        toolbar.addWidget(self._btn_open)

        toolbar.addSeparator()

        self._btn_save_wp = QPushButton("💾 保存航点")
        self._btn_save_wp.setToolTip("保存航点集为JSON文件 (Ctrl+S)")
        toolbar.addWidget(self._btn_save_wp)

        self._btn_load_wp = QPushButton("📥 加载航点")
        self._btn_load_wp.setToolTip("从JSON文件加载航点集 (Ctrl+L)")
        toolbar.addWidget(self._btn_load_wp)

        toolbar.addSeparator()

        self._btn_generate = QPushButton("🚀 生成Launch")
        self._btn_generate.setToolTip("生成 ROS launch 文件 (Ctrl+G)")
        self._btn_generate.setStyleSheet(
            "QPushButton { background-color: #1565c0; color: white; "
            "padding: 4px 12px; }"
        )
        toolbar.addWidget(self._btn_generate)

        toolbar.addSeparator()

        self._btn_reset_view = QPushButton("🔄 重置视角")
        self._btn_reset_view.setToolTip("重置3D视角 (R)")
        toolbar.addWidget(self._btn_reset_view)

        self._btn_rendering = QPushButton("⚙️ 渲染")
        self._btn_rendering.setToolTip("调整点云和航点渲染参数")
        toolbar.addWidget(self._btn_rendering)

        # Help text for controls
        help_label = QLabel(
            "  🖱 左拖=旋转 | 滚轮=缩放 | Shift+左拖=平移 | 中键=添加航点 | ⚙️ 渲染设置调整速度/颜色  "
        )
        help_label.setStyleSheet(
            "color: #888; font-size: 11px; padding: 2px 8px;"
        )
        toolbar.addWidget(help_label)

        # ── Splitter ───────────────────────────────────────────────
        splitter = QSplitter(Qt.Horizontal)

        self._canvas = PointCloudCanvas(self)

        canvas_container = QWidget()
        canvas_layout = QVBoxLayout(canvas_container)
        canvas_layout.setContentsMargins(0, 0, 0, 0)
        canvas_layout.addWidget(self._canvas.native)

        splitter.addWidget(canvas_container)

        self._panel = WaypointPanel(self._waypoint_manager)
        splitter.addWidget(self._panel)

        splitter.setSizes([800, 350])
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 1)

        main_layout.addWidget(splitter)

        # ── Status bar ────────────────────────────────────────────
        self._status_bar = QStatusBar()
        self.setStatusBar(self._status_bar)

        self._status_pcd = QLabel("就绪 — 请打开 PCD 文件")
        self._status_wp = QLabel("航点数: 0")
        self._status_bar.addWidget(self._status_pcd, 3)
        self._status_bar.addPermanentWidget(self._status_wp, 1)

    def _connect_signals(self):
        """Connect all UI signals to slots."""
        self._act_open_pcd.triggered.connect(self._on_open_pcd)
        self._act_close_pcd.triggered.connect(self._on_close_pcd)
        self._act_save_waypoints.triggered.connect(self._on_save_waypoints)
        self._act_load_waypoints.triggered.connect(self._on_load_waypoints)
        self._act_generate_launch.triggered.connect(self._on_generate_launch)
        self._act_exit.triggered.connect(self.close)
        self._act_toggle_axis.triggered.connect(self._canvas.toggle_axis)
        self._act_reset_view.triggered.connect(self._canvas.reset_view)
        self._act_rendering.triggered.connect(self._on_rendering_settings)
        self._act_about.triggered.connect(self._on_about)

        self._btn_open.clicked.connect(self._on_open_pcd)
        self._btn_save_wp.clicked.connect(self._on_save_waypoints)
        self._btn_load_wp.clicked.connect(self._on_load_waypoints)
        self._btn_generate.clicked.connect(self._on_generate_launch)
        self._btn_reset_view.clicked.connect(self._canvas.reset_view)
        self._btn_rendering.clicked.connect(self._on_rendering_settings)

        # Canvas signals
        self._canvas.sig_point_picked.connect(self._on_point_picked)
        self._canvas.sig_waypoint_requested.connect(self._on_waypoint_requested)

        # Waypoint panel
        self._panel.waypoints_changed.connect(self._on_waypoints_changed)
        self._panel.home_set.connect(self._on_home_changed)
        self._panel.home_cleared.connect(self._on_home_changed)

    # ── Slots ─────────────────────────────────────────────────────

    def _on_open_pcd(self):
        """Open a PCD file. Clears existing waypoints when loading new data."""
        path, _ = QFileDialog.getOpenFileName(
            self, "打开 PCD 文件",
            self._last_launch_dir,
            "PCD files (*.pcd *.PCD);;All files (*)",
        )
        if not path:
            return

        # If waypoints exist, clear them (new PCD = new mission)
        if self._waypoint_manager.count() > 0:
            print("[app] Clearing existing waypoints for new PCD")
            self._waypoint_manager.clear()
            self._panel.refresh()

        # Close any active preview dialog
        if hasattr(self, '_active_waypoint_dlg') and self._active_waypoint_dlg:
            self._canvas.hide_preview_marker()
            self._active_waypoint_dlg.close()
            self._active_waypoint_dlg = None

        try:
            print(f"[app] Loading PCD: {path}")
            self._current_pcd = read_pcd(path)
            self._current_pcd_path = path

            self._canvas.set_point_cloud(
                self._current_pcd.points,
                self._current_pcd.colors,
            )

            filename = os.path.basename(path)
            n_points = self._current_pcd.point_count
            self._status_pcd.setText(
                f"PCD: {filename} ({n_points:,} 点)"
            )
            self._act_close_pcd.setEnabled(True)
            print(f"[app] PCD loaded: {filename} ({n_points:,} points)")

            self._update_all()

        except Exception as e:
            print(f"[app] ERROR loading PCD: {e}", file=sys.stderr)
            QMessageBox.critical(
                self, "加载失败",
                f"无法读取 PCD 文件:\n{str(e)}"
            )

    def _on_close_pcd(self):
        """Close the current PCD file, clearing all data."""
        # Confirm if waypoints exist
        if self._waypoint_manager.count() > 0:
            reply = QMessageBox.question(
                self, "确认关闭",
                f"当前有 {self._waypoint_manager.count()} 个航点将随点云一起清除。"
                "是否继续？\n\n"
                "提示：你可以先使用 Ctrl+S 保存航点集。",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if reply != QMessageBox.Yes:
                return

            # Clear waypoints
            self._waypoint_manager.clear()
            self._panel.refresh()

        # Close any preview dialog
        if hasattr(self, '_active_waypoint_dlg') and self._active_waypoint_dlg:
            self._canvas.hide_preview_marker()
            self._active_waypoint_dlg.close()
            self._active_waypoint_dlg = None

        # Clear canvas
        self._canvas.close_point_cloud()

        self._current_pcd = None
        self._current_pcd_path = None
        self._act_close_pcd.setEnabled(False)
        self._status_pcd.setText("就绪 — 请打开 PCD 文件")
        self._status_wp.setText("航点数: 0")
        self._update_all()
        print("[app] File closed")

    def _on_save_waypoints(self):
        """Save waypoints to a JSON file."""
        path, _ = QFileDialog.getSaveFileName(
            self, "保存航点集",
            self._last_waypoint_dir,
            "JSON files (*.json);;All files (*)",
        )
        if not path:
            return

        try:
            self._waypoint_manager.save_json(path)
            self._last_waypoint_dir = os.path.dirname(path)
            n = self._waypoint_manager.count()
            print(f"[app] Waypoints saved: {path} ({n} waypoints)")
            self._status_bar.showMessage(f"航点已保存: {path} ({n} 个)", 3000)
        except Exception as e:
            print(f"[app] ERROR saving waypoints: {e}", file=sys.stderr)
            QMessageBox.critical(self, "保存失败", str(e))

    def _on_load_waypoints(self):
        """Load waypoints from a JSON file."""
        path, _ = QFileDialog.getOpenFileName(
            self, "加载航点集",
            self._last_waypoint_dir,
            "JSON files (*.json);;All files (*)",
        )
        if not path:
            return

        try:
            self._waypoint_manager.load_json(path)
            self._last_waypoint_dir = os.path.dirname(path)
            self._panel.refresh()
            self._update_all()
            n = self._waypoint_manager.count()
            print(f"[app] Waypoints loaded: {path} ({n} waypoints)")
            self._status_bar.showMessage(f"航点已加载: {path} ({n} 个)", 3000)
        except Exception as e:
            print(f"[app] ERROR loading waypoints: {e}", file=sys.stderr)
            QMessageBox.critical(self, "加载失败", str(e))

    def _on_generate_launch(self):
        """Open the launch generation dialog."""
        if self._waypoint_manager.count() == 0:
            reply = QMessageBox.question(
                self, "无航点",
                "当前没有设置任何航点。是否继续生成空的 launch 文件？",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if reply != QMessageBox.Yes:
                return

        dialog = LaunchDialog(self._waypoint_manager, self)
        if dialog.exec() == LaunchDialog.Accepted:
            path = dialog.generated_path
            if path:
                print(f"[app] Launch generated: {path}")
                self._status_bar.showMessage(f"Launch 已生成: {path}", 5000)

    def _on_point_picked(self, x: float, y: float, z: float):
        """Left-click pick — pre-fill panel input fields."""
        self._panel.set_waypoint_input(x, y, z)
        self._status_bar.showMessage(
            f"已选取: ({x:.3f}, {y:.3f}, {z:.3f}) — 点击'添加航点'确认",
            5000,
        )

    def _on_waypoint_requested(self, x: float, y: float, z: float):
        """Middle-click pick — show floating add-waypoint dialog with live preview."""
        print(f"[app] Middle-click waypoint request at ({x:.3f}, {y:.3f}, {z:.3f})")

        # Close any existing preview dialog
        if hasattr(self, '_active_waypoint_dlg') and self._active_waypoint_dlg:
            self._canvas.hide_preview_marker()
            self._active_waypoint_dlg.close()

        # Show preview marker at picked position
        self._canvas.show_preview_marker(x, y, z)

        # Non-modal floating dialog
        dlg = WaypointAddDialog(x, y, z, self)
        self._active_waypoint_dlg = dlg

        # Live preview: update marker as user adjusts coordinates
        dlg.coords_changed.connect(self._canvas.move_preview_marker)

        def _on_accepted():
            data = dlg.waypoint_data
            wp = Waypoint(
                x=data['x'], y=data['y'], z=data['z'],
                wait_time=data['wait_time'],
                record_flag=data['record_flag'],
                label=data['label'],
            )
            self._waypoint_manager.add(wp)
            self._panel.refresh()
            self._update_all()
            self._canvas.hide_preview_marker()
            self._active_waypoint_dlg = None
            print(f"[app] Waypoint added via middle-click: {wp}")

        def _on_rejected():
            self._canvas.hide_preview_marker()
            self._active_waypoint_dlg = None
            print("[app] Waypoint preview cancelled")

        dlg.accepted.connect(_on_accepted)
        dlg.rejected.connect(_on_rejected)
        dlg.show()  # non-modal

    def _on_rendering_settings(self):
        """Open rendering settings dialog."""
        dlg = RenderingDialog(self._canvas, self)
        dlg.exec()

    def _on_waypoints_changed(self):
        self._update_all()

    def _on_home_changed(self, wp=None):
        self._update_all()

    def _on_about(self):
        """Show about dialog."""
        QMessageBox.about(
            self, "关于 Mission Generator",
            "<h3>Mission Generator v1.2</h3>"
            "<p>PCD 点云可视化与 ROS 任务点生成工具</p>"
            "<p>基于 PySide6 + vispy 构建</p>"
            "<hr>"
            "<p><b>操作说明:</b></p>"
            "<ul>"
            "<li><b>左拖</b> = 旋转视角</li>"
            "<li><b>中拖</b> = 平移视角</li>"
            "<li><b>滚轮</b> = 缩放</li>"
            "<li><b>右键</b> = 选取位置并添加航点</li>"
            "<li><b>左键</b> = 拾取坐标填入面板</li>"
            "</ul>"
        )

    # ── Internal ──────────────────────────────────────────────────

    def _update_all(self):
        """Sync 3D canvas and status bar with current state."""
        wps = self._waypoint_manager.get_all()
        wp_positions = [(wp.x, wp.y, wp.z) for wp in wps]
        recording_flags = [wp.record_flag for wp in wps]
        home = self._waypoint_manager.get_home()
        home_pos = (home.x, home.y, home.z) if home else None

        self._canvas.set_waypoints(wp_positions, home_pos, recording_flags)
        self._status_wp.setText(f"航点数: {self._waypoint_manager.count()}")

    def closeEvent(self, event):
        """Prompt to save before closing. Skipped on force_close (Ctrl+C).

        vispy GPU cleanup is handled by QApplication.aboutToQuit
        (connected in main.py), NOT here — it fires after all windows
        close but before Qt destroys widgets/GL contexts.
        """
        if self.property("force_close"):
            print("[app] Force close — exiting")
            event.accept()
            return

        if self._waypoint_manager.count() > 0:
            reply = QMessageBox.question(
                self, "确认退出",
                f"当前有 {self._waypoint_manager.count()} 个未保存的航点。"
                "是否退出？\n\n"
                "提示：你可以先使用 Ctrl+S 保存航点集。",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if reply != QMessageBox.Yes:
                event.ignore()
                return
        print("[app] Normal exit")
        event.accept()
