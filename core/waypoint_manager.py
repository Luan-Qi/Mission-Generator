"""
Waypoint data model and manager.

Manages a list of mission waypoints with per-waypoint properties:
- XYZ coordinates
- Wait time (default, custom, or infinite)
- Recording flag (*)
- Optional label

Also supports an optional Home position for visualization purposes.
"""

import json
import re
from dataclasses import dataclass, field
from typing import List, Optional

from PySide6.QtCore import QObject, Signal


@dataclass
class Waypoint:
    """A single mission waypoint.

    Attributes:
        x, y, z: Position coordinates in meters.
        wait_time:
            None = use global default
            0 = infinite wait (hover until external control)
            >0 = custom wait time in seconds
        record_flag: If True, mark with * for video recording control.
        label: Optional user-defined label.
        frame_id: ROS frame ID.
    """
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0
    wait_time: Optional[float] = None   # None=default, 0=infinite, >0=custom
    record_flag: bool = False
    label: str = ""
    frame_id: str = "map"

    def to_dict(self) -> dict:
        return {
            'x': self.x,
            'y': self.y,
            'z': self.z,
            'wait_time': self.wait_time,
            'record_flag': self.record_flag,
            'label': self.label,
            'frame_id': self.frame_id,
        }

    @classmethod
    def from_dict(cls, d: dict) -> 'Waypoint':
        return cls(
            x=float(d.get('x', 0)),
            y=float(d.get('y', 0)),
            z=float(d.get('z', 0)),
            wait_time=d.get('wait_time'),  # can be None
            record_flag=bool(d.get('record_flag', False)),
            label=str(d.get('label', '')),
            frame_id=str(d.get('frame_id', 'map')),
        )

    @property
    def position(self) -> tuple:
        """Return (x, y, z) tuple."""
        return (self.x, self.y, self.z)

    def __repr__(self) -> str:
        parts = [f"({self.x:.2f}, {self.y:.2f}, {self.z:.2f})"]
        if self.record_flag:
            parts.append("*")
        if self.wait_time == 0:
            parts.append("wait=∞")
        elif self.wait_time is not None and self.wait_time > 0:
            parts.append(f"wait={self.wait_time:.1f}s")
        if self.label:
            parts.append(f"'{self.label}'")
        return " ".join(parts)


# C++ regex from mission_manger.cpp for validation:
# \\[\\s*([-0-9\\.eE]+)\\s*,\\s*([-0-9\\.eE]+)\\s*,\\s*([-0-9\\.eE]+)(?:\\s*,\\s*([0-9\\.eE]+))?\\s*\\]\\s*(\\*?)
_WP_REGEX = re.compile(
    r'\[\s*([-0-9\.eE]+)\s*,\s*([-0-9\.eE]+)\s*,\s*([-0-9\.eE]+)'
    r'(?:\s*,\s*([0-9\.eE]+))?\s*\]\s*(\*?)'
)


class WaypointManager(QObject):
    """Manages an ordered list of waypoints and optional home position.

    Signals:
        data_changed: Emitted whenever the waypoint list or home changes.
    """

    data_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._waypoints: List[Waypoint] = []
        self._home: Optional[Waypoint] = None

    # ── Waypoint CRUD ──────────────────────────────────────────────

    def add(self, wp: Waypoint) -> None:
        """Append a waypoint to the end of the list."""
        self._waypoints.append(wp)
        self.data_changed.emit()

    def insert(self, index: int, wp: Waypoint) -> None:
        """Insert a waypoint at the given index."""
        self._waypoints.insert(index, wp)
        self.data_changed.emit()

    def remove(self, index: int) -> None:
        """Remove the waypoint at the given index."""
        if 0 <= index < len(self._waypoints):
            self._waypoints.pop(index)
            self.data_changed.emit()

    def update(self, index: int, wp: Waypoint) -> None:
        """Replace the waypoint at the given index."""
        if 0 <= index < len(self._waypoints):
            self._waypoints[index] = wp
            self.data_changed.emit()

    def clear(self) -> None:
        """Remove all waypoints."""
        self._waypoints.clear()
        self.data_changed.emit()

    def move_up(self, index: int) -> None:
        """Move waypoint at index one position earlier."""
        if 1 <= index < len(self._waypoints):
            self._waypoints[index], self._waypoints[index - 1] = \
                self._waypoints[index - 1], self._waypoints[index]
            self.data_changed.emit()

    def move_down(self, index: int) -> None:
        """Move waypoint at index one position later."""
        if 0 <= index < len(self._waypoints) - 1:
            self._waypoints[index], self._waypoints[index + 1] = \
                self._waypoints[index + 1], self._waypoints[index]
            self.data_changed.emit()

    # ── Accessors ──────────────────────────────────────────────────

    def get_all(self) -> List[Waypoint]:
        """Return a copy of all waypoints."""
        return list(self._waypoints)

    def get(self, index: int) -> Optional[Waypoint]:
        """Get waypoint at index, or None if out of range."""
        if 0 <= index < len(self._waypoints):
            return self._waypoints[index]
        return None

    def count(self) -> int:
        """Return the number of waypoints."""
        return len(self._waypoints)

    # ── Home position ──────────────────────────────────────────────

    def set_home(self, wp: Optional[Waypoint]) -> None:
        """Set or clear the home (takeoff/landing) position."""
        self._home = wp
        self.data_changed.emit()

    def has_home(self) -> bool:
        """Return True if a home position is set."""
        return self._home is not None

    def get_home(self) -> Optional[Waypoint]:
        """Return the home position, or None."""
        return self._home

    # ── String generation (C++ regex-compatible) ───────────────────

    def to_waypoint_string(self) -> str:
        """Generate the waypoint string in mission_manger.cpp format.

        Format: [[x, y, z]* , [x, y, z, wait_time], ...]

        The home position is NOT included in this string.

        Returns:
            A string parseable by the C++ regex in mission_manger.cpp.
        """
        if not self._waypoints:
            return "[]"

        parts = []
        for wp in self._waypoints:
            parts.append(self._waypoint_to_str(wp))

        return "[" + ", ".join(parts) + "]"

    @staticmethod
    def _waypoint_to_str(wp: Waypoint) -> str:
        """Convert a single waypoint to its string representation."""
        # Build coordinate part
        coord = f"[{_fmt(wp.x)}, {_fmt(wp.y)}, {_fmt(wp.z)}"

        # Add wait_time if not using default
        if wp.wait_time is not None:
            coord += f", {_fmt(wp.wait_time)}"

        coord += "]"

        # Add recording flag
        if wp.record_flag:
            coord += "*"

        return coord

    # ── Persistence ────────────────────────────────────────────────

    def to_dict(self) -> dict:
        """Serialize waypoints and home to a dictionary."""
        return {
            'waypoints': [wp.to_dict() for wp in self._waypoints],
            'home': self._home.to_dict() if self._home else None,
        }

    def from_dict(self, d: dict) -> None:
        """Load waypoints and home from a dictionary."""
        self._waypoints = [Waypoint.from_dict(wd) for wd in d.get('waypoints', [])]
        home_dict = d.get('home')
        self._home = Waypoint.from_dict(home_dict) if home_dict else None
        self.data_changed.emit()

    def save_json(self, path: str) -> None:
        """Save waypoints (including home) to a JSON file."""
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(self.to_dict(), f, indent=2, ensure_ascii=False)

    def load_json(self, path: str) -> None:
        """Load waypoints (including home) from a JSON file."""
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        self.from_dict(data)

    # ── Validation ─────────────────────────────────────────────────

    @staticmethod
    def validate_waypoint_string(s: str) -> bool:
        """Check if a waypoint string is parseable by the C++ regex.

        Args:
            s: Waypoint string to validate (e.g., "[[35.0, 0.0, 0.6]*]")

        Returns:
            True if the string can be parsed by the C++ regex.
        """
        if not s:
            return False
        # The C++ code loops over regex_search, so we use finditer
        matches = list(_WP_REGEX.finditer(s))
        return len(matches) > 0

    def __repr__(self) -> str:
        return f"WaypointManager({len(self._waypoints)} waypoints, home={'yes' if self._home else 'no'})"


def _fmt(val: float) -> str:
    """Format a float value for the waypoint string.

    Uses .1f for most values, but .0f for whole numbers to match the
    existing launch file style.
    """
    if val == int(val) and abs(val) < 1000:
        return f"{val:.1f}"
    # Check if it has a meaningful fractional part
    rounded = round(val, 1)
    if abs(val - rounded) < 0.001:
        return f"{val:.1f}"
    return f"{val:.2f}"
