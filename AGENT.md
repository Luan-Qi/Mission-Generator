# AGENT.md — Mission Generator 项目文档

本文档面向 AI Agent（Claude Code 等）提供项目架构、设计决策、数据流和开发约定，便于后续迭代和维护。

---

## 项目概述

**Mission Generator** 是一个基于 PySide6 + vispy 的跨平台桌面应用，用于：

1. 3D 可视化 PCD 点云文件
2. 在点云上选取任务航点（鼠标点选 + 手动输入）
3. 生成与 `mission_manger.cpp` 兼容的 ROS1 launch 文件

**目标用户**：无人机操作员 / 任务规划人员
**目标平台**：Linux / Windows / macOS
**分发方式**：PyInstaller 打包为独立可执行文件

**参考源码（只读，不随本仓库分发）**：
- `/UAV-Super/src/uav_px4_ctrl/src/mission_manger.cpp` — WaypointPublisher 类
- `/UAV-Super/src/uav_px4_ctrl/launch/mission_manger.launch` — 参考 launch 格式

---

## 技术栈与设计决策

### 为什么不用 Open3D

| 决策 | 原因 |
|---|---|
| **放弃 Open3D** | 安装体积 200-250MB，打包后 +250-500MB；系统依赖链复杂（libtbb, filament, glfw, X11, BLAS）；AVX/AVX2 指令兼容风险；PyInstaller hidden-import 和 .so 路径问题 |
| **自研 PCD 解析器** | PCD 格式简单（ASCII 文本头 + 三种数据模式），~150 行纯 Python 即可完整支持 |
| **vispy 渲染** | 仅 4MB，原生嵌入 Qt，高性能 OpenGL 渲染，仅需 OpenGL 2.1+ GPU 驱动 |

### 打包方案

打包后整个程序约 **100-130 MB**（vs 400-700MB 如果用 Open3D）。
目标机器无需安装 Python 或任何 pip 包，仅需 OpenGL 2.1+ 驱动。

---

## 文件结构

```
mission_generator/
├── main.py                    # 应用入口
├── build.py                   # PyInstaller 打包脚本
├── requirements.txt           # pip 依赖清单
├── README.md                  # 用户文档
├── AGENT.md                   # 本文档（Agent 文档）
├── ui/
│   ├── __init__.py
│   ├── main_window.py         # QMainWindow — 整合所有组件
│   ├── point_cloud_canvas.py  # vispy SceneCanvas — 3D 渲染 + 鼠标选点
│   ├── waypoint_panel.py      # QWidget — 航点编辑面板
│   └── launch_dialog.py       # QDialog — Launch 生成配置
├── core/
│   ├── __init__.py
│   ├── pcd_parser.py          # 纯 Python PCD 文件解析器
│   └── waypoint_manager.py    # 航点数据模型 + CRUD + 序列化
└── ros_utils/
    ├── __init__.py
    └── launch_generator.py    # ROS1 XML launch 生成器
```

---

## 模块间数据流

```
用户操作
  │
  ├─ 打开PCD → pcd_parser.read_pcd()
  │              └─→ PCDData (points, colors)
  │                   └─→ PointCloudCanvas.set_point_cloud()
  │
  ├─ 鼠标选点 → PointCloudCanvas._perform_pick()
  │               └─→ sig_point_picked (x, y, z)
  │                    └─→ WaypointPanel.set_waypoint_input()
  │
  ├─ 添加航点 → WaypointManager.add()
  │               └─→ data_changed signal
  │                    ├─→ WaypointPanel._refresh_table()
  │                    └─→ MainWindow._update_all()
  │                         └─→ PointCloudCanvas.set_waypoints()
  │
  └─ 生成Launch → LaunchDialog
                    └─→ WaypointManager.to_waypoint_string()
                    └─→ LaunchConfig + save_launch_file()
```

### 信号流图

```
PointCloudCanvas.sig_point_picked ──→ MainWindow._on_point_picked
                                            │
                                            └→ WaypointPanel.set_waypoint_input()

WaypointPanel.waypoints_changed ──→ MainWindow._on_waypoints_changed
WaypointPanel.home_set ──────────→ MainWindow._on_home_changed
WaypointPanel.home_cleared ──────→ MainWindow._on_home_changed

WaypointManager.data_changed ────→ WaypointPanel._refresh_table
```

---

## 关键算法

### 1. PCD 文件解析 (`pcd_parser.py`)

**Header 解析**：逐行读取 ASCII 行，直到 `DATA` 行，提取 `FIELDS / SIZE / TYPE / COUNT / POINTS` 等字段。

**Data 读取**（三种模式）：
- **ASCII**：`numpy.loadtxt()`，跳过 header 行
- **Binary**：`struct.unpack` 逐点读取，point_size = `sum(SIZE[i] * COUNT[i])`
- **Binary Compressed**：LZF 解压 + 去平面化（structure-of-arrays → array-of-structures）

**依赖**：`binary_compressed` 需要 `python-lzf`（可选，~10KB）

### 2. 航点字符串生成 (`waypoint_manager.py`)

**必须严格匹配 C++ 正则**（`mission_manger.cpp` 第 139-144 行）：

```regex
\[\s*([-0-9\.eE]+)\s*,\s*([-0-9\.eE]+)\s*,\s*([-0-9\.eE]+)(?:\s*,\s*([0-9\.eE]+))?\s*\]\s*(\*?)
```

**规则**：
- 前 3 个值必填（x, y, z）
- 第 4 个值（wait_time）可选：省略 = 使用默认，0 = 无限等待
- `*` 后缀：录像标记，可选
- 全局格式：`[wp1, wp2, ...]`，用 `, ` 分隔

### 3. vispy 点选 (`point_cloud_canvas.py`)

```
屏幕坐标 → camera._vispy_get_ray() → 射线 (origin, direction)
  → 计算所有点到射线的垂直距离
  → 在阈值内（<2m）且相机前方的点中取最近者
  → 发射 sig_point_picked(x, y, z)
```

选点精度取决于点云密度——在稀疏区域可能选不到点。

---

## 开发约定

### 命名规范

- **类名**：PascalCase（`WaypointManager`, `PointCloudCanvas`）
- **方法/函数**：snake_case（`to_waypoint_string`, `set_point_cloud`）
- **私有成员**：前缀 `_`（`_waypoints`, `_pcd_points`）
- **信号**：`snake_case` + 描述性名称（`data_changed`, `point_picked`）
- **文件**：snake_case（`waypoint_manager.py`, `launch_generator.py`）

### 编码模式

- **Qt 信号/槽**：用 `Signal` + `connect`（不用装饰器）
- **类型注解**：关键 API 使用 `typing` 注解
- **错误处理**：UI 层用 `QMessageBox`，Core 层抛异常
- **数据不可变**：`get_all()` 返回拷贝，防止外部修改

### 坐标系统

- 使用 ROS 标准 frame：默认 `map`
- 坐标顺序：(X, Y, Z) — X 前，Y 左，Z 上（ENU）
- vispy 内部使用与 ROS 相同的右手坐标系

---

## 航点属性映射

| 程序字段 | C++ 表示 | 字符串格式 |
|---|---|---|
| `wait_time = None` | 使用全局默认 `wait_time_` | 不写第4参数 |
| `wait_time = 0.0` | 无限等待 | `[x, y, z, 0]` |
| `wait_time = 5.0` | 自定义 5 秒 | `[x, y, z, 5.0]` |
| `record_flag = True` | `*` 后缀 | `[x, y, z]*` |
| `record_flag = False` | 无后缀 | `[x, y, z]` |

---

## 打包与发布

### 打包

```bash
# 安装打包工具
pip install pyinstaller

# 一键打包
python build.py

# 清理
python build.py --clean
```

**打包产物**：

| 平台 | 输出 | 说明 |
|------|------|------|
| **Linux** | `dist/MissionGenerator` | ELF 可执行文件 |
| | `dist/MissionGenerator.desktop` | Freedesktop 桌面入口文件 |
| | `dist/install.sh` | 一键安装脚本（含桌面集成和图标） |
| | `dist/icon.png` | 应用图标副本 |
| **Windows** | `dist/MissionGenerator.exe` | PE 可执行文件（嵌入多分辨率 .ico 图标） |
| **macOS** | `dist/MissionGenerator.app` | 应用包（嵌入图标） |

**打包特性**：

- **图标嵌入**：Windows 自动从 `icon.png` 生成 6 种尺寸 `.ico` 并通过 `--icon` 嵌入 PE；Linux 生成 `.desktop` + `install.sh` 实现桌面图标集成；macOS 通过 `--icon` 嵌入
- **运行时图标查找**：`main.py` 和 `ui/main_window.py` 均支持 `sys._MEIPASS`（PyInstaller 临时解压目录），优先从 bundle 查找 `icon.png`
- **精确数据收集**：`build.py` 精确添加 `icon.png`、`core/`、`ui/`、`ros_utils/` 而非整个项目目录，避免打包垃圾文件
- **排除大型库**：排除 matplotlib、scipy、pandas、PIL、tkinter，减小体积

### GitHub 上传准备

项目根目录包含 `.gitignore`，已排除：

- Python 字节码（`__pycache__/`、`*.pyc`）
- 虚拟环境（`venv/`、`.venv/`）
- IDE 文件（`.vscode/`、`.idea/`）
- PyInstaller 输出（`dist/`、`build/`、`*.spec`、`icon.ico`）
- OS 文件（`.DS_Store`、`Thumbs.db`）

### 用户文档

- **`README.md`** — 面向用户的安装与使用简介
- **`USER_GUIDE.md`** — 12,000+ 字中文操作手册（15 章：界面概览、逐步操作、快捷键速查、常见问题解答）

### 版本发布检查清单

- [ ] `python main.py` 启动正常
- [ ] 加载 PCD 文件 → 3D 渲染正常
- [ ] 鼠标点选 → 航点添加正常
- [ ] 手动输入 → 航点编辑正常
- [ ] 起降点 → 路径连线正常
- [ ] 生成 launch 文件 → 格式与参考一致
- [ ] 航点字符串 → C++ 正则可解析
- [ ] `python build.py` → 打包成功
- [ ] 打包后的二进制在干净环境可运行

---

## 已知限制与改进方向

### 限制

- 点云渲染对小 GPU 内存可能不足（百万点级别需测试）
- 选点精度受点云密度影响
- vispy 文本标签不支持中文（英文字体）
- 不生成配套的 publisher 脚本（假设使用者已有 `mission_manger` 节点）

### 改进方向

- **预设航点模板**：常用任务模式（如扫描网格、围绕航点）
- **航点属性批量编辑**：多选航点统一设置等待时间/录像标记
- **PCD 点云优化**：支持降采样显示（点云过大时）
- **3D 视图截图**：导出当前视角为图片
- **KML 导入导出**：从 Google Earth 等工具导入航点
- **ROS2 支持**：生成 ROS2 Python launch 文件格式
- **i18n**：国际化支持

---

## 启动与调试

```bash
# 从源码运行
python main.py

# 开启 vispy 调试日志
VISPY_DEBUG=1 python main.py

# 生成测试 PCD 文件
python -c "
from core.pcd_parser import generate_test_pcd
generate_test_pcd('test.pcd', 10000)
"
```

---

## 迭代记录

> **规则**：每次对项目进行实质性修改后，必须在下方追加一条迭代记录。
> 格式：`版本号 | 日期 | 作者 | 变更摘要 | 涉及文件`

| 版本 | 日期 | 作者 | 变更摘要 | 涉及文件 |
|---|---|---|---|---|
| v1.0 | 2026-06-26 | Claude Code | **初始版本** — 完成全部核心功能：PCD 解析器（ASCII/Binary/Compressed）、3D 点云可视化（vispy）、航点管理（CRUD + 等待时间 + 录像标记 + JSON 持久化）、起降点可视化（含路径连线）、ROS1 launch 生成器（mission_manger.cpp 兼容）、PyInstaller 打包脚本、README + AGENT 文档。技术决策：放弃 Open3D（体积/依赖问题），自研轻量 PCD 解析器。 | 全部文件（初始提交） |
| v1.0.1 | 2026-06-26 | Claude Code | **修复鼠标交互报错** — 移除 `on_mouse_press`/`on_mouse_release`/`on_mouse_move` 中对 `super()` 的无效调用。vispy `SceneCanvas` 未定义这些方法，导致中键/右键/修饰键操作时持续抛 `AttributeError`，且平移功能失效。vispy 的 TurntableCamera 通过独立的事件系统处理旋转/平移/缩放，不依赖方法覆盖。同时完善拖拽检测逻辑（5px 阈值区分点击与拖拽）。 | `ui/point_cloud_canvas.py` |
| v1.0.2 | 2026-06-26 | Claude Code | **修复 PCD 加载报错 + Ctrl+C 退出** — (1) 移除 `Markers.set_data()` 中的 `scaling=True` 参数（vispy 0.16 不支持该参数，需通过 `visual.scaling` 属性设置），修复 PCD 文件打开时报 `unexpected keyword argument 'scaling'` 的问题；(2) 在 `main.py` 添加 `SIGINT` 信号处理器 + `QTimer` 轮询，使 `Ctrl+C` 可正常退出 Qt 事件循环；(3) `closeEvent` 增加 `force_close` 属性检查，Ctrl+C 时跳过确认对话框。 | `ui/point_cloud_canvas.py`, `main.py`, `ui/main_window.py` |
| v1.1 | 2026-06-26 | Claude Code | **重大 UI 增强** — (1) **中键平移**：新增 `MissionCamera` 类（继承 TurntableCamera），将中键拖拽从缩放重映射为平移，同时保留 Shift+左键平移和 Shift+中键 FOV 调整；(2) **右键菜单添加航点**：右键点击点云 → ray-cast 拾取 → 弹出 `WaypointAddDialog`（含坐标微调、等待时间、录像标记、标签）→ 确认后直接添加航点并 3D 标记；(3) **渲染设置对话框**：新增 `RenderingDialog`（工具栏 ⚙️ 渲染），可调节点大小(1-20px)、透明度(5-100%)、颜色模式(5种)、航点大小、航线宽度、航点深度测试开关；(4) **深度测试控制**：航点和起降点默认关闭深度测试（始终绘制在最上层，不被点云遮挡）；(5) **调试日志**：`[app]` `[canvas]` 前缀的关键操作日志；(6) **应用图标**：程序化生成 256x256 靶标风格 icon.png；(7) 工具栏增加操作提示文本。 | `ui/point_cloud_canvas.py`, `ui/main_window.py`, `main.py`, `icon.png` |
| v1.1.1 | 2026-06-26 | Claude Code | **回退自定义相机，恢复标准鼠标操作** — (1) 移除 `MissionCamera` 类（其 `PerspectiveCamera` 导入在 vispy 0.16 中路径不兼容，且中键重映射导致持续报错）；(2) 恢复标准 `TurntableCamera`（左拖=旋转，滚轮=缩放）；(3) ~~修正 vispy 按钮号映射~~（此修复有误，见 v1.1.2）；(4) 初始化 `_is_picking` 和 `_mouse_press_pos` 成员变量；(5) 简化 `on_mouse_*` 处理器；(6) 更新工具栏提示文字。保留所有 v1.1 功能。 | `ui/point_cloud_canvas.py`, `ui/main_window.py` |
| v1.1.2 | 2026-06-26 | Claude Code | **修复 vispy 0.16.2 导入 + 按钮号** — (1) 将 `visuals.Markers`/`Text`/`Line`/`XYZAxis` 从 `vispy.visuals` 改为 `vispy.scene.visuals`（vispy 0.16.2 中这些类仅在 `scene.visuals` 中导出，不在顶层 `visuals` 模块）。修复 PCD 加载时报 `module 'vispy.visuals' has no attribute 'Markers'` 的问题；(2) 修正右键按钮号：vispy Qt 后端 `BUTTONMAP = {1:左, 2:右, 4:中}`，即 `event.button==2` 才是右键。之前错误地将 `button==3`（中键）当作右键，导致中键和右键功能互换。 | `ui/point_cloud_canvas.py` |
| v1.1.3 | 2026-06-26 | Claude Code | **修复点选 + 多个改进** — (1) **重写射线拾取算法**：`_vispy_get_ray` 在 vispy 0.16.2 中已移除，改为从 TurntableCamera 参数（azimuth/elevation/distance/center/fov）手动计算相机位置和屏幕射线方向，再执行最近邻点搜索。拾取半径从 2m 扩大到 3m；(2) **改为中键添加航点**：右键留给 vispy 默认的缩放操作，中键点击弹出航点对话框；(3) **加速 Shift+左键平移**：设置 `translate_speed=3.0`，平移速度提升 3 倍；(4) **航线上添加箭头**：在每段路径中点放置橙色三角标记指示运动方向；(5) **修复退出崩溃**：新增 `cleanup()` 方法，在 closeEvent 中先分离所有 vispy 可视对象再销毁 Qt 窗口，解决 `QObject was deleted directly` 和 `malloc_consolidate` 双重释放问题。 | `ui/point_cloud_canvas.py`, `ui/main_window.py` |
| v1.2 | 2026-06-26 | Claude Code | **重构渲染管线 + 预览标记 + 悬浮对话框** — (1) **修复首次航点黑块**：将 Markers 类可视化对象从"按需创建/销毁"改为"全部预创建 + 仅更新数据"。所有 Markers/Line 在 `__init__` 中创建（用 size=0 的 dummy 点初始化 GPU 缓冲区），后续只调用 `set_data()` 更新。避免运行中创建新 visual 导致的 GPU shader 重编译/缓冲区重分配闪烁。Text 类标签改为延迟按需创建（见 v1.2.1）；(2) **实时预览标记**：中键拾取点后立即显示黄色菱形 (◇) 预览标记，对话框修改坐标时标记实时跟随；(3) **悬浮非模态对话框**：`WaypointAddDialog` 改为浮动非模态窗口，用户可边旋转视角边微调坐标；(4) **移除多余的 `self.update()` 调用**。 | `ui/point_cloud_canvas.py`, `ui/main_window.py` |
| v1.2.1 | 2026-06-26 | Claude Code | **内存优化 + 关闭文件 + 平移速度 + 新文件清空航点** — (1) **内存从 ~1GB 降到 ~178MB**：移除 256 个 Text 视觉的预分配（每个 vispy Text 占用约 3.5MB GPU 内存），改为按需创建/销毁（`_refresh_markers` 中动态管理 `_wp_labels` 列表）。Markers 仍预创建（每个仅 ~0.1MB）；(2) **关闭文件**：菜单栏"文件 → 关闭文件（Ctrl+W）"，清空点云和航点，有航点时弹出确认对话框；(3) **打开新 PCD 自动清空航点**：避免旧航点残留；(4) **平移速度调节**：渲染设置对话框滑块，修改 `TurntableCamera.translate_speed`。 | `ui/point_cloud_canvas.py`, `ui/main_window.py` |
| v1.2.2 | 2026-06-26 | Claude Code | **PCD 点数限制 + 坐标轴黑块 + 箭头增大 + 航线颜色 + 平移速度默认值** — (1) **PCD 点数限制**：`read_pcd()` 中增加 10,000,000 点上限，超限抛 `ValueError` 并提示降采样；(2) **坐标轴黑块修复**：将 `XYZAxis` 放入独立 `_axis_node` 节点，设置 `depth_test=False` 避免与背景 Z-fighting；(3) **航线箭头增大**：箭头尺寸增大且 depth_test=False；(4) **航线颜色选择**：6 种预设颜色下拉选择；(5) **平移速度默认 20×，范围 1~100×**；(6) 移除 `ARROW_COLOR`/`PATH_COLOR`，改用 `PATH_COLOR_PRESETS`。 | `ui/point_cloud_canvas.py`, `ui/main_window.py`, `core/pcd_parser.py` |
| v1.2.3 | 2026-06-26 | Claude Code | **表格列宽 + 录像复选框 + 3D 方向箭头** — (1) **表格列宽**：X/Y/Z 列固定 64px，#=28px，等待=62px，录像=44px，标签自适应拉伸，默认窗口可显示全部 7 列；(2) **录像标记改为复选框**：表格"录像"列渲染为居中 `QCheckBox`，点击直接切换 `record_flag`；(3) **航线箭头改为 3D Arrow 视觉**：移除 billboarded `triangle_up` Markers（始终面朝相机，无方向指示作用），改用 `visuals.Arrow` 的 `arrows` 参数，在每个路径段中点放置真正的 3D 锥形箭头，箭头从上一航点指向下一航点方向。`Arrow` 视觉同时绘制连接线和箭头头。修复字段名 `header.points`（原 `header.point_count` 不存在）。 | `ui/point_cloud_canvas.py`, `ui/waypoint_panel.py`, `core/pcd_parser.py` |
| v1.2.4 | 2026-06-27 | Claude Code | **可执行文件图标 + .gitignore + 用户手册** — (1) **修复 PyInstaller 打包图标**：`build.py` 改为精确添加数据文件（`icon.png`、`core/`、`ui/`、`ros_utils/`）而非整个项目目录；Windows 构建自动从 `icon.png` 生成多分辨率 `.ico` 并通过 `--icon` 嵌入 PE 文件；Linux 构建自动生成 `.desktop` 文件和 `install.sh` 安装脚本（含桌面集成和图标安装）；macOS 构建通过 `--icon` 嵌入图标；(2) **PyInstaller 运行时图标查找**：`main.py` 和 `ui/main_window.py` 均支持 `sys._MEIPASS`（PyInstaller 临时解压目录），优先从 bundle 中查找 `icon.png`，回退到源目录；(3) **`.gitignore`**：涵盖 Python 字节码、虚拟环境、IDE 文件、PyInstaller 输出（`dist/`、`build/`、`*.spec`、`icon.ico`）、OS 文件等；(4) **`USER_GUIDE.md`**：12,000+ 字中文操作手册，包含界面概览图、逐步操作指南、鼠标/键盘操作表、航点编辑、渲染设置、Launch 生成、快捷键速查表、10 个常见问题解答；更新 `README.md` 引用用户手册。 | `build.py`, `main.py`, `ui/main_window.py`, `.gitignore`, `USER_GUIDE.md`, `README.md`, `AGENT.md` |
| v1.2.5 | 2026-06-27 | Claude Code | **起降点默认 Z + 录像段可视化** — (1) **起降点默认 Z 改为 1.0**：`waypoint_panel.py` 中 `_home_z.setValue(1.0)`，与地面高度区分；(2) **录像段独立颜色渲染**：目的地航点 `record_flag=True` 的飞行段，使用第二个 `visuals.Arrow`（`_path_rec_visual`）以不同颜色渲染。录像颜色由 `_get_recording_color()` 自动选择为当前航线颜色的下一个预设（"黄色→青色→洋红→橙红→白色→绿色→黄色"循环），用户无需手动配置。两个 Arrow 均使用 `connect='segments'` 模式，按段独立绘制；(3) **数据流扩展**：`PointCloudCanvas.set_waypoints()` 新增 `recording_flags` 参数（`List[bool]`），`MainWindow._update_all()` 同步传递每个航点的 `record_flag`；(4) `close_point_cloud()` 和 `cleanup()` 同步清理 `_path_rec_visual`。 | `ui/point_cloud_canvas.py`, `ui/waypoint_panel.py`, `ui/main_window.py`, `AGENT.md` |
| v1.2.6 | 2026-06-27 | Claude Code | **修复退出 SIGSEGV（根因修复）** — (1) **根因**：在 `closeEvent` 中调用 `cleanup()` 分离 vispy visual → 释放内部共享 GL 对象（shader/buffer）；但 `event.accept()` 后 Qt 开始递归销毁 widget 树 → 再次触发 vispy 内部析构访问同一批已释放的 GL 对象 → 双重释放 → `"shared QObject was deleted directly"` → SIGSEGV；(2) **修复策略**：将 `cleanup()` 从 `closeEvent` 移至 `QApplication.aboutToQuit` 信号。`aboutToQuit` 在所有窗口关闭后、Qt 开始销毁 widget/GL context **之前**触发 — 此时 GL context 仍有效且空闲（无 pending paint 事件），分离 vispy visual 安全无竞争；(3) `cleanup()` 内部改进：先通过 `set_data(dummy_zeros)` 将 GPU buffer 缩到最小；再分离 visual；不再操作 Qt widget parent；移除 `_signal_proxy.deleteLater()`（避免在 app 关闭时排程无效删除）；(4) `closeEvent` 简化为仅做确认对话框和 `event.accept()`，不再调用 `cleanup()`；(5) `main.py` 中 `app.aboutToQuit.connect(window._canvas.cleanup)`。 | `ui/point_cloud_canvas.py`, `ui/main_window.py`, `main.py`, `AGENT.md` |
