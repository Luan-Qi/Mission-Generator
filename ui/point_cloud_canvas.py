"""
3D Point Cloud Visualization Canvas using vispy + PySide6.

Provides:
- Point cloud rendering with adjustable size, opacity, and color modes
- Waypoint markers (red spheres with number labels)
- Home marker (green square)
- Flight path lines with direction arrows
- Live preview marker when picking a waypoint
- Middle-click → pick point → emit signal for waypoint creation
- Left-click → pick point → emit signal for coordinate filling

Key design: ALL vispy visuals are pre-created at init time with empty data.
This avoids GPU shader recompilation / buffer reallocation glitches that
cause black artifacts when visuals are created on-the-fly after the first
render pass.
"""

import math
import numpy as np
from typing import Optional, List

from PySide6.QtCore import Signal, QObject, Qt
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout,
    QDoubleSpinBox, QCheckBox, QLineEdit, QComboBox,
    QPushButton, QLabel, QDialogButtonBox,
)

from vispy import scene
from vispy.scene import visuals
from vispy.scene.cameras import TurntableCamera
from vispy.color import Colormap


# ── Constants ─────────────────────────────────────────────────────

BG_COLOR = (0.12, 0.12, 0.12, 1.0)
WP_COLOR = (1.0, 0.2, 0.2, 1.0)
HOME_COLOR = (0.2, 1.0, 0.2, 1.0)
PREVIEW_COLOR = (1.0, 0.9, 0.0, 0.9)  # yellow diamond for preview

# Path color presets
PATH_COLOR_PRESETS = {
    "黄色 (默认)":   (1.0, 1.0, 0.0, 0.9),
    "青色":          (0.0, 1.0, 1.0, 0.9),
    "洋红":          (1.0, 0.0, 1.0, 0.9),
    "橙红":          (1.0, 0.4, 0.0, 0.9),
    "白色":          (1.0, 1.0, 1.0, 0.9),
    "绿色":          (0.2, 1.0, 0.2, 0.9),
}

Z_CMAP = Colormap(['#0044ff', '#00ff88', '#ffff00', '#ff4400'])

SOLID_COLORMAPS = {
    "Z 高度 (蓝→红)": Z_CMAP,
    "白色": Colormap(['#ffffff', '#ffffff']),
    "青色": Colormap(['#00ffff', '#00ffff']),
    "绿色": Colormap(['#00ff00', '#00ff00']),
    "橙色": Colormap(['#ff8800', '#ff8800']),
}

# Ordered list of color preset names for auto-selecting recording color
_PATH_COLOR_NAMES = list(PATH_COLOR_PRESETS.keys())


def _get_recording_color(path_color_name: str) -> tuple:
    """Return the next color preset after the current path color.

    Recording segments are rendered in this color to visually
    distinguish which flight segments trigger video recording.
    """
    try:
        idx = _PATH_COLOR_NAMES.index(path_color_name)
    except ValueError:
        idx = 0
    next_idx = (idx + 1) % len(_PATH_COLOR_NAMES)
    return PATH_COLOR_PRESETS[_PATH_COLOR_NAMES[next_idx]]


# ── Waypoint Add Dialog (non-modal, floating) ─────────────────────

class WaypointAddDialog(QDialog):
    """Non-modal floating dialog for adding/editing a waypoint.

    Emits coords_changed as the user adjusts spinboxes, so the parent
    can update a live preview marker in the 3D view.
    """

    coords_changed = Signal(float, float, float)

    def __init__(self, x: float, y: float, z: float, parent=None):
        super().__init__(parent)
        self.setWindowTitle("添加航点")
        self.setMinimumWidth(340)
        # Floating, stays on top, non-modal
        self.setWindowFlags(
            Qt.Window | Qt.WindowStaysOnTopHint | Qt.CustomizeWindowHint |
            Qt.WindowTitleHint | Qt.WindowCloseButtonHint
        )
        self.setAttribute(Qt.WA_DeleteOnClose, True)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("<b>已拾取位置 (微调后确认):</b>"))
        layout.addWidget(QLabel(
            "<span style='color:#888;font-size:11px;'>"
            "💡 对话框悬浮不遮挡 — 可旋转视角确认位置后微调"
            "</span>"
        ))

        form = QFormLayout()
        self._x_spin = QDoubleSpinBox()
        self._x_spin.setRange(-99999, 99999)
        self._x_spin.setDecimals(3)
        self._x_spin.setValue(x)
        self._x_spin.valueChanged.connect(self._emit_coords)
        form.addRow("X:", self._x_spin)
        self._y_spin = QDoubleSpinBox()
        self._y_spin.setRange(-99999, 99999)
        self._y_spin.setDecimals(3)
        self._y_spin.setValue(y)
        self._y_spin.valueChanged.connect(self._emit_coords)
        form.addRow("Y:", self._y_spin)
        self._z_spin = QDoubleSpinBox()
        self._z_spin.setRange(-99999, 99999)
        self._z_spin.setDecimals(3)
        self._z_spin.setValue(z)
        self._z_spin.valueChanged.connect(self._emit_coords)
        form.addRow("Z:", self._z_spin)
        layout.addLayout(form)

        wait_layout = QHBoxLayout()
        wait_layout.addWidget(QLabel("等待:"))
        self._wait_mode = QComboBox()
        self._wait_mode.addItem("使用默认", "default")
        self._wait_mode.addItem("自定义 (秒)", "custom")
        self._wait_mode.addItem("永久等待 (∞)", "infinite")
        wait_layout.addWidget(self._wait_mode)
        self._wait_value = QDoubleSpinBox()
        self._wait_value.setRange(0.1, 9999)
        self._wait_value.setDecimals(1)
        self._wait_value.setValue(5.0)
        self._wait_value.setEnabled(False)
        wait_layout.addWidget(self._wait_value)
        layout.addLayout(wait_layout)
        self._wait_mode.currentIndexChanged.connect(
            lambda i: self._wait_value.setEnabled(
                self._wait_mode.currentData() == "custom"
            )
        )

        self._record_cb = QCheckBox("录像标记 (*)")
        layout.addWidget(self._record_cb)

        lbl_layout = QHBoxLayout()
        lbl_layout.addWidget(QLabel("标签:"))
        self._label_edit = QLineEdit()
        self._label_edit.setPlaceholderText("备注（可选）")
        lbl_layout.addWidget(self._label_edit)
        layout.addLayout(lbl_layout)

        btn_layout = QHBoxLayout()
        btn_add = QPushButton("✓ 添加航点")
        btn_add.setStyleSheet(
            "QPushButton { background-color: #c62828; color: white; "
            "padding: 6px 20px; font-size: 13px; }"
        )
        btn_cancel = QPushButton("取消")
        btn_layout.addWidget(btn_add)
        btn_layout.addWidget(btn_cancel)
        layout.addLayout(btn_layout)

        btn_add.clicked.connect(self.accept)
        btn_cancel.clicked.connect(self.reject)

    def _emit_coords(self):
        self.coords_changed.emit(
            self._x_spin.value(),
            self._y_spin.value(),
            self._z_spin.value(),
        )

    def update_coords(self, x: float, y: float, z: float):
        """Set spinbox values without re-emitting coords_changed."""
        self._x_spin.blockSignals(True)
        self._y_spin.blockSignals(True)
        self._z_spin.blockSignals(True)
        self._x_spin.setValue(x)
        self._y_spin.setValue(y)
        self._z_spin.setValue(z)
        self._x_spin.blockSignals(False)
        self._y_spin.blockSignals(False)
        self._z_spin.blockSignals(False)

    @property
    def waypoint_data(self) -> dict:
        mode = self._wait_mode.currentData()
        if mode == "default":
            wait = None
        elif mode == "infinite":
            wait = 0.0
        else:
            wait = self._wait_value.value()
        return {
            'x': self._x_spin.value(),
            'y': self._y_spin.value(),
            'z': self._z_spin.value(),
            'wait_time': wait,
            'record_flag': self._record_cb.isChecked(),
            'label': self._label_edit.text().strip(),
        }


# ── Signal Proxy ──────────────────────────────────────────────────

class _CanvasSignals(QObject):
    """Signal holder — vispy's frozen metaclass blocks direct Signal on canvas."""
    point_picked = Signal(float, float, float)
    waypoint_requested = Signal(float, float, float)


# ── Main Canvas ───────────────────────────────────────────────────

class PointCloudCanvas(scene.SceneCanvas):
    """3D point cloud + waypoint visualization canvas.

    Camera: TurntableCamera (left-drag=rotate, scroll=zoom,
                             Shift+left-drag=pan at 3× speed)
    Middle-click: pick point → waypoint_requested signal (with live preview)
    Left-click: pick point → point_picked signal
    """

    def __init__(self, parent=None):
        super().__init__(parent=parent, keys='interactive')
        self.unfreeze()

        # ── Signal proxy ──
        self._signal_proxy = _CanvasSignals()
        self.sig_point_picked = self._signal_proxy.point_picked
        self.sig_waypoint_requested = self._signal_proxy.waypoint_requested

        # ── State ──
        self._pcd_points: Optional[np.ndarray] = None
        self._point_colors: Optional[np.ndarray] = None
        self._waypoints_data: List = []
        self._recording_flags: List[bool] = []
        self._home_data: Optional[tuple] = None
        self._waypoint_count: int = 0
        self._preview_active: bool = False

        # Mouse state
        self._is_picking: bool = False
        self._mouse_press_pos = None

        # Rendering settings
        self._point_size: float = 3.0
        self._point_opacity: float = 1.0
        self._color_mode: str = "Z 高度 (蓝→红)"
        self._wp_size: float = 14.0
        self._path_width: float = 2.5
        self._path_color_name: str = "黄色 (默认)"
        self._waypoint_depth_test: bool = False

        # ── Build scene ──
        self._grid = self.central_widget.add_grid(spacing=0)
        self._view = self._grid.add_view(row=0, col=0)
        self._view.camera = TurntableCamera(
            fov=45, elevation=30, azimuth=-45,
            translate_speed=20.0,  # default pan speed
        )
        self._view.camera.distance = 20
        self._view.camera.center = (0, 0, 5)
        self._view.bgcolor = BG_COLOR

        # ── Nodes (fixed hierarchy, never modified after init) ──
        self._axis_node = scene.Node(parent=self._view.scene, name='axis')
        self._pcd_node = scene.Node(parent=self._view.scene, name='pcd')
        self._marker_node = scene.Node(parent=self._view.scene, name='markers')
        self._label_node = scene.Node(parent=self._view.scene, name='labels')
        self._path_node = scene.Node(parent=self._view.scene, name='path')

        # Axis — in its own node with depth_test disabled to avoid black
        # artifacts from Z-fighting with the grid or background.
        self._axis_visual = visuals.XYZAxis(parent=self._axis_node)
        self._axis_visual.set_gl_state(depth_test=False, blend=True)
        self._show_axis: bool = True

        # ── Pre-create ALL visuals (empty/invisible at start) ──
        # This avoids the one-frame shader compilation + buffer allocation
        # glitch that causes black artifacts when creating visuals
        # on-the-fly after the first render pass.

        # Use a single invisible dummy point to pre-allocate GPU buffers.
        # An empty (0,3) array crashes vispy's ColorArray (zero-size min/max).
        _dummy_xyz = np.zeros((1, 3), dtype=np.float32)
        _dummy_rgba = np.zeros((1, 4), dtype=np.float32)

        # Point cloud
        self._pc_visual = visuals.Markers(parent=self._pcd_node)
        self._pc_visual.set_data(_dummy_xyz, face_color=_dummy_rgba, size=0)
        self._pc_visual.set_gl_state(depth_test=True, blend=True)

        # Waypoint markers (single Markers for all WPs)
        self._wp_visual = visuals.Markers(parent=self._marker_node)
        self._wp_visual.set_data(_dummy_xyz, face_color=_dummy_rgba,
                                 edge_color=(0, 0, 0, 0), size=0, symbol='disc')
        self._wp_visual.set_gl_state(depth_test=False, blend=True, depth_mask=False)

        # Home marker
        self._home_visual = visuals.Markers(parent=self._marker_node)
        self._home_visual.set_data(_dummy_xyz, face_color=_dummy_rgba,
                                   edge_color=(0, 0, 0, 0), size=0, symbol='square')
        self._home_visual.set_gl_state(depth_test=False, blend=True, depth_mask=False)

        # Preview marker (yellow diamond, hidden by default)
        self._preview_visual = visuals.Markers(parent=self._marker_node)
        self._preview_visual.set_data(_dummy_xyz, face_color=_dummy_rgba,
                                      edge_color=(0, 0, 0, 0), size=0, symbol='diamond')
        self._preview_visual.set_gl_state(depth_test=False, blend=True, depth_mask=False)

        # Path — Arrow visual draws both the connecting line and 3D
        # directional arrow heads at segment midpoints.
        # Arrow appearance properties are set on the visual object;
        # set_data() only accepts pos/color/width/connect/arrows.
        path_rgba = PATH_COLOR_PRESETS[self._path_color_name]
        self._path_visual = visuals.Arrow(parent=self._path_node,
                                          pos=_dummy_xyz, color=path_rgba,
                                          width=self._path_width, method='gl',
                                          connect='segments')
        self._path_visual.arrow_color = path_rgba
        self._path_visual.arrow_size = 14
        self._path_visual.arrow_type = 'triangle_60'
        self._path_visual.set_gl_state(depth_test=True, blend=True)

        # Recording-segment path — second Arrow for segments where the
        # destination waypoint has record_flag=True.  Uses the next color
        # preset after the selected path color (auto/passive choice).
        rec_rgba = _get_recording_color(self._path_color_name)
        self._path_rec_visual = visuals.Arrow(parent=self._path_node,
                                              pos=_dummy_xyz, color=rec_rgba,
                                              width=self._path_width + 1.0,
                                              method='gl', connect='segments')
        self._path_rec_visual.arrow_color = rec_rgba
        self._path_rec_visual.arrow_size = 14
        self._path_rec_visual.arrow_type = 'triangle_60'
        self._path_rec_visual.set_gl_state(depth_test=True, blend=True)

        # Waypoint labels — created lazily (on demand, not pre-allocated).
        # Each Text visual costs ~3.5 MB GPU memory; pre-allocating 256
        # would consume ~900 MB at startup.
        self._wp_labels: List[visuals.Text] = []

        # Home label
        self._home_label = visuals.Text('H', parent=self._label_node,
                                        pos=(0, 0, 0), color='lime',
                                        font_size=int(self._wp_size * 0.9),
                                        bold=True,
                                        anchor_x='center', anchor_y='center')
        self._home_label.visible = False

        self.freeze()
        self._view.camera.set_range()
        print("[canvas] Initialized — pre-created all visuals, "
              "left-drag=rotate, scroll=zoom, Shift+left=pan (20x), "
              "middle-click=add waypoint")

    # ── Mouse Events ──────────────────────────────────────────────

    def on_mouse_press(self, event):
        """Middle-click → waypoint; left-click (no mods) → pick."""
        if event.button == 3:  # Middle
            result = self._perform_pick(event.pos)
            if result is not None:
                x, y, z = result
                self.sig_waypoint_requested.emit(x, y, z)
                print(f"[canvas] Middle-click → waypoint at ({x:.3f}, {y:.3f}, {z:.3f})")
            else:
                print("[canvas] Middle-click — no point near cursor")
        elif event.button == 1 and not event.modifiers:  # Left
            self._is_picking = True
            self._mouse_press_pos = event.pos
        else:
            self._is_picking = False

    def on_mouse_release(self, event):
        """Left release: pick if click (not drag)."""
        if event.button == 1 and self._is_picking and self._mouse_press_pos is not None:
            dx = abs(event.pos[0] - self._mouse_press_pos[0])
            dy = abs(event.pos[1] - self._mouse_press_pos[1])
            if dx < 5 and dy < 5:
                result = self._perform_pick(event.pos)
                if result is not None:
                    x, y, z = result
                    self.sig_point_picked.emit(x, y, z)
        self._is_picking = False
        self._mouse_press_pos = None

    def on_mouse_move(self, event):
        """Drag > 5 px → cancel pick."""
        if self._is_picking and self._mouse_press_pos is not None:
            dx = abs(event.pos[0] - self._mouse_press_pos[0])
            dy = abs(event.pos[1] - self._mouse_press_pos[1])
            if dx > 5 or dy > 5:
                self._is_picking = False

    # ── Picking (ray-cast from camera params, no vispy internals) ─

    def _compute_camera_ray(self, screen_pos):
        """Compute world-space ray from screen position using camera params.

        Uses TurntableCamera azimuth/elevation/distance/center/fov to
        compute the camera position and ray direction analytically.
        No dependency on vispy internal methods (which were removed in 0.16).
        """
        cam = self._view.camera
        w, h = float(self.size[0]), float(self.size[1])
        if w == 0 or h == 0:
            return None

        az = math.radians(cam.azimuth)
        el = math.radians(cam.elevation)
        d = getattr(cam, '_actual_distance', cam.distance or 10.0)
        cx, cy, cz = cam.center

        # Camera world position (orbits center)
        cam_x = cx + d * math.cos(el) * math.sin(az)
        cam_y = cy - d * math.cos(el) * math.cos(az)
        cam_z = cz + d * math.sin(el)
        origin = np.array([cam_x, cam_y, cam_z], dtype=np.float64)

        # Camera basis
        look = np.array([cx - cam_x, cy - cam_y, cz - cam_z])
        look /= np.linalg.norm(look)

        world_up = np.array([0.0, 0.0, 1.0])
        right = np.cross(look, world_up)
        rn = np.linalg.norm(right)
        if rn < 1e-9:
            right = np.cross(look, np.array([1.0, 0.0, 0.0]))
            rn = np.linalg.norm(right)
        right /= rn
        cam_up = np.cross(right, look)

        # NDC → camera-space direction
        nx = 2.0 * screen_pos[0] / w - 1.0
        ny = 1.0 - 2.0 * screen_pos[1] / h

        fov_rad = math.radians(max(1.0, cam.fov))
        half_h = math.tan(fov_rad / 2.0)
        half_w = half_h * (w / h)

        ray_cam = np.array([nx * half_w, ny * half_h, -1.0])
        ray_cam /= np.linalg.norm(ray_cam)

        # World-space: camera +X→right, +Y→cam_up, -Z→look
        direction = (right * ray_cam[0] +
                     cam_up * ray_cam[1] +
                     look * (-ray_cam[2]))
        direction /= np.linalg.norm(direction)

        return origin.astype(np.float32), direction.astype(np.float32)

    def _perform_pick(self, pos) -> Optional[tuple]:
        """Ray-cast against point cloud, return nearest (x,y,z) or None."""
        if self._pcd_points is None or len(self._pcd_points) == 0:
            return None
        try:
            ray = self._compute_camera_ray(pos)
            if ray is None:
                return None
            origin = ray[0].astype(np.float64)
            direction = ray[1].astype(np.float64)

            to_pts = self._pcd_points.astype(np.float64) - origin
            proj = np.dot(to_pts, direction)
            front = proj > 0.0
            if not np.any(front):
                return None

            closest = origin + proj[:, np.newaxis] * direction
            perp = np.linalg.norm(
                self._pcd_points.astype(np.float64) - closest, axis=1)
            valid = front & (perp < 3.0)
            if not np.any(valid):
                return None

            best = int(np.where(valid)[0][np.argmin(perp[valid])])
            return (float(self._pcd_points[best, 0]),
                    float(self._pcd_points[best, 1]),
                    float(self._pcd_points[best, 2]))
        except Exception as e:
            print(f"[canvas] Pick error: {e}")
            return None

    # ── Point Cloud ───────────────────────────────────────────────

    def set_point_cloud(self, points: np.ndarray, colors=None):
        self._pcd_points = np.asarray(points, dtype=np.float32)
        self._rebuild_colors()
        self._pc_visual.set_data(
            self._pcd_points, face_color=self._point_colors,
            edge_color=None, size=self._point_size,
        )
        self._view.camera.set_range()
        print(f"[canvas] Cloud: {len(points)} pts, size={self._point_size:.0f}, "
              f"opacity={self._point_opacity:.0%}, color={self._color_mode}")

    def _rebuild_colors(self):
        if self._pcd_points is None:
            return
        z = self._pcd_points[:, 2]
        zn = (z - z.min()) / (z.max() - z.min() + 1e-6)
        cmap = SOLID_COLORMAPS.get(self._color_mode, Z_CMAP)
        self._point_colors = cmap.map(zn).astype(np.float32)
        self._point_colors[:, 3] = self._point_opacity

    def clear_point_cloud(self):
        self._pcd_points = None
        self._pc_visual.set_data(
            np.zeros((1, 3), dtype=np.float32),
            face_color=np.zeros((1, 4), dtype=np.float32), size=0)

    def close_point_cloud(self):
        """Clear point cloud, all waypoints, home, and reset view.

        Returns the canvas to its initial empty state — no data, no markers.
        """
        self._pcd_points = None
        self._point_colors = None
        self._waypoints_data.clear()
        self._recording_flags.clear()
        self._home_data = None
        self._waypoint_count = 0
        self._preview_active = False

        # Clear point cloud visual
        self._pc_visual.set_data(
            np.zeros((1, 3), dtype=np.float32),
            face_color=np.zeros((1, 4), dtype=np.float32), size=0)

        # Clear waypoint markers
        self._wp_visual.set_data(
            np.zeros((1, 3), dtype=np.float32),
            face_color=np.zeros((1, 4), dtype=np.float32),
            edge_color=(0, 0, 0, 0), size=0, symbol='disc')

        # Clear home marker
        self._home_visual.set_data(
            np.zeros((1, 3), dtype=np.float32),
            face_color=np.zeros((1, 4), dtype=np.float32),
            edge_color=(0, 0, 0, 0), size=0, symbol='square')
        self._home_label.visible = False

        # Clear preview marker
        self._preview_visual.set_data(
            np.zeros((1, 3), dtype=np.float32),
            face_color=np.zeros((1, 4), dtype=np.float32),
            edge_color=(0, 0, 0, 0), size=0, symbol='diamond')

        # Clear path (Arrow rejects size <= 0, keep size=1)
        self._path_visual.set_data(
            pos=np.zeros((1, 3), dtype=np.float32),
            arrows=np.zeros((1, 6), dtype=np.float32))
        self._path_rec_visual.set_data(
            pos=np.zeros((1, 3), dtype=np.float32),
            arrows=np.zeros((1, 6), dtype=np.float32))

        # Clear labels
        for t in self._wp_labels:
            t.parent = None
        self._wp_labels.clear()

        # Reset view
        self._view.camera.center = (0, 0, 5)
        self._view.camera.distance = 20
        self._view.camera.elevation = 30
        self._view.camera.azimuth = -45

        print("[canvas] Closed — all data cleared")

    # ── Preview Marker ────────────────────────────────────────────

    def show_preview_marker(self, x: float, y: float, z: float):
        """Show a yellow diamond at the candidate waypoint position."""
        self._preview_visual.set_data(
            np.array([[x, y, z]], dtype=np.float32),
            face_color=PREVIEW_COLOR,
            edge_color=(1, 1, 1, 0.8),
            size=self._wp_size + 4, symbol='diamond')
        self._preview_active = True

    def move_preview_marker(self, x: float, y: float, z: float):
        """Update the preview marker position (called from dialog spinboxes)."""
        if self._preview_active:
            self._preview_visual.set_data(
                np.array([[x, y, z]], dtype=np.float32),
                face_color=PREVIEW_COLOR,
                edge_color=(1, 1, 1, 0.8),
                size=self._wp_size + 4, symbol='diamond')

    def hide_preview_marker(self):
        """Remove the preview marker (set to invisible size=0)."""
        self._preview_visual.set_data(
            np.zeros((1, 3), dtype=np.float32),
            face_color=np.zeros((1, 4), dtype=np.float32),
            edge_color=(0, 0, 0, 0), size=0, symbol='diamond')
        self._preview_active = False

    # ── Rendering Settings ────────────────────────────────────────

    def set_point_size(self, size: float):
        self._point_size = size
        if self._pcd_points is not None:
            self._pc_visual.set_data(
                self._pcd_points, face_color=self._point_colors,
                edge_color=None, size=size)

    def set_point_opacity(self, opacity: float):
        self._point_opacity = max(0.01, min(1.0, opacity))
        if self._pcd_points is not None:
            self._rebuild_colors()
            self._pc_visual.set_data(
                self._pcd_points, face_color=self._point_colors,
                edge_color=None, size=self._point_size)

    def set_color_mode(self, mode: str):
        self._color_mode = mode
        if self._pcd_points is not None:
            self._rebuild_colors()
            self._pc_visual.set_data(
                self._pcd_points, face_color=self._point_colors,
                edge_color=None, size=self._point_size)

    def set_waypoint_size(self, size: float):
        self._wp_size = size
        self._refresh_markers()

    def set_path_width(self, width: float):
        self._path_width = width
        self._refresh_path()

    def set_path_color(self, name: str):
        self._path_color_name = name
        self._refresh_path()

    def set_waypoint_depth_test(self, enabled: bool):
        self._waypoint_depth_test = enabled
        self._wp_visual.set_gl_state(depth_test=enabled, blend=True,
                                     depth_mask=enabled)
        self._home_visual.set_gl_state(depth_test=enabled, blend=True,
                                       depth_mask=enabled)
        self._preview_visual.set_gl_state(depth_test=enabled, blend=True,
                                          depth_mask=enabled)

    # ── Waypoints / Markers ───────────────────────────────────────

    def set_waypoints(self, waypoints: List[tuple], home: Optional[tuple] = None,
                      recording_flags: Optional[List[bool]] = None):
        self._waypoints_data = list(waypoints)
        self._home_data = home
        self._waypoint_count = len(waypoints)
        self._recording_flags = list(recording_flags) if recording_flags else []
        self._refresh_markers()
        self._refresh_path()

    def _refresh_markers(self):
        """Update all marker visuals with current data (no create/destroy)."""
        dt = self._waypoint_depth_test

        # Waypoint markers (single visual, all positions at once)
        if self._waypoint_count > 0:
            wp_positions = np.array(self._waypoints_data, dtype=np.float32)
            self._wp_visual.set_data(
                wp_positions, face_color=WP_COLOR,
                edge_color=(1, 1, 1, 0.7),
                size=self._wp_size, symbol='disc')
        else:
            self._wp_visual.set_data(
                np.zeros((1, 3), dtype=np.float32),
                face_color=np.zeros((1, 4), dtype=np.float32),
                edge_color=(0, 0, 0, 0), size=0, symbol='disc')
        self._wp_visual.set_gl_state(depth_test=dt, blend=True, depth_mask=dt)

        # Home marker
        if self._home_data is not None:
            self._home_visual.set_data(
                np.array([[self._home_data[0], self._home_data[1],
                          self._home_data[2]]], dtype=np.float32),
                face_color=HOME_COLOR, edge_color=(0.5, 0.5, 0.5, 0.7),
                size=self._wp_size + 2, symbol='square')
            self._home_visual.set_gl_state(depth_test=dt, blend=True, depth_mask=dt)
            self._home_label.visible = True
            self._home_label.pos = (self._home_data[0], self._home_data[1],
                                    self._home_data[2] + 0.6)
            self._home_label.font_size = int(self._wp_size * 0.9)
        else:
            self._home_visual.set_data(
                np.zeros((1, 3), dtype=np.float32),
                face_color=np.zeros((1, 4), dtype=np.float32),
                edge_color=(0, 0, 0, 0), size=0, symbol='square')
            self._home_label.visible = False

        # Waypoint labels — lazy creation, lazy removal
        # Ensure we have enough Text visuals
        needed = self._waypoint_count
        current = len(self._wp_labels)

        # Create additional Text visuals if needed
        for i in range(current, needed):
            t = visuals.Text('', parent=self._label_node,
                            pos=(0, 0, 0), color='white',
                            font_size=int(self._wp_size * 0.8),
                            anchor_x='center', anchor_y='center')
            self._wp_labels.append(t)

        # Update visible labels
        for i in range(needed):
            wx, wy, wz = self._waypoints_data[i]
            label = self._wp_labels[i]
            label.text = str(i + 1)
            label.pos = (wx, wy, wz + 0.6)
            label.font_size = int(self._wp_size * 0.8)
            label.visible = True

        # Hide/remove excess labels (detach to free GPU memory)
        for i in range(needed, current):
            self._wp_labels[i].parent = None
        # Trim the list
        del self._wp_labels[needed:]

    def _refresh_path(self):
        """Update path lines and 3D directional arrows.

        Segments whose destination waypoint has record_flag=True are
        rendered with a separate Arrow visual in the recording colour
        (next preset after the selected path colour) so the operator
        can see at a glance which flight legs trigger video recording.
        """

        # ── Build full point sequence ────────────────────────────
        pts: list = []
        if self._home_data:
            pts.append(self._home_data)
        pts.extend(self._waypoints_data)
        if self._home_data and len(pts) > 1:
            pts.append(self._home_data)

        n_wps = self._waypoint_count
        has_home = self._home_data is not None
        n_segments = len(pts) - 1

        path_rgba = PATH_COLOR_PRESETS[self._path_color_name]
        rec_rgba = _get_recording_color(self._path_color_name)
        arrow_size = max(12, self._wp_size * 0.9)

        # ── Build per-segment data ────────────────────────────────
        reg_pos_list: list = []    # (p1, p2) pairs for regular colour
        rec_pos_list: list = []    # (p1, p2) pairs for recording colour
        reg_arrow_list: list = []
        rec_arrow_list: list = []

        for i in range(n_segments):
            p1 = np.array(pts[i], dtype=np.float32)
            p2 = np.array(pts[i + 1], dtype=np.float32)

            direction = p2 - p1
            length = float(np.linalg.norm(direction))
            if length < 1e-6:
                continue
            mid = (p1 + p2) / 2.0
            tail = mid - direction / length * (arrow_size * 0.04)
            arrow = [tail[0], tail[1], tail[2],
                     mid[0], mid[1], mid[2]]

            # Destination waypoint index for this segment
            if has_home:
                dest_wp_idx = i          # i=0: home→WP0 (dest 0), …, i=n_wps: WPn→home
            else:
                dest_wp_idx = i + 1      # i=0: WP0→WP1 (dest 1), …

            is_recording = (dest_wp_idx < n_wps
                            and dest_wp_idx < len(self._recording_flags)
                            and self._recording_flags[dest_wp_idx])

            if is_recording:
                rec_pos_list.extend([p1.tolist(), p2.tolist()])
                rec_arrow_list.append(arrow)
            else:
                reg_pos_list.extend([p1.tolist(), p2.tolist()])
                reg_arrow_list.append(arrow)

        # ── Update regular-path Arrow ─────────────────────────────
        if reg_pos_list:
            reg_pos = np.array(reg_pos_list, dtype=np.float32)
            reg_arrows = np.array(reg_arrow_list, dtype=np.float32)
        else:
            reg_pos = np.zeros((1, 3), dtype=np.float32)
            reg_arrows = np.zeros((1, 6), dtype=np.float32)

        self._path_visual.arrow_color = path_rgba
        self._path_visual.arrow_size = arrow_size
        self._path_visual.arrow_type = 'triangle_60'
        self._path_visual.set_data(
            pos=reg_pos, color=path_rgba, width=self._path_width,
            connect='segments', arrows=reg_arrows)

        # ── Update recording-path Arrow ───────────────────────────
        if rec_pos_list:
            rec_pos = np.array(rec_pos_list, dtype=np.float32)
            rec_arrows = np.array(rec_arrow_list, dtype=np.float32)
        else:
            rec_pos = np.zeros((1, 3), dtype=np.float32)
            rec_arrows = np.zeros((1, 6), dtype=np.float32)

        self._path_rec_visual.arrow_color = rec_rgba
        self._path_rec_visual.arrow_size = arrow_size
        self._path_rec_visual.arrow_type = 'triangle_60'
        self._path_rec_visual.set_data(
            pos=rec_pos, color=rec_rgba, width=self._path_width + 1.0,
            connect='segments', arrows=rec_arrows)

    # ── Cleanup ───────────────────────────────────────────────────

    def cleanup(self):
        """Release all vispy GPU resources while the OpenGL context is still valid.

        This MUST be called via QApplication.aboutToQuit — NOT from closeEvent.
        The aboutToQuit signal fires after all windows are closed but BEFORE
        Qt starts tearing down widgets and GL contexts.  If we detach visuals
        during widget destruction (closeEvent), Qt and vispy race to free
        shared QObjects (shaders, buffers) → "shared QObject was deleted
        directly" + SIGSEGV.
        """
        # ── Step 1: clear GPU data to minimal footprint ──
        _dxy = np.zeros((1, 3), dtype=np.float32)
        _drgba = np.zeros((1, 4), dtype=np.float32)
        _darrows = np.zeros((1, 6), dtype=np.float32)

        for visual, kwargs in [
            (getattr(self, '_pc_visual', None),
             {'face_color': _drgba, 'size': 0}),
            (getattr(self, '_wp_visual', None),
             {'face_color': _drgba, 'edge_color': (0, 0, 0, 0), 'size': 0, 'symbol': 'disc'}),
            (getattr(self, '_home_visual', None),
             {'face_color': _drgba, 'edge_color': (0, 0, 0, 0), 'size': 0, 'symbol': 'square'}),
            (getattr(self, '_preview_visual', None),
             {'face_color': _drgba, 'edge_color': (0, 0, 0, 0), 'size': 0, 'symbol': 'diamond'}),
        ]:
            if visual is not None:
                try:
                    visual.set_data(_dxy, **kwargs)
                except Exception:
                    pass

        for visual in (getattr(self, '_path_visual', None),
                       getattr(self, '_path_rec_visual', None)):
            if visual is not None:
                try:
                    visual.set_data(pos=_dxy, arrows=_darrows)
                except Exception:
                    pass

        # ── Step 2: hide all labels ──
        for t in getattr(self, '_wp_labels', []):
            try:
                t.text = ''
                t.visible = False
            except Exception:
                pass

        # ── Step 3: detach from scene graph (safe — GL context still alive) ──
        for attr in ('_pc_visual', '_wp_visual', '_home_visual',
                     '_preview_visual', '_path_visual', '_path_rec_visual',
                     '_axis_visual', '_home_label'):
            v = getattr(self, attr, None)
            if v is not None:
                try:
                    v.parent = None
                except Exception:
                    pass
                setattr(self, attr, None)

        for t in getattr(self, '_wp_labels', []):
            try:
                t.parent = None
            except Exception:
                pass
        self._wp_labels.clear()

        for node_attr in ('_axis_node', '_pcd_node', '_marker_node',
                          '_label_node', '_path_node'):
            node = getattr(self, node_attr, None)
            if node is not None:
                try:
                    node.parent = None
                except Exception:
                    pass
                setattr(self, node_attr, None)

        # ── Step 4: disconnect signal proxy ──
        proxy = getattr(self, '_signal_proxy', None)
        if proxy is not None:
            try:
                proxy.blockSignals(True)
            except Exception:
                pass
            self._signal_proxy = None

    # ── View ──────────────────────────────────────────────────────

    def reset_view(self):
        if self._pcd_points is not None and len(self._pcd_points) > 0:
            c = self._pcd_points.mean(axis=0)
            d = float(np.linalg.norm(self._pcd_points.max(axis=0) -
                                     self._pcd_points.min(axis=0)) * 1.5)
        else:
            c, d = (0, 0, 5), 20
        self._view.camera.center = tuple(c)
        self._view.camera.distance = max(d, 1.0)
        self._view.camera.elevation = 30
        self._view.camera.azimuth = -45

    def toggle_axis(self, visible=None):
        if visible is None:
            self._show_axis = not self._show_axis
        else:
            self._show_axis = visible
        if self._axis_visual:
            self._axis_visual.visible = self._show_axis

    @property
    def has_point_cloud(self) -> bool:
        return self._pcd_points is not None and len(self._pcd_points) > 0

    # Pan speed
    @property
    def pan_speed(self) -> float:
        return self._view.camera.translate_speed

    @pan_speed.setter
    def pan_speed(self, value: float):
        self._view.camera.translate_speed = float(value)

    # Rendering getters
    @property
    def point_size(self) -> float: return self._point_size
    @property
    def point_opacity(self) -> float: return self._point_opacity
    @property
    def color_mode(self) -> str: return self._color_mode
    @property
    def waypoint_size(self) -> float: return self._wp_size
    @property
    def path_width(self) -> float: return self._path_width
    @property
    def path_color_name(self) -> str: return self._path_color_name
    @property
    def waypoint_depth_test(self) -> bool: return self._waypoint_depth_test
