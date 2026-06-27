"""
Waypoint editing panel widget.

Provides:
- Manual coordinate input for adding waypoints
- Optional home position setting
- Waypoint table with inline editing
- Per-waypoint wait time and recording flag
- Right-click context menu for waypoint operations
"""

from typing import Optional, List

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGroupBox,
    QLabel, QDoubleSpinBox, QComboBox, QCheckBox,
    QLineEdit, QPushButton, QTableWidget, QTableWidgetItem,
    QHeaderView, QAbstractItemView, QMenu, QMessageBox,
    QSizePolicy,
)

from core.waypoint_manager import WaypointManager, Waypoint


class WaypointPanel(QWidget):
    """Right-side panel for managing waypoints and home position.

    Signals:
        waypoint_added(Waypoint): Emitted when a waypoint is added.
        waypoint_removed(int): Emitted when a waypoint is removed.
        waypoints_changed(): Emitted when the list is modified in any way.
        home_set(Waypoint): Emitted when home position is set.
        home_cleared(): Emitted when home position is cleared.
    """

    waypoint_added = Signal(object)   # Waypoint
    waypoint_removed = Signal(int)    # index
    waypoints_changed = Signal()
    home_set = Signal(object)         # Waypoint
    home_cleared = Signal()

    def __init__(self, waypoint_manager: WaypointManager, parent=None):
        super().__init__(parent)
        self._manager = waypoint_manager
        self._suppress_changes = False

        self._setup_ui()
        self._connect_signals()

    def _setup_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(4, 4, 4, 4)
        main_layout.setSpacing(8)

        # ── Home Position Group ────────────────────────────────────
        home_group = QGroupBox("起降点 (可选，仅用于可视化)")
        home_layout = QVBoxLayout(home_group)

        home_coord_layout = QHBoxLayout()
        home_coord_layout.addWidget(QLabel("X:"))
        self._home_x = QDoubleSpinBox()
        self._home_x.setRange(-99999, 99999)
        self._home_x.setDecimals(3)
        self._home_x.setValue(0)
        home_coord_layout.addWidget(self._home_x)

        home_coord_layout.addWidget(QLabel("Y:"))
        self._home_y = QDoubleSpinBox()
        self._home_y.setRange(-99999, 99999)
        self._home_y.setDecimals(3)
        home_coord_layout.addWidget(self._home_y)

        home_coord_layout.addWidget(QLabel("Z:"))
        self._home_z = QDoubleSpinBox()
        self._home_z.setRange(-99999, 99999)
        self._home_z.setDecimals(3)
        self._home_z.setValue(1.0)
        home_coord_layout.addWidget(self._home_z)
        home_layout.addLayout(home_coord_layout)

        home_btn_layout = QHBoxLayout()
        self._btn_set_home = QPushButton("设为起降点")
        self._btn_set_home.setStyleSheet(
            "QPushButton { background-color: #2e7d32; color: white; }"
        )
        self._btn_clear_home = QPushButton("清除起降点")
        home_btn_layout.addWidget(self._btn_set_home)
        home_btn_layout.addWidget(self._btn_clear_home)
        home_layout.addLayout(home_btn_layout)

        self._home_status = QLabel("◆ 未设置起降点")
        self._home_status.setStyleSheet("color: #888;")
        home_layout.addWidget(self._home_status)

        main_layout.addWidget(home_group)

        # ── Add Waypoint Group ─────────────────────────────────────
        wp_group = QGroupBox("手动添加航点")
        wp_layout = QVBoxLayout(wp_group)

        coord_layout = QHBoxLayout()
        coord_layout.addWidget(QLabel("X:"))
        self._wp_x = QDoubleSpinBox()
        self._wp_x.setRange(-99999, 99999)
        self._wp_x.setDecimals(3)
        coord_layout.addWidget(self._wp_x)

        coord_layout.addWidget(QLabel("Y:"))
        self._wp_y = QDoubleSpinBox()
        self._wp_y.setRange(-99999, 99999)
        self._wp_y.setDecimals(3)
        coord_layout.addWidget(self._wp_y)

        coord_layout.addWidget(QLabel("Z:"))
        self._wp_z = QDoubleSpinBox()
        self._wp_z.setRange(-99999, 99999)
        self._wp_z.setDecimals(3)
        coord_layout.addWidget(self._wp_z)
        wp_layout.addLayout(coord_layout)

        # Wait time
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
        wp_layout.addLayout(wait_layout)

        # Recording flag and label
        extra_layout = QHBoxLayout()
        self._record_flag = QCheckBox("录像标记 (*)")
        extra_layout.addWidget(self._record_flag)
        extra_layout.addWidget(QLabel("标签:"))
        self._wp_label = QLineEdit()
        self._wp_label.setPlaceholderText("备注")
        extra_layout.addWidget(self._wp_label)
        wp_layout.addLayout(extra_layout)

        # Add button
        self._btn_add_wp = QPushButton("添加航点")
        self._btn_add_wp.setStyleSheet(
            "QPushButton { background-color: #c62828; color: white; }"
        )
        wp_layout.addWidget(self._btn_add_wp)

        main_layout.addWidget(wp_group)

        # ── Waypoint Table ─────────────────────────────────────────
        table_group = QGroupBox("航点列表")
        table_layout = QVBoxLayout(table_group)

        self._table = QTableWidget(0, 7)
        self._table.setHorizontalHeaderLabels(
            ["#", "X", "Y", "Z", "等待(s)", "录像", "标签"]
        )
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self._table.setAlternatingRowColors(True)
        self._table.setEditTriggers(QAbstractItemView.DoubleClicked)
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.verticalHeader().setVisible(False)
        self._table.setContextMenuPolicy(Qt.CustomContextMenu)

        # Fixed column widths so all 7 columns fit in default window
        self._table.setColumnWidth(0, 28)   # #
        self._table.setColumnWidth(1, 64)   # X
        self._table.setColumnWidth(2, 64)   # Y
        self._table.setColumnWidth(3, 64)   # Z
        self._table.setColumnWidth(4, 62)   # 等待
        self._table.setColumnWidth(5, 44)   # 录像

        table_layout.addWidget(self._table)

        # Status bar
        self._status_label = QLabel("共 0 个航点")
        table_layout.addWidget(self._status_label)

        main_layout.addWidget(table_group, stretch=1)

        # ── Tool buttons ───────────────────────────────────────────
        tool_layout = QHBoxLayout()
        self._btn_move_up = QPushButton("↑ 上移")
        self._btn_move_down = QPushButton("↓ 下移")
        self._btn_delete = QPushButton("删除选中")
        self._btn_clear_all = QPushButton("清空全部")

        self._btn_move_up.setEnabled(False)
        self._btn_move_down.setEnabled(False)
        self._btn_delete.setEnabled(False)
        self._btn_clear_all.setEnabled(False)

        tool_layout.addWidget(self._btn_move_up)
        tool_layout.addWidget(self._btn_move_down)
        tool_layout.addWidget(self._btn_delete)
        tool_layout.addWidget(self._btn_clear_all)
        main_layout.addLayout(tool_layout)

    def _connect_signals(self):
        # Home buttons
        self._btn_set_home.clicked.connect(self._on_set_home)
        self._btn_clear_home.clicked.connect(self._on_clear_home)

        # Wait mode toggle
        self._wait_mode.currentIndexChanged.connect(self._on_wait_mode_changed)

        # Add waypoint
        self._btn_add_wp.clicked.connect(self._on_add_waypoint)

        # Table context menu
        self._table.customContextMenuRequested.connect(self._on_table_context_menu)

        # Tool buttons
        self._btn_move_up.clicked.connect(self._on_move_up)
        self._btn_move_down.clicked.connect(self._on_move_down)
        self._btn_delete.clicked.connect(self._on_delete_selected)
        self._btn_clear_all.clicked.connect(self._on_clear_all)

        # Table cell changes
        self._table.cellChanged.connect(self._on_cell_changed)

        # Manager data changed
        self._manager.data_changed.connect(self._refresh_table)

    # ── Slots ─────────────────────────────────────────────────────

    def _on_set_home(self):
        """Set the home position from the input fields."""
        wp = Waypoint(
            x=self._home_x.value(),
            y=self._home_y.value(),
            z=self._home_z.value(),
            label="Home",
        )
        self._manager.set_home(wp)
        self._update_home_status()
        self.home_set.emit(wp)
        self.waypoints_changed.emit()

    def _on_clear_home(self):
        """Clear the home position."""
        self._manager.set_home(None)
        self._update_home_status()
        self.home_cleared.emit()
        self.waypoints_changed.emit()

    def _on_wait_mode_changed(self, index: int):
        """Enable/disable the custom wait time input."""
        mode = self._wait_mode.currentData()
        self._wait_value.setEnabled(mode == "custom")

    def _on_add_waypoint(self):
        """Add a waypoint from the input fields."""
        mode = self._wait_mode.currentData()

        if mode == "default":
            wait_time = None
        elif mode == "infinite":
            wait_time = 0.0
        else:  # custom
            wait_time = self._wait_value.value()

        wp = Waypoint(
            x=self._wp_x.value(),
            y=self._wp_y.value(),
            z=self._wp_z.value(),
            wait_time=wait_time,
            record_flag=self._record_flag.isChecked(),
            label=self._wp_label.text().strip(),
        )
        self._manager.add(wp)
        self.waypoint_added.emit(wp)
        self.waypoints_changed.emit()

    def _on_table_context_menu(self, pos):
        """Show right-click context menu on the table."""
        menu = QMenu(self)
        menu.addAction("删除选中", self._on_delete_selected)
        menu.addAction("上移", self._on_move_up)
        menu.addAction("下移", self._on_move_down)
        menu.addSeparator()
        menu.addAction("清空全部", self._on_clear_all)
        menu.exec(self._table.viewport().mapToGlobal(pos))

    def _on_move_up(self):
        """Move selected waypoint up."""
        rows = set(item.row() for item in self._table.selectedItems())
        if len(rows) == 1:
            idx = rows.pop()
            self._manager.move_up(idx)
            # Re-select the moved row
            self._table.selectRow(max(0, idx - 1))

    def _on_move_down(self):
        """Move selected waypoint down."""
        rows = set(item.row() for item in self._table.selectedItems())
        if len(rows) == 1:
            idx = rows.pop()
            self._manager.move_down(idx)
            self._table.selectRow(min(self._manager.count() - 1, idx + 1))

    def _on_delete_selected(self):
        """Delete selected waypoints (in reverse order to preserve indices)."""
        rows = sorted(set(item.row() for item in self._table.selectedItems()), reverse=True)
        if not rows:
            return

        if len(rows) > 1:
            reply = QMessageBox.question(
                self, "确认删除",
                f"确定要删除 {len(rows)} 个航点吗?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if reply != QMessageBox.Yes:
                return

        for row in rows:
            self._manager.remove(row)
            self.waypoint_removed.emit(row)

        self.waypoints_changed.emit()

    def _on_clear_all(self):
        """Clear all waypoints."""
        if self._manager.count() == 0:
            return

        reply = QMessageBox.question(
            self, "确认清空",
            f"确定要清空全部 {self._manager.count()} 个航点吗?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply == QMessageBox.Yes:
            self._manager.clear()
            self.waypoints_changed.emit()

    def _on_cell_changed(self, row: int, col: int):
        """Handle inline cell edits in the waypoint table."""
        if self._suppress_changes:
            return

        wp = self._manager.get(row)
        if wp is None:
            return

        item = self._table.item(row, col)
        if item is None:
            return

        text = item.text().strip()

        try:
            if col == 1:  # X
                wp.x = float(text)
            elif col == 2:  # Y
                wp.y = float(text)
            elif col == 3:  # Z
                wp.z = float(text)
            elif col == 4:  # Wait time
                text_lower = text.lower()
                if text_lower in ('默认', 'default', '', '-'):
                    wp.wait_time = None
                elif text_lower in ('∞', '永久', 'infinite', 'inf', '0'):
                    wp.wait_time = 0.0
                else:
                    wp.wait_time = float(text)
            elif col == 6:  # Label
                wp.label = text
            else:
                return  # Not editable

            self._manager.update(row, wp)
            self.waypoints_changed.emit()
        except (ValueError, IndexError):
            pass  # Ignore invalid edits; revert on refresh

    def _on_record_toggled(self, idx: int, checked: bool):
        """Handle recording flag checkbox toggle in the table."""
        wp = self._manager.get(idx)
        if wp is None:
            return
        wp.record_flag = checked
        self._manager.update(idx, wp)
        self.waypoints_changed.emit()

    # ── Public Methods ────────────────────────────────────────────

    def set_home_input(self, x: float, y: float, z: float):
        """Pre-fill the home position input fields."""
        self._home_x.setValue(x)
        self._home_y.setValue(y)
        self._home_z.setValue(z)

    def set_waypoint_input(self, x: float, y: float, z: float):
        """Pre-fill the waypoint input fields (called from 3D pick)."""
        self._wp_x.setValue(x)
        self._wp_y.setValue(y)
        self._wp_z.setValue(z)

    def refresh(self):
        """Force a full refresh from the manager."""
        self._refresh_table()

    # ── Internal ──────────────────────────────────────────────────

    def _refresh_table(self):
        """Rebuild the table from the manager's data."""
        self._suppress_changes = True

        wps = self._manager.get_all()
        self._table.setRowCount(len(wps))

        for i, wp in enumerate(wps):
            # Row number
            self._set_item(i, 0, str(i + 1), editable=False)

            # Coordinates
            self._set_item(i, 1, f"{wp.x:.3f}")
            self._set_item(i, 2, f"{wp.y:.3f}")
            self._set_item(i, 3, f"{wp.z:.3f}")

            # Wait time
            if wp.wait_time is None:
                wait_text = "默认"
            elif wp.wait_time == 0.0:
                wait_text = "∞"
            else:
                wait_text = f"{wp.wait_time:.1f}s"
            self._set_item(i, 4, wait_text)

            # Recording flag — centered checkbox
            cb = QCheckBox()
            cb.setChecked(wp.record_flag)
            cb.setStyleSheet("margin-left: 10px;")
            # Capture index for the lambda closure
            cb.toggled.connect(
                lambda checked, _i=i: self._on_record_toggled(_i, checked)
            )
            cb_widget = QWidget()
            cb_layout = QHBoxLayout(cb_widget)
            cb_layout.addWidget(cb)
            cb_layout.setAlignment(Qt.AlignCenter)
            cb_layout.setContentsMargins(0, 0, 0, 0)
            self._table.setCellWidget(i, 5, cb_widget)

            # Label
            self._set_item(i, 6, wp.label)

        # Update status
        n = self._manager.count()
        self._status_label.setText(f"共 {n} 个航点")

        # Enable/disable tool buttons
        self._btn_clear_all.setEnabled(n > 0)
        self._btn_delete.setEnabled(n > 0)
        self._btn_move_up.setEnabled(n > 1)
        self._btn_move_down.setEnabled(n > 1)

        self._update_home_status()

        self._suppress_changes = False

    def _set_item(self, row: int, col: int, text: str,
                   editable: bool = True):
        """Set a table item."""
        item = QTableWidgetItem(text)
        item.setTextAlignment(Qt.AlignCenter)
        if not editable:
            item.setFlags(item.flags() & ~Qt.ItemIsEditable)
        self._table.setItem(row, col, item)

    def _update_home_status(self):
        """Update the home status display."""
        home = self._manager.get_home()
        if home:
            self._home_status.setText(
                f"◆ Home: ({home.x:.2f}, {home.y:.2f}, {home.z:.2f})"
            )
            self._home_status.setStyleSheet("color: #4caf50; font-weight: bold;")
        else:
            self._home_status.setText("◆ 未设置起降点")
            self._home_status.setStyleSheet("color: #888;")
