"""
Launch file generation configuration dialog.

Provides a tabbed dialog for configuring all ROS parameters
and previewing the waypoint list before generating the launch file.
"""

import os
from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QTabWidget,
    QGroupBox, QFormLayout, QLineEdit, QDoubleSpinBox,
    QCheckBox, QTableWidget, QTableWidgetItem, QHeaderView,
    QPushButton, QFileDialog, QMessageBox, QLabel,
    QDialogButtonBox, QWidget,
)

from core.waypoint_manager import WaypointManager
from ros_utils.launch_generator import LaunchConfig, save_launch_file


class LaunchDialog(QDialog):
    """Dialog for configuring and generating ROS launch files.

    Tab 1: ROS parameter configuration
    Tab 2: Waypoint preview
    """

    def __init__(self, waypoint_manager: WaypointManager, parent=None):
        super().__init__(parent)
        self._manager = waypoint_manager
        self._config: LaunchConfig = LaunchConfig()
        self._generated_path: str = ""

        self.setWindowTitle("生成 ROS Launch 文件")
        self.setMinimumSize(600, 500)
        self._setup_ui()
        self._load_current_waypoints()

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        # Tab widget
        tabs = QTabWidget()

        # Tab 1: ROS parameters
        tab_params = self._create_params_tab()
        tabs.addTab(tab_params, "ROS 参数")

        # Tab 2: Waypoint preview
        tab_preview = self._create_preview_tab()
        tabs.addTab(tab_preview, "航点预览")

        layout.addWidget(tabs)

        # Output path selection
        path_layout = QHBoxLayout()
        path_layout.addWidget(QLabel("输出路径:"))
        self._output_path = QLineEdit()
        self._output_path.setReadOnly(True)
        path_layout.addWidget(self._output_path)
        self._btn_browse = QPushButton("浏览...")
        self._btn_browse.clicked.connect(self._on_browse)
        path_layout.addWidget(self._btn_browse)
        layout.addLayout(path_layout)

        # Bottom buttons
        btn_box = QDialogButtonBox()
        self._btn_generate = QPushButton("生成 Launch 文件")
        self._btn_generate.setStyleSheet(
            "QPushButton { background-color: #1565c0; color: white; "
            "padding: 8px 24px; font-size: 14px; }"
        )
        self._btn_cancel = QPushButton("取消")
        btn_box.addButton(self._btn_generate, QDialogButtonBox.AcceptRole)
        btn_box.addButton(self._btn_cancel, QDialogButtonBox.RejectRole)
        btn_box.accepted.connect(self._on_generate)
        btn_box.rejected.connect(self.reject)
        layout.addWidget(btn_box)

    def _create_params_tab(self) -> QWidget:
        """Create the ROS parameters configuration tab."""
        widget = QWidget()
        layout = QVBoxLayout(widget)

        # Node config
        node_group = QGroupBox("节点配置")
        node_form = QFormLayout(node_group)

        self._pkg = QLineEdit("uav_px4_ctrl")
        node_form.addRow("包名 (pkg):", self._pkg)

        self._node_type = QLineEdit("mission_manger")
        node_form.addRow("节点类型 (type):", self._node_type)

        self._node_name = QLineEdit("mission_manger")
        node_form.addRow("节点名 (name):", self._node_name)

        layout.addWidget(node_group)

        # Topic config
        topic_group = QGroupBox("话题配置")
        topic_form = QFormLayout(topic_group)

        self._odom_topic = QLineEdit("/localization")
        self._odom_topic.setToolTip("里程计话题（优先级高于 pose_topic）")
        topic_form.addRow("odom_topic:", self._odom_topic)

        self._pose_topic = QLineEdit("/mavros/local_position/pose")
        topic_form.addRow("pose_topic:", self._pose_topic)

        self._goal_topic = QLineEdit("/goal_pose")
        topic_form.addRow("goal_topic:", self._goal_topic)

        layout.addWidget(topic_group)

        # Parameter config
        param_group = QGroupBox("任务参数")
        param_form = QFormLayout(param_group)

        self._distance_threshold = QDoubleSpinBox()
        self._distance_threshold.setRange(0.01, 100)
        self._distance_threshold.setValue(0.5)
        self._distance_threshold.setDecimals(2)
        self._distance_threshold.setSuffix(" m")
        param_form.addRow("distance_threshold:", self._distance_threshold)

        self._wait_time = QDoubleSpinBox()
        self._wait_time.setRange(0.1, 9999)
        self._wait_time.setValue(5.0)
        self._wait_time.setDecimals(1)
        self._wait_time.setSuffix(" s")
        self._wait_time.setToolTip("到达航点后的默认等待时间")
        param_form.addRow("wait_time:", self._wait_time)

        self._start_delay = QDoubleSpinBox()
        self._start_delay.setRange(0, 9999)
        self._start_delay.setValue(3.0)
        self._start_delay.setDecimals(1)
        self._start_delay.setSuffix(" s")
        param_form.addRow("start_delay:", self._start_delay)

        self._topic_timeout = QDoubleSpinBox()
        self._topic_timeout.setRange(0.1, 9999)
        self._topic_timeout.setValue(2.0)
        self._topic_timeout.setDecimals(1)
        self._topic_timeout.setSuffix(" s")
        param_form.addRow("topic_timeout:", self._topic_timeout)

        self._mission_cycle = QCheckBox("循环执行任务")
        self._mission_cycle.setChecked(True)
        param_form.addRow("mission_cycle:", self._mission_cycle)

        layout.addWidget(param_group)
        layout.addStretch()
        return widget

    def _create_preview_tab(self) -> QWidget:
        """Create the waypoint preview tab."""
        widget = QWidget()
        layout = QVBoxLayout(widget)

        # Waypoint table (read-only)
        self._preview_table = QTableWidget(0, 6)
        self._preview_table.setHorizontalHeaderLabels(
            ["#", "X", "Y", "Z", "等待", "录像"]
        )
        self._preview_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self._preview_table.setSelectionMode(QTableWidget.NoSelection)
        self._preview_table.setAlternatingRowColors(True)
        self._preview_table.horizontalHeader().setStretchLastSection(True)
        self._preview_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.Stretch
        )
        self._preview_table.verticalHeader().setVisible(False)
        layout.addWidget(self._preview_table)

        # Waypoint string preview
        layout.addWidget(QLabel("生成的航点字符串:"))
        self._waypoint_str_label = QLabel()
        self._waypoint_str_label.setWordWrap(True)
        self._waypoint_str_label.setStyleSheet(
            "QLabel { background-color: #1e1e1e; color: #4fc3f7; "
            "padding: 8px; border-radius: 4px; font-family: monospace; }"
        )
        layout.addWidget(self._waypoint_str_label)

        # Count
        self._preview_count = QLabel()
        layout.addWidget(self._preview_count)

        return widget

    # ── Slots ─────────────────────────────────────────────────────

    def _on_browse(self):
        """Open file dialog to select output path."""
        default_name = f"mission_{datetime.now().strftime('%Y%m%d_%H%M%S')}.launch"
        path, _ = QFileDialog.getSaveFileName(
            self, "保存 Launch 文件",
            os.path.join(os.path.expanduser("~"), default_name),
            "Launch files (*.launch);;All files (*)",
        )
        if path:
            self._output_path.setText(path)

    def _on_generate(self):
        """Generate and save the launch file."""
        path = self._output_path.text().strip()
        if not path:
            QMessageBox.warning(self, "路径为空", "请先选择输出文件路径。")
            return

        # Build config from UI
        self._config = LaunchConfig(
            pkg=self._pkg.text().strip(),
            node_type=self._node_type.text().strip(),
            node_name=self._node_name.text().strip(),
            odom_topic=self._odom_topic.text().strip(),
            pose_topic=self._pose_topic.text().strip(),
            goal_topic=self._goal_topic.text().strip(),
            distance_threshold=self._distance_threshold.value(),
            wait_time=self._wait_time.value(),
            start_delay=self._start_delay.value(),
            topic_timeout=self._topic_timeout.value(),
            mission_cycle=self._mission_cycle.isChecked(),
            waypoints=self._manager.to_waypoint_string(),
        )

        try:
            save_launch_file(self._config, path, self._manager.count())
            self._generated_path = path
            QMessageBox.information(
                self, "生成成功",
                f"Launch 文件已保存到:\n{path}\n\n"
                f"航点数量: {self._manager.count()}"
            )
            self.accept()
        except Exception as e:
            QMessageBox.critical(
                self, "生成失败",
                f"保存文件时出错:\n{str(e)}"
            )

    def _load_current_waypoints(self):
        """Populate the preview tab with current waypoints."""
        wps = self._manager.get_all()
        n = len(wps)

        self._preview_table.setRowCount(n)
        for i, wp in enumerate(wps):
            self._preview_table.setItem(i, 0,
                QTableWidgetItem(str(i + 1)))
            self._preview_table.setItem(i, 1,
                QTableWidgetItem(f"{wp.x:.3f}"))
            self._preview_table.setItem(i, 2,
                QTableWidgetItem(f"{wp.y:.3f}"))
            self._preview_table.setItem(i, 3,
                QTableWidgetItem(f"{wp.z:.3f}"))

            if wp.wait_time is None:
                wt = "默认"
            elif wp.wait_time == 0.0:
                wt = "∞"
            else:
                wt = f"{wp.wait_time:.1f}s"
            self._preview_table.setItem(i, 4, QTableWidgetItem(wt))
            self._preview_table.setItem(i, 5,
                QTableWidgetItem("✓" if wp.record_flag else "—"))

        self._waypoint_str_label.setText(self._manager.to_waypoint_string())
        self._preview_count.setText(f"共 {n} 个航点" +
            (" + 起降点" if self._manager.has_home() else ""))

    @property
    def generated_path(self) -> str:
        """Return the path of the last generated launch file."""
        return self._generated_path
