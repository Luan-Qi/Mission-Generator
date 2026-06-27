"""
Pure Python PCD (Point Cloud Data) file parser.

Supports all three PCD formats:
- ASCII: human-readable text
- Binary: raw binary dump
- Binary Compressed: LZF-compressed with planar layout

Reference: https://pcl.readthedocs.io/en/latest/pcd_file_format.html
"""

import struct
import re
from dataclasses import dataclass, field
from typing import List, Optional, Tuple
from pathlib import Path

import numpy as np


# Mapping from PCD type characters to struct format and numpy dtype
_TYPE_MAP = {
    'I': ('i', np.int32),    # signed int 32
    'U': ('I', np.uint32),   # unsigned int 32
    'F': ('f', np.float32),  # float 32
}


@dataclass
class PCDHeader:
    """Parsed PCD file header."""
    version: str = "0.7"
    fields: List[str] = field(default_factory=list)
    size: List[int] = field(default_factory=list)
    type: List[str] = field(default_factory=list)
    count: List[int] = field(default_factory=list)
    width: int = 0
    height: int = 0
    viewpoint: List[float] = field(default_factory=lambda: [0, 0, 0, 1, 0, 0, 0])
    points: int = 0
    data_mode: str = "ascii"


@dataclass
class PCDData:
    """Parsed PCD point cloud data."""
    points: np.ndarray          # (N, 3) — XYZ coordinates
    colors: Optional[np.ndarray] = None  # (N, 3) — RGB values (0.0-1.0) or None
    fields: List[str] = field(default_factory=list)
    point_count: int = 0

    def __repr__(self) -> str:
        has_color = self.colors is not None
        return (f"PCDData(points={self.points.shape}, "
                f"has_color={has_color}, "
                f"fields={self.fields})")


def parse_header(filepath: str) -> PCDHeader:
    """Parse the ASCII header of a PCD file.

    Args:
        filepath: Path to the PCD file.

    Returns:
        PCDHeader with parsed fields.

    Raises:
        ValueError: If the header is malformed or unsupported.
    """
    header = PCDHeader()

    with open(filepath, 'rb') as f:
        header_lines = []
        for _ in range(100):  # Safety limit
            line = f.readline().decode('ascii', errors='replace').strip()
            header_lines.append(line)
            if line.startswith('DATA'):
                break
        else:
            raise ValueError("DATA field not found in PCD header within 100 lines")

    # Parse each header line
    for line in header_lines:
        line = line.strip()
        if not line or line.startswith('#'):
            continue

        parts = line.split()
        key = parts[0].upper()

        if key == 'VERSION':
            header.version = parts[1]
        elif key == 'FIELDS':
            header.fields = parts[1:]
        elif key == 'SIZE':
            header.size = [int(v) for v in parts[1:]]
        elif key == 'TYPE':
            header.type = parts[1:]
        elif key == 'COUNT':
            header.count = [int(v) for v in parts[1:]]
        elif key == 'WIDTH':
            header.width = int(parts[1])
        elif key == 'HEIGHT':
            header.height = int(parts[1])
        elif key == 'VIEWPOINT':
            header.viewpoint = [float(v) for v in parts[1:]]
        elif key == 'POINTS':
            header.points = int(parts[1])
        elif key == 'DATA':
            header.data_mode = parts[1].lower()
        else:
            # Unknown key, silently ignore for forward compatibility
            pass

    # Fill default COUNT if not specified
    if not header.count:
        header.count = [1] * len(header.fields)

    # Total points
    if header.points == 0:
        header.points = header.width * header.height

    return header


def _find_data_offset(filepath: str) -> int:
    """Find the byte offset where data starts (after DATA line + newline)."""
    with open(filepath, 'rb') as f:
        content = f.read()
    # Find the DATA line and offset after its trailing newline
    # DATA line could end with \n or \r\n
    idx = content.find(b'DATA ')
    if idx < 0:
        raise ValueError("DATA keyword not found in file")
    offset = content.index(b'\n', idx) + 1
    return offset


def _read_ascii_data(filepath: str, header: PCDHeader) -> np.ndarray:
    """Read ASCII-encoded PCD data."""
    offset = _find_data_offset(filepath)
    data = np.loadtxt(filepath, skiprows=0, comments=None)

    # loadtxt reads from beginning; need to skip header
    with open(filepath, 'r') as f:
        for _ in range(header.points + len([l for l in open(filepath) if not l.startswith('#')])):
            pass

    # Simpler approach: count header lines
    n_header_lines = 0
    with open(filepath, 'r') as f:
        for line in f:
            n_header_lines += 1
            if line.strip().startswith('DATA'):
                break

    data = np.loadtxt(filepath, skiprows=n_header_lines)
    if data.ndim == 1:
        data = data.reshape(1, -1)
    return data


def _read_ascii_data_simple(filepath: str, header: PCDHeader) -> np.ndarray:
    """Read ASCII PCD data by skipping header lines."""
    n_skip = 0
    with open(filepath, 'r') as f:
        for line in f:
            n_skip += 1
            if line.strip().upper().startswith('DATA'):
                break

    data = np.loadtxt(filepath, skiprows=n_skip)
    if data.ndim == 1:
        data = data.reshape(1, -1)
    return data


def _read_binary_data(filepath: str, header: PCDHeader) -> np.ndarray:
    """Read raw binary PCD data.

    Data is stored as a flat memory dump of the point array.
    Each point occupies sum(SIZE[i] * COUNT[i]) bytes.
    """
    offset = _find_data_offset(filepath)

    # Calculate point size and build struct format string
    point_size = 0
    fmt_parts = []
    for field_name, sz, typ, cnt in zip(header.fields, header.size, header.type, header.count):
        fmt_char, _ = _TYPE_MAP[typ]
        point_size += sz * cnt
        if cnt > 1:
            fmt_parts.append(f"{cnt}{fmt_char}")
        else:
            fmt_parts.append(fmt_char)

    fmt_string = '<' + ''.join(fmt_parts)  # little-endian

    with open(filepath, 'rb') as f:
        f.seek(offset)
        raw = f.read()

    n_points = header.points
    expected_size = n_points * point_size

    if len(raw) < expected_size:
        # Some files have extra trailing data; try to use what we have
        n_points = len(raw) // point_size

    data = np.zeros((n_points, len(header.fields)), dtype=np.float32)

    for i in range(n_points):
        start = i * point_size
        values = struct.unpack(fmt_string, raw[start:start + point_size])
        data[i] = values

    return data


def _read_binary_compressed_data(filepath: str, header: PCDHeader) -> np.ndarray:
    """Read LZF-compressed binary PCD data.

    The compressed data layout:
    - 4 bytes: compressed_size (uint32)
    - 4 bytes: uncompressed_size (uint32)
    - compressed_size bytes: LZF-compressed planar data

    The uncompressed data is transposed: xxx...yyy...zzz...
    (structure-of-arrays instead of array-of-structures).
    """
    import lzf

    offset = _find_data_offset(filepath)

    with open(filepath, 'rb') as f:
        f.seek(offset)
        compressed_size = struct.unpack('<I', f.read(4))[0]
        uncompressed_size = struct.unpack('<I', f.read(4))[0]
        compressed = f.read(compressed_size)

    # Decompress
    decompressed = lzf.decompress(compressed, uncompressed_size)

    n_fields = len(header.fields)
    n_points = header.points

    # De-planarize: data is stored as [field0_all_points, field1_all_points, ...]
    data = np.zeros((n_points, n_fields), dtype=np.float32)
    offset_bytes = 0

    for fi in range(n_fields):
        field_size = header.size[fi] * header.count[fi]
        n_values = n_points * header.count[fi]

        if field_size == 4:
            field_data = np.frombuffer(
                decompressed, dtype=np.float32,
                count=n_values, offset=offset_bytes
            ).copy()
            offset_bytes += n_values * 4
        elif field_size == 8:
            field_data = np.frombuffer(
                decompressed, dtype=np.float64,
                count=n_values, offset=offset_bytes
            ).copy().astype(np.float32)
            offset_bytes += n_values * 8
        elif field_size == 1:
            field_data = np.frombuffer(
                decompressed, dtype=np.uint8,
                count=n_values, offset=offset_bytes
            ).copy().astype(np.float32)
            offset_bytes += n_values * 1
        elif field_size == 2:
            field_data = np.frombuffer(
                decompressed, dtype=np.uint16,
                count=n_values, offset=offset_bytes
            ).copy().astype(np.float32)
            offset_bytes += n_values * 2
        else:
            raise ValueError(f"Unsupported field size: {field_size}")

        if header.count[fi] == 1:
            data[:, fi] = field_data
        else:
            data[:, fi] = field_data.reshape(n_points, header.count[fi])[:, 0]

    return data


def _check_has_field(header: PCDHeader, field_names: List[str]) -> bool:
    """Check if all specified fields exist in the header."""
    return all(f in header.fields for f in field_names)


def read_pcd(filepath: str) -> PCDData:
    """Read a PCD file and return point cloud data.

    Automatically detects and handles ASCII, binary, and binary_compressed
    formats. Extracts XYZ coordinates and RGB color (if available).

    Args:
        filepath: Path to the PCD file.

    Returns:
        PCDData with points (N,3) and optional colors (N,3).

    Raises:
        FileNotFoundError: If the file does not exist.
        ValueError: If the file format is invalid or unsupported.
    """
    filepath = str(Path(filepath).expanduser().resolve())
    if not Path(filepath).exists():
        raise FileNotFoundError(f"PCD file not found: {filepath}")

    header = parse_header(filepath)

    # ── Point-count guard ──────────────────────────────────────────
    if header.points > 10_000_000:
        raise ValueError(
            f"点云点数 ({header.points:,}) 超过上限 (10,000,000)。\n"
            "请对点云进行降采样后再打开。"
        )

    # Read data based on storage mode
    if header.data_mode == 'ascii':
        raw_data = _read_ascii_data_simple(filepath, header)
    elif header.data_mode == 'binary':
        raw_data = _read_binary_data(filepath, header)
    elif header.data_mode == 'binary_compressed':
        try:
            raw_data = _read_binary_compressed_data(filepath, header)
        except ImportError:
            raise ImportError(
                "Reading binary_compressed PCD files requires 'python-lzf' package. "
                "Install it with: pip install python-lzf"
            )
    else:
        raise ValueError(f"Unsupported PCD data mode: {header.data_mode}")

    # Extract XYZ coordinates
    has_x = 'x' in header.fields
    has_y = 'y' in header.fields
    has_z = 'z' in header.fields

    if not (has_x and has_y and has_z):
        raise ValueError(
            f"PCD file missing XYZ fields. Found fields: {header.fields}"
        )

    ix = header.fields.index('x')
    iy = header.fields.index('y')
    iz = header.fields.index('z')

    points = np.column_stack([
        raw_data[:, ix],
        raw_data[:, iy],
        raw_data[:, iz],
    ]).astype(np.float32)

    # Extract RGB color if available
    colors = None
    if 'rgb' in header.fields:
        irgb = header.fields.index('rgb')
        rgb_int = raw_data[:, irgb].astype(np.uint32)
        colors = np.column_stack([
            ((rgb_int >> 16) & 0xFF).astype(np.float32) / 255.0,
            ((rgb_int >> 8) & 0xFF).astype(np.float32) / 255.0,
            (rgb_int & 0xFF).astype(np.float32) / 255.0,
        ])
    elif 'r' in header.fields and 'g' in header.fields and 'b' in header.fields:
        ir = header.fields.index('r')
        ig = header.fields.index('g')
        ib = header.fields.index('b')
        colors = np.column_stack([
            raw_data[:, ir].astype(np.float32),
            raw_data[:, ig].astype(np.float32),
            raw_data[:, ib].astype(np.float32),
        ])
        # If values are in 0-255 range, normalize
        if colors.max() > 1.0:
            colors /= 255.0

    return PCDData(
        points=points,
        colors=colors,
        fields=header.fields.copy(),
        point_count=len(points),
    )


def generate_test_pcd(filepath: str, n_points: int = 1000) -> None:
    """Generate a test PCD file for development and testing.

    Creates a simple point cloud with scattered points and color gradient.

    Args:
        filepath: Output file path.
        n_points: Number of points to generate.
    """
    np.random.seed(42)
    points = np.random.randn(n_points, 3).astype(np.float32) * 5.0
    points[:, 2] = np.abs(points[:, 2]) * 2  # Make Z positive

    # Generate RGB colors based on Z height (blue → green → red)
    z_norm = (points[:, 2] - points[:, 2].min()) / (points[:, 2].max() - points[:, 2].min() + 1e-6)
    r = np.clip(z_norm * 2.0 - 1.0, 0, 1)
    g = np.clip(1.0 - np.abs(z_norm - 0.5) * 2.0, 0, 1)
    b = np.clip(1.0 - z_norm * 2.0, 0, 1)

    rgb_int = ((r * 255).astype(np.uint32) << 16) | \
              ((g * 255).astype(np.uint32) << 8) | \
              (b * 255).astype(np.uint32)

    header = f"""# .PCD v0.7 - Point Cloud Data file format
VERSION 0.7
FIELDS x y z rgb
SIZE 4 4 4 4
TYPE F F F F
COUNT 1 1 1 1
WIDTH {n_points}
HEIGHT 1
VIEWPOINT 0 0 0 1 0 0 0
POINTS {n_points}
DATA ascii
"""

    with open(filepath, 'w') as f:
        f.write(header)
        for i in range(n_points):
            f.write(f"{points[i, 0]:.6f} {points[i, 1]:.6f} {points[i, 2]:.6f} {rgb_int[i]}\n")
