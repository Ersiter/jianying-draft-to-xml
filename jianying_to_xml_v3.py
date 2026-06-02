22#!/usr/bin/env python3
"""
Jianying/CapCut Draft Subtitle Exporter & XML Converter v3.0
Unified script: subtitle export (SRT/ASS/STL/TXT) + FCP7 XML export + keyframes

Backend: plugin-core.exe (jianying_assistant)
  - Reads encrypted drafts transparently (AES-GCM decryption in Go)
  - Full track/segment/material/transition/text/keyframe data access

Modes:
  - CLI: python subtitle_export.py <draft> -f srt,ass,stl
  - TUI: python subtitle_export.py (no args -> interactive menu)
  - Module: from subtitle_export import SubtitleExporter

Features:
  - SRT/ASS/TXT: via plugin-core subtitle_exporter (proven working)
  - EBU STL: binary encoding with CP936 Chinese support
  - FCP7 XML: full video/audio/transitions/keyframe animation/markers
  - Encrypted draft support (plugin-core transparent decryption)
"""

import json
import os
import struct
import sys
import subprocess
import argparse
import re
import shutil
from pathlib import Path
from datetime import datetime
from xml.etree.ElementTree import Element, SubElement, tostring
from xml.dom import minidom
from urllib.parse import quote
from dataclasses import dataclass, field

VERSION = "3.0.0"

# ── Constants ──────────────────────────────────────────────────────────────────
MICROSECOND = 1_000_000
DEFAULT_FPS = 30.0
DEFAULT_WIDTH = 1920
DEFAULT_HEIGHT = 1080

FORMATS_ALL = ["srt", "ass", "stl", "txt"]

# Jianying transition name -> FCP7 effectid
TRANSITION_MAP = {
    "淡入淡出": "Cross Dissolve", "叠化": "Cross Dissolve",
    "交叉溶解": "Cross Dissolve", "cross dissolve": "Cross Dissolve",
    "dissolve": "Cross Dissolve", "淡入": "Dip to Black",
    "fade in": "Dip to Black", "淡出": "Dip to Black",
    "fade out": "Dip to Black", "黑场": "Dip to Black",
    "dip to black": "Dip to Black", "白场": "Dip to White",
    "dip to white": "Dip to White",
}

# Jianying keyframe animation name -> (FCP7 parameter name, value_type)
# value_type: "xy" = center x,y | "pct" = percentage | "deg" = degrees
KEYFRAME_PARAM_MAP = {
    "LEFT_TO_RIGHT": ("Center", "xy"), "RIGHT_TO_LEFT": ("Center", "xy"),
    "UPPER_TO_BOTTOM": ("Center", "xy"), "BOTTOM_TO_UPPER": ("Center", "xy"),
    "UPPER_LEFT_TO_BOTTOM_RIGHT": ("Center", "xy"),
    "BOTTOM_LEFT_TO_UPPER_RIGHT": ("Center", "xy"),
    "UPPER_RIGHT_TO_BOTTOM_LEFT": ("Center", "xy"),
    "BOTTOM_RIGHT_TO_UPPER_LEFT": ("Center", "xy"),
    "ZOOM_IN": ("Scale", "pct"), "ZOOM_OUT": ("Scale", "pct"),
    "ZOOM_IN_SLIGHT": ("Scale", "pct"), "ZOOM_OUT_SLIGHT": ("Scale", "pct"),
    "ZOOM_IN_SEVERE": ("Scale", "pct"), "ZOOM_OUT_SEVERE": ("Scale", "pct"),
    "ZOOM_BREATH": ("Scale", "pct"),
    "ROTATE_CW": ("Rotation", "deg"), "ROTATE_CCW": ("Rotation", "deg"),
    "ROTATE_SWING": ("Rotation", "deg"),
    "FADE_IN": ("Opacity", "pct"), "FADE_OUT": ("Opacity", "pct"),
}


# ═══════════════════════════════════════════════════════════════════════════════
# 1. EBU STL Binary Generator
# ═══════════════════════════════════════════════════════════════════════════════

def _stl_timecode(ms: int, fps: float) -> bytes:
    """Convert milliseconds to STL binary timecode (4 bytes: HH MM SS FF)."""
    total_frames = round(ms / 1000.0 * fps)
    ff = total_frames % int(fps)
    remaining = total_frames // int(fps)
    ss = remaining % 60
    remaining //= 60
    mm = remaining % 60
    hh = remaining // 60
    return bytes([min(hh, 99), min(mm, 59), min(ss, 59), min(ff, int(fps) - 1)])


def _build_gsi_block(title: str, fps: float, subtitle_count: int,
                     encoding_byte: int = 0x30) -> bytes:
    """Build 128-byte GSI (General Subtitle Information) block."""
    blk = bytearray(128)

    # 0-2: Code page number (CPN) - "850" for multilingual
    blk[0:3] = b"850"

    # 3-11: Display standard code + Application
    blk[3:6] = b"STL"    # Display standard
    blk[6:8] = b"  "     # Application profile
    blk[8:12] = b"25.01" # Version (but offset 8-11)

    # 12-13: Encoding (0x30=Latin, 0x31=UTF-8)
    blk[12] = encoding_byte
    blk[13] = 0x20  # filler

    # 14-15: Format code
    blk[14] = 0x31  # "1" = Standard
    blk[15] = 0x30  # "0"

    # 16-17: Frame rate
    fps_str = f"{int(fps):02d}"
    blk[16] = ord(fps_str[0])
    blk[17] = ord(fps_str[1])

    # 18-22: Number of subtitle groups (5 digits)
    nsub = f"{subtitle_count:05d}"
    blk[18:23] = nsub.encode("ascii", errors="replace")

    # 23-24: Number of subtitles in this group (not critical)
    blk[23:25] = b"00"

    # 25-29: Total number of subtitles
    blk[25:30] = nsub.encode("ascii", errors="replace")

    # 30-31: Number of substitution character codes
    blk[30:32] = b"00"

    # 32-33: Substitution character codes
    blk[32:34] = b"  "

    # 34-41: Country code
    blk[34:37] = b"CHN"
    blk[37:42] = b"     "

    # 42-44: Character code
    if encoding_byte == 0x30:
        blk[42:46] = b"936 "  # CP936 (GBK/Chinese)
    else:
        blk[42:46] = b"UTF8"

    # 45-46: Language code
    blk[45:48] = b"zho"

    # 47-57: Original subtitle program name (Title)
    title_bytes = title.encode("utf-8", errors="replace")[:11]
    blk[48:48 + len(title_bytes)] = title_bytes

    # 59-68: Original subtitle program name continuation
    if len(title_bytes) > 11:
        blk[59:59 + min(len(title_bytes) - 11, 10)] = title_bytes[11:21]

    # 78-84: Creation date (DDMMYY)
    now = datetime.now()
    date_str = now.strftime("%d%m%y")
    blk[78:84] = date_str.encode("ascii")

    # 84-89: Revision date
    blk[84:90] = date_str.encode("ascii")

    # 90-95: Revision number
    blk[90:96] = b"000000"

    # 100-105: Total number of text and timing information blocks (TTI)
    tti_count = f"{subtitle_count:05d}"
    blk[100:105] = tti_count.encode("ascii")

    # 106: Timing mode (0 = not declared, 1 = frame count)
    blk[106] = 0x01

    # 107: Time code status (0 = not intended for use)
    blk[107] = 0x00

    # 110-113: Country code for time code
    blk[110:114] = b"CHN "

    # 114-117: Time code offset
    blk[114:118] = b"\x00\x00\x00\x00"

    return bytes(blk)


def _build_tti_block(index: int, start_ms: int, end_ms: int,
                     text: str, fps: float,
                     encoding_byte: int = 0x30) -> bytes:
    """Build 128-byte TTI (Text and Timing Information) block."""
    blk = bytearray(128)

    # 0-1: Subtitle group number
    blk[0] = 0x00
    blk[1] = 0x00

    # 2-3: Subtitle number (within group)
    struct.pack_into(">H", blk, 2, index & 0xFFFF)

    # 4: Extension block number
    blk[4] = 0xFF  # no extension

    # 5: Cumulative status (CS) - 0x00 = not cumulative
    blk[5] = 0x00

    # 6-7: Vertical position (row 0-22, 0=top, 22=bottom)
    blk[6] = 0x00  # top of screen (row 0)
    blk[7] = 0x00

    # 8-11: Justification code
    # 0x01=left, 0x02=center, 0x03=right
    blk[8] = 0x02  # center

    # 9-10: Comment flag
    blk[9] = 0x00

    # 10-13: Timecode In
    blk[10:14] = _stl_timecode(start_ms, fps)

    # 14-17: Timecode Out
    blk[14:18] = _stl_timecode(end_ms, fps)

    # 18: Timecode mode (0 = normal)
    blk[18] = 0x00

    # Encode text
    if encoding_byte == 0x30:
        # CP936 (GBK) for Chinese
        text_bytes = text.encode("gbk", errors="replace")
    else:
        text_bytes = text.encode("utf-8", errors="replace")

    # TTI block text area: bytes 19-127 (max 112 bytes), terminated with 0x8F
    max_text_len = 111  # 112 - 1 byte for terminator
    text_bytes = text_bytes[:max_text_len]
    blk[19:19 + len(text_bytes)] = text_bytes
    blk[19 + len(text_bytes)] = 0x8F  # end of text marker

    return bytes(blk)


def generate_stl(subtitles: list[dict], fps: float, title: str,
                 output_path: str, encoding: str = "cp936") -> None:
    """
    Generate EBU STL binary file from subtitle list.

    Args:
        subtitles: [{"index": int, "start_ms": int, "end_ms": int, "text": str}, ...]
        fps: Frame rate
        title: Program title
        output_path: Output .stl file path
        encoding: "cp936" for Chinese, "latin1" for European
    """
    enc_byte = 0x30  # Latin encoding byte; text still uses cp936

    gsi = _build_gsi_block(title, fps, len(subtitles), enc_byte)

    ttis = []
    for sub in subtitles:
        tti = _build_tti_block(
            sub["index"], sub["start_ms"], sub["end_ms"],
            sub["text"], fps, enc_byte,
        )
        ttis.append(tti)

    with open(output_path, "wb") as f:
        f.write(gsi)
        for tti in ttis:
            f.write(tti)


# ═══════════════════════════════════════════════════════════════════════════════
# 2. SRT Parser
# ═══════════════════════════════════════════════════════════════════════════════

def parse_srt(filepath: str) -> list[dict]:
    """Parse SRT file into subtitle list."""
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()

    subs = []
    blocks = re.split(r"\n\s*\n", content.strip())
    for block in blocks:
        lines = block.strip().split("\n")
        if len(lines) < 3:
            continue
        # Line 0: index
        try:
            idx = int(lines[0].strip())
        except ValueError:
            continue
        # Line 1: timecodes
        time_match = re.match(
            r"(\d{2}):(\d{2}):(\d{2}),(\d{3})\s*-->\s*(\d{2}):(\d{2}):(\d{2}),(\d{3})",
            lines[1].strip()
        )
        if not time_match:
            continue
        g = [int(x) for x in time_match.groups()]
        start_ms = g[0] * 3600000 + g[1] * 60000 + g[2] * 1000 + g[3]
        end_ms = g[4] * 3600000 + g[5] * 60000 + g[6] * 1000 + g[7]
        text = "\n".join(lines[2:]).strip()
        subs.append({"index": idx, "start_ms": start_ms, "end_ms": end_ms, "text": text})

    return subs


# ═══════════════════════════════════════════════════════════════════════════════
# 3. SubtitleExporter Class (importable API)
# ═══════════════════════════════════════════════════════════════════════════════

class SubtitleExporter:
    """
    Export subtitles from Jianying/CapCut drafts.

    Usage:
        exporter = SubtitleExporter("C:/path/to/plugin-core.exe")
        if exporter.has_subtitles("C:/draft/dir"):
            result = exporter.export("C:/draft/dir", "C:/output", ["srt", "ass", "stl"])
    """

    def __init__(self, plugin_core: str = ""):
        self.plugin_core = plugin_core or self._find_plugin_core()
        if not self.plugin_core or not Path(self.plugin_core).exists():
            raise FileNotFoundError(
                "plugin-core.exe not found. "
                "Use --plugin-core or set PLUGIN_CORE environment variable."
            )

    @staticmethod
    def _find_plugin_core() -> str:
        candidates = [
            Path(__file__).parent / "jianying_assistant" / "plugins" / "plugin-core.exe",
            Path(__file__).parent / "jianying_assistant" / "plugins" / "plugin-core",
        ]
        env_path = os.environ.get("PLUGIN_CORE", "")
        if env_path:
            candidates.insert(0, Path(env_path))
        for c in candidates:
            if c.exists():
                return str(c)
        return ""

    def has_subtitles(self, draft_dir: str) -> bool:
        """Check if draft has any text/subtitle segments."""
        try:
            data = _run_core(self.plugin_core, ["text", "list"], draft_dir)
            return len(data.get("data", [])) > 0
        except Exception:
            return False

    def get_draft_info(self, draft_dir: str) -> dict:
        """Get draft metadata (fps, resolution, duration, etc.)."""
        data = _run_core(self.plugin_core, ["draft", "info"], draft_dir)
        return data.get("data", {})

    def export(self, draft_dir: str, output_dir: str,
               formats: list[str] | None = None) -> dict:
        """
        Export subtitles in specified formats.

        Args:
            draft_dir: Draft directory path
            output_dir: Output directory
            formats: List of format strings (default: ["srt", "ass", "txt"])

        Returns:
            {"srt": "path/to/file.srt", "ass": "path/to/file.ass", ...}
        """
        if formats is None:
            formats = ["srt", "ass", "txt"]

        os.makedirs(output_dir, exist_ok=True)
        result = {}

        # Separate: formats supported by plugin-core vs STL (custom)
        core_fmts = [f for f in formats if f in ("srt", "ass", "txt")]
        want_stl = "stl" in formats

        # 1. Export SRT/ASS/TXT via plugin-core
        if core_fmts:
            core_result = _export_subtitle_via_core(
                self.plugin_core, draft_dir, output_dir, ",".join(core_fmts)
            )
            for r in core_result.get("data", core_result).get("results", []):
                for fp in r.get("files", []):
                    ext = Path(fp).suffix.lstrip(".")
                    result[ext] = fp

        # 2. Generate STL from SRT data
        if want_stl:
            srt_path = result.get("srt")
            if not srt_path:
                # Need SRT first to generate STL
                srt_result = _export_subtitle_via_core(
                    self.plugin_core, draft_dir, output_dir, "srt"
                )
                for r in srt_result.get("data", srt_result).get("results", []):
                    for fp in r.get("files", []):
                        if fp.endswith(".srt"):
                            srt_path = fp
                            result["srt"] = fp

            if srt_path and Path(srt_path).exists():
                subs = parse_srt(srt_path)
                info = self.get_draft_info(draft_dir)
                fps = float(info.get("fps", 25.0))
                name = Path(draft_dir).name
                stl_path = os.path.join(output_dir, f"{name}.stl")
                generate_stl(subs, fps, name, stl_path)
                result["stl"] = stl_path

        return result


# ═══════════════════════════════════════════════════════════════════════════════
# 4. Plugin-core CLI Helpers
# ═══════════════════════════════════════════════════════════════════════════════

def _run_core(exe: str, command: list[str], draft_dir: str) -> dict:
    """Run a plugin-core CLI command and return parsed JSON."""
    args = [exe] + command + ["--input", draft_dir]
    result = subprocess.run(args, capture_output=True, timeout=30)
    stdout = result.stdout.decode("utf-8", errors="replace").strip()
    stderr = result.stderr.decode("utf-8", errors="replace").strip()
    if not stdout:
        raise RuntimeError(f"plugin-core no output: {stderr[:200]}")
    data = json.loads(stdout)
    if not data.get("ok", False):
        err = data.get("error", {})
        msg = err.get("message", str(err)) if isinstance(err, dict) else str(err)
        raise RuntimeError(f"plugin-core error: {msg}")
    return data


def _run_core_plugin(exe: str, plugin_id: str, params: dict) -> dict:
    """Run plugin-core in plugin mode with JSON input."""
    import tempfile
    input_data = {"plugin_id": plugin_id, "params": params}
    fd, path = tempfile.mkstemp(suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(input_data, f, ensure_ascii=False)
        args = [exe, "plugin", "--input", path]
        result = subprocess.run(args, capture_output=True, timeout=60)
        stdout = result.stdout.decode("utf-8", errors="replace").strip()
        if not stdout:
            stderr = result.stderr.decode("utf-8", errors="replace").strip()
            raise RuntimeError(f"plugin-core no output: {stderr[:200]}")
        data = json.loads(stdout)
        if not data.get("ok", False):
            err = data.get("error", {})
            msg = err.get("message", str(err)) if isinstance(err, dict) else str(err)
            raise RuntimeError(f"plugin error: {msg}")
        return data
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def _export_subtitle_via_core(exe: str, draft_dir: str,
                               output_dir: str, fmt: str) -> dict:
    """Export subtitles via plugin-core subtitle_exporter."""
    name = Path(draft_dir).name
    return _run_core_plugin(exe, "subtitle_exporter", {
        "action": "export_subtitle",
        "output_dir": output_dir,
        "format": fmt,
        "drafts": [{"name": name, "draft_dir": draft_dir}],
    })


def _load_draft_via_core(exe: str, draft_dir: str) -> dict:
    """Load full draft data via plugin-core CLI commands."""
    timeline = {}

    # Draft info
    info = _run_core(exe, ["draft", "info"], draft_dir)
    d = info["data"]
    timeline["name"] = Path(draft_dir).name
    timeline["width"] = d.get("width", DEFAULT_WIDTH)
    timeline["height"] = d.get("height", DEFAULT_HEIGHT)
    timeline["fps"] = float(d.get("fps", DEFAULT_FPS))
    timeline["duration_us"] = d.get("duration_us", 0)
    timeline["is_encrypted"] = d.get("is_encrypted", False)

    # Tracks
    track_data = _run_core(exe, ["track", "list"], draft_dir)
    timeline["tracks"] = track_data.get("data", [])

    # Segments per track + materials
    timeline["segments"] = {}
    timeline["materials"] = {}
    for track in timeline["tracks"]:
        idx = track["index"]
        seg_data = _run_core(exe, ["segment", "list", "--track", str(idx)], draft_dir)
        segs = []
        for i, s in enumerate(seg_data.get("data", [])):
            mid = s.get("material_id", "")
            seg = {
                "segment_id": f"seg-{idx}-{i}",
                "material_id": mid,
                "track_index": idx,
                "track_type": track.get("type", "video"),
                "target_start": s.get("start_us", 0),
                "target_duration": s.get("duration_us", 0),
                "source_start": 0,
                "source_duration": s.get("duration_us", 0),
                "speed": s.get("speed", 1.0),
                "extra": s,
            }
            segs.append(seg)
            if mid and mid not in timeline["materials"]:
                try:
                    mat_data = _run_core(exe, ["material", "get", "--id", mid], draft_dir)
                    md = mat_data.get("data", {})
                    raw_type = md.get("type", "")
                    mtype = raw_type.rstrip("s") if raw_type.endswith("s") else raw_type
                    timeline["materials"][mid] = {
                        "id": mid, "type": mtype,
                        "path": md.get("path", ""),
                        "name": md.get("name", ""),
                        "duration": md.get("duration", 0),
                        "width": md.get("width", 0),
                        "height": md.get("height", 0),
                        "fps": md.get("fps", 0),
                        "sample_rate": md.get("sample_rate", 0),
                        "channels": md.get("channels", 0),
                    }
                except Exception:
                    timeline["materials"][mid] = {
                        "id": mid, "type": track.get("type", "video"),
                        "path": "", "name": "", "duration": 0,
                        "width": 0, "height": 0, "fps": 0,
                        "sample_rate": 0, "channels": 0,
                    }
        timeline["segments"][idx] = segs

    # Transitions
    trans_data = _run_core(exe, ["transition", "list"], draft_dir)
    timeline["transitions"] = trans_data.get("data", [])

    # Text segments
    text_data = _run_core(exe, ["text", "list"], draft_dir)
    timeline["texts"] = text_data.get("data", [])

    # Keyframes (best-effort; may return empty for drafts without keyframes)
    try:
        kf_data = _run_core(exe, ["keyframe", "list"], draft_dir)
        timeline["keyframes"] = kf_data.get("data", [])
    except Exception:
        timeline["keyframes"] = []

    return timeline


# ═══════════════════════════════════════════════════════════════════════════════
# 5. Utility Functions
# ═══════════════════════════════════════════════════════════════════════════════

def is_ntsc_fps(fps: float) -> bool:
    for rate in (23.976, 29.97, 29.97002997, 47.952, 59.94, 59.94005994):
        if abs(fps - rate) < 0.01:
            return True
    return False


def get_timebase(fps: float) -> int:
    if is_ntsc_fps(fps):
        return round(fps * 1001 / 1000)
    return round(fps)


def us_to_frames(us: int, fps: float) -> int:
    if fps <= 0:
        return 0
    return round(us / MICROSECOND * fps)


def windows_path_to_url(filepath: str) -> str:
    p = Path(filepath)
    if not p.is_absolute():
        p = p.resolve()
    try:
        return p.as_uri()
    except Exception:
        url_path = str(p).replace("\\", "/")
        return f"file:///{quote(url_path, safe='/:')}"


def sanitize_filename(name: str) -> str:
    return "".join(c for c in name if c not in '<>:"/\\|?*').strip()


def short_id(id_str: str) -> str:
    return id_str[:8] if len(id_str) >= 8 else id_str


def us_to_srt_time(us: int) -> str:
    total_ms = us // 1000
    ms = total_ms % 1000
    total_s = total_ms // 1000
    s = total_s % 60
    total_m = total_s // 60
    m = total_m % 60
    h = total_m // 60
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def resolve_transition_effect(name: str) -> tuple[str, str]:
    name_lower = name.lower().strip()
    for key, effectid in TRANSITION_MAP.items():
        if key.lower() in name_lower or name_lower in key.lower():
            return effectid, "center"
    return "Cross Dissolve", "center"


# ═══════════════════════════════════════════════════════════════════════════════
# 6. FCP7 XML Generator (with keyframe + transition support)
# ═══════════════════════════════════════════════════════════════════════════════

def _add_rate(parent, fps):
    rate = SubElement(parent, "rate")
    SubElement(rate, "ntsc").text = "TRUE" if is_ntsc_fps(fps) else "FALSE"
    SubElement(rate, "timebase").text = str(get_timebase(fps))


def _build_file_elem(parent, material, fps, file_id, full=True):
    fe = SubElement(parent, "file", id=file_id)
    if not full:
        return fe
    SubElement(fe, "name").text = material.get("name") or Path(material.get("path", "")).name or "unknown"
    if material.get("path"):
        SubElement(fe, "pathurl").text = windows_path_to_url(material["path"])
    _add_rate(fe, fps)
    SubElement(fe, "duration").text = str(max(us_to_frames(material.get("duration", 0), fps), 1))
    media_elem = SubElement(fe, "media")
    if material.get("type") == "video" or material.get("width", 0) > 0:
        v = SubElement(media_elem, "video")
        sc = SubElement(v, "samplecharacteristics")
        SubElement(sc, "width").text = str(material.get("width") or DEFAULT_WIDTH)
        SubElement(sc, "height").text = str(material.get("height") or DEFAULT_HEIGHT)
    a = SubElement(media_elem, "audio")
    SubElement(a, "channelcount").text = str(material.get("channels") or 2)
    sc = SubElement(a, "samplecharacteristics")
    SubElement(sc, "samplerate").text = str(material.get("sample_rate") or 48000)
    SubElement(sc, "size").text = "16-bit"
    return fe


def _build_keyframe_filter(parent, fps, keyframes):
    """Build <filter> with keyframe animation parameters.

    keyframes: list of dicts with keys:
        animation_name, start_us, duration_us, keyframe_points
    Each keyframe_points entry: {"frame": int, "value": ..., "curve": "linear"}
    """
    if not keyframes:
        return

    filter_elem = SubElement(parent, "filter")
    effect = SubElement(filter_elem, "effect")
    SubElement(effect, "name").text = "Basic Motion"
    SubElement(effect, "effectid").text = "basic"
    SubElement(effect, "effectcategory").text = "motion"
    SubElement(effect, "effecttype").text = "motion"
    SubElement(effect, "mediatype").text = "video"

    for kf in keyframes:
        anim_name = kf.get("animation_name", "")
        param_info = KEYFRAME_PARAM_MAP.get(anim_name)
        if not param_info:
            continue
        param_name, value_type = param_info

        param = SubElement(effect, "parameter")
        SubElement(param, "name").text = param_name

        for point in kf.get("keyframe_points", []):
            kfe = SubElement(param, "keyframe")
            SubElement(kfe, "when").text = str(point.get("frame", 0))
            val = point.get("value", "")
            if isinstance(val, (list, tuple)):
                SubElement(kfe, "value").text = ", ".join(str(v) for v in val)
            else:
                SubElement(kfe, "value").text = str(val)
            curve = SubElement(kfe, "curve")
            SubElement(curve, "type").text = point.get("curve", "linear")


def generate_xml(timeline: dict, output_path: str, keyframe_data: list = None) -> None:
    """Generate FCP7 XML from timeline data dict (from _load_draft_via_core)."""
    fps = timeline["fps"]
    xmeml = Element("xmeml", version="5")
    seq = SubElement(xmeml, "sequence")
    SubElement(seq, "name").text = timeline.get("name", "Jianying Timeline")
    total_frames = us_to_frames(timeline["duration_us"], fps)
    SubElement(seq, "duration").text = str(max(total_frames, 1))
    _add_rate(seq, fps)

    # Text/subtitle markers
    for txt in timeline.get("texts", []):
        hint = txt.get("content_hint", "")
        if not hint:
            continue
        marker = SubElement(seq, "marker")
        SubElement(marker, "name").text = hint
        SubElement(marker, "in").text = str(us_to_frames(txt.get("start_us", 0), fps))
        out_us = txt.get("start_us", 0) + txt.get("duration_us", 0)
        SubElement(marker, "out").text = str(us_to_frames(out_us, fps))
        SubElement(marker, "comment").text = f"[subtitle] {hint}"

    # Media
    media = SubElement(seq, "media")
    video = SubElement(media, "video")
    audio = SubElement(media, "audio")

    # Video format (DaVinci requires this)
    vf = SubElement(video, "format")
    vsc = SubElement(vf, "samplecharacteristics")
    SubElement(vsc, "width").text = str(timeline["width"])
    SubElement(vsc, "height").text = str(timeline["height"])
    SubElement(vsc, "anamorphic").text = "FALSE"
    SubElement(vsc, "pixelaspectratio").text = "square"
    SubElement(vsc, "fielddominance").text = "none"

    # Audio format
    af = SubElement(audio, "format")
    asc = SubElement(af, "samplecharacteristics")
    SubElement(asc, "samplerate").text = "48000"
    SubElement(asc, "size").text = "16-bit"
    SubElement(asc, "channelcount").text = "2"

    # Separate tracks
    video_tracks = [t for t in timeline["tracks"] if t.get("type") == "video"]
    audio_tracks = [t for t in timeline["tracks"] if t.get("type") == "audio"]

    # ID management
    clip_counter = [0]
    file_counter = [0]
    master_counter = [0]
    file_full_written = set()
    file_id_map = {}
    master_id_map = {}

    for mid in timeline["materials"]:
        file_counter[0] += 1
        file_id_map[mid] = f"file-{file_counter[0]}"
        master_counter[0] += 1
        master_id_map[mid] = f"masterclip-{master_counter[0]}"

    seg_clip_id_map = {}
    for track_idx, segs in timeline["segments"].items():
        for seg in segs:
            clip_counter[0] += 1
            seg_clip_id_map[seg["segment_id"]] = f"clipitem-{clip_counter[0]}"

    # Link groups
    video_mids = set()
    audio_mids = set()
    for t in video_tracks:
        for seg in timeline["segments"].get(t["index"], []):
            video_mids.add(seg["material_id"])
    for t in audio_tracks:
        for seg in timeline["segments"].get(t["index"], []):
            audio_mids.add(seg["material_id"])
    linked_mids = video_mids & audio_mids

    link_groups = {}
    for mid in linked_mids:
        group = []
        for vi, vt in enumerate(video_tracks):
            for seg in timeline["segments"].get(vt["index"], []):
                if seg["material_id"] == mid:
                    group.append({"clipref": seg_clip_id_map[seg["segment_id"]],
                                  "mediatype": "video", "trackindex": vi + 1, "clipindex": 1})
        for ai, at in enumerate(audio_tracks):
            for seg in timeline["segments"].get(at["index"], []):
                if seg["material_id"] == mid:
                    group.append({"clipref": seg_clip_id_map[seg["segment_id"]],
                                  "mediatype": "audio", "trackindex": ai + 1, "clipindex": 1})
        link_groups[mid] = group

    # Keyframe data per segment
    kf_by_seg = {}
    if keyframe_data:
        for kf in keyframe_data:
            seg_id = kf.get("segment_id", "")
            if seg_id:
                kf_by_seg.setdefault(seg_id, []).append(kf)

    # ── Write video tracks ──
    for vi, vtrack in enumerate(video_tracks):
        track_idx = vtrack["index"]
        segs = timeline["segments"].get(track_idx, [])
        xm_track = SubElement(video, "track")

        for seg in segs:
            mid = seg["material_id"]
            mat = timeline["materials"].get(mid)
            if not mat:
                continue

            clip_id = seg_clip_id_map[seg["segment_id"]]
            fid = file_id_map.get(mid)
            mcl = master_id_map.get(mid)
            is_linked = mid in linked_mids
            full = fid not in file_full_written
            if full:
                file_full_written.add(fid)
            links = link_groups.get(mid, [])

            clip = Element("clipitem", id=clip_id)
            SubElement(clip, "name").text = mat.get("name") or Path(mat.get("path", "")).name or seg["segment_id"]
            SubElement(clip, "masterclipid").text = mcl
            SubElement(clip, "duration").text = str(max(us_to_frames(mat.get("duration", 0), fps), 1))
            _add_rate(clip, fps)

            start_f = us_to_frames(seg["target_start"], fps)
            clip_f = us_to_frames(seg["target_duration"], fps)
            src_in = us_to_frames(seg["source_start"], fps)
            src_out = us_to_frames(seg["source_start"] + seg["source_duration"], fps)

            SubElement(clip, "start").text = str(start_f)
            SubElement(clip, "end").text = str(start_f + clip_f)
            SubElement(clip, "in").text = str(src_in)
            SubElement(clip, "out").text = str(src_out)

            _build_file_elem(clip, mat, fps, fid, full=full)

            st = SubElement(clip, "sourcetrack")
            SubElement(st, "mediatype").text = "video"
            SubElement(st, "trackindex").text = "1"

            for lk in links:
                link = SubElement(clip, "link")
                SubElement(link, "linkclipref").text = lk["clipref"]
                SubElement(link, "mediatype").text = lk["mediatype"]
                SubElement(link, "trackindex").text = str(lk["trackindex"])
                SubElement(link, "clipindex").text = str(lk["clipindex"])

            # Keyframe animation filter
            seg_keyframes = kf_by_seg.get(seg["segment_id"], [])
            if seg_keyframes:
                _build_keyframe_filter(clip, fps, seg_keyframes)

            xm_track.append(clip)

    # ── Write audio tracks ──
    if not audio_tracks:
        SubElement(audio, "track")
    else:
        for ai, atrack in enumerate(audio_tracks):
            track_idx = atrack["index"]
            segs = timeline["segments"].get(track_idx, [])
            xm_track = SubElement(audio, "track")

            for seg in segs:
                mid = seg["material_id"]
                mat = timeline["materials"].get(mid)
                if not mat:
                    continue

                clip_id = seg_clip_id_map[seg["segment_id"]]
                fid = file_id_map.get(mid)
                mcl = master_id_map.get(mid)
                is_linked = mid in linked_mids
                full = not is_linked and fid not in file_full_written
                if full:
                    file_full_written.add(fid)
                links = link_groups.get(mid, [])

                clip = Element("clipitem", id=clip_id)
                SubElement(clip, "name").text = mat.get("name") or Path(mat.get("path", "")).name or seg["segment_id"]
                SubElement(clip, "masterclipid").text = mcl
                SubElement(clip, "duration").text = str(max(us_to_frames(mat.get("duration", 0), fps), 1))
                _add_rate(clip, fps)

                start_f = us_to_frames(seg["target_start"], fps)
                clip_f = us_to_frames(seg["target_duration"], fps)
                SubElement(clip, "start").text = str(start_f)
                SubElement(clip, "end").text = str(start_f + clip_f)
                SubElement(clip, "in").text = str(us_to_frames(seg["source_start"], fps))
                SubElement(clip, "out").text = str(us_to_frames(seg["source_start"] + seg["source_duration"], fps))

                _build_file_elem(clip, mat, fps, fid, full=full)

                st = SubElement(clip, "sourcetrack")
                SubElement(st, "mediatype").text = "audio"
                SubElement(st, "trackindex").text = "1"

                for lk in links:
                    link = SubElement(clip, "link")
                    SubElement(link, "linkclipref").text = lk["clipref"]
                    SubElement(link, "mediatype").text = lk["mediatype"]
                    SubElement(link, "trackindex").text = str(lk["trackindex"])
                    SubElement(link, "clipindex").text = str(lk["clipindex"])

                xm_track.append(clip)

    # Pretty print
    raw_xml = tostring(xmeml, encoding="unicode")
    dom = minidom.parseString(raw_xml)
    pretty = dom.toprettyxml(indent="  ", encoding="utf-8").decode("utf-8")
    body = "\n".join(l for l in pretty.splitlines() if not l.lstrip().startswith("<?xml")).lstrip()

    with open(output_path, "w", encoding="utf-8") as f:
        f.write('<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE xmeml>\n')
        f.write(body)
        f.write("\n")


def generate_json(timeline: dict, output_path: str) -> None:
    """Export comprehensive timeline JSON."""
    fps = timeline["fps"]
    tracks_out = []
    for track in timeline["tracks"]:
        idx = track["index"]
        segs = timeline["segments"].get(idx, [])
        seg_dicts = []
        for seg in segs:
            mat = timeline["materials"].get(seg["material_id"], {})
            seg_dicts.append({
                "segment_id": seg["segment_id"],
                "material_id": seg["material_id"],
                "material_name": mat.get("name", ""),
                "material_path": mat.get("path", ""),
                "material_type": mat.get("type", ""),
                "start_sec": round(seg["target_start"] / MICROSECOND, 3),
                "end_sec": round((seg["target_start"] + seg["target_duration"]) / MICROSECOND, 3),
                "duration_sec": round(seg["target_duration"] / MICROSECOND, 3),
                "speed": seg["speed"],
            })
        tracks_out.append({
            "index": idx, "type": track.get("type", "video"),
            "segment_count": len(segs), "segments": seg_dicts,
        })

    texts_out = []
    for txt in timeline.get("texts", []):
        texts_out.append({
            "material_id": txt.get("material_id", ""),
            "content": txt.get("content_hint", ""),
            "track_index": txt.get("track_index", -1),
            "start_sec": round(txt.get("start_us", 0) / MICROSECOND, 3),
            "end_sec": round((txt.get("start_us", 0) + txt.get("duration_us", 0)) / MICROSECOND, 3),
        })

    output = {
        "project_name": timeline.get("name", ""),
        "canvas": {"width": timeline["width"], "height": timeline["height"], "fps": fps},
        "total_duration_sec": round(timeline["duration_us"] / MICROSECOND, 3),
        "total_frames": us_to_frames(timeline["duration_us"], fps),
        "is_encrypted": timeline.get("is_encrypted", False),
        "track_count": len(timeline["tracks"]),
        "material_count": len(timeline["materials"]),
        "transition_count": len(timeline.get("transitions", [])),
        "text_count": len(timeline.get("texts", [])),
        "keyframe_group_count": len(timeline.get("keyframes", [])),
        "tracks": tracks_out,
        "texts": texts_out,
        "converter_version": VERSION,
    }
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)


# ═══════════════════════════════════════════════════════════════════════════════
# 7. CLI Mode (called by converter_v3.bat / converter_v3.sh TUI)
# ═══════════════════════════════════════════════════════════════════════════════

def cli_mode():
    parser = argparse.ArgumentParser(
        description=f"Jianying Subtitle Exporter & XML Converter v{VERSION}",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python subtitle_export.py "path/to/draft" -f srt,ass,stl
  python subtitle_export.py "path/to/draft" -f all -o ./output
  python subtitle_export.py "path/to/draft" --xml --json
  python subtitle_export.py "path/to/draft" -f srt,stl --xml --json -o ./output
        """,
    )
    parser.add_argument("draft_dir", help="Draft directory path")
    parser.add_argument("-o", "--output", default=None, help="Output directory")
    parser.add_argument("-f", "--format", default=None,
                        help="Subtitle formats: srt,ass,stl,txt or 'all' (comma-separated)")
    parser.add_argument("--xml", action="store_true", help="Also generate FCP7 XML")
    parser.add_argument("--json", action="store_true", help="Also generate timeline JSON")
    parser.add_argument("--json-only", action="store_true", help="Only output JSON (no XML)")
    parser.add_argument("--all", action="store_true", help="Export everything (all formats + XML + JSON)")
    parser.add_argument("--plugin-core", default=None, help="Path to plugin-core.exe")
    parser.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    args = parser.parse_args()

    exe = args.plugin_core or SubtitleExporter._find_plugin_core()
    if not exe or not Path(exe).exists():
        print("[ERROR] plugin-core.exe not found. Use --plugin-core to specify.", file=sys.stderr)
        sys.exit(1)

    draft_dir = args.draft_dir
    if not Path(draft_dir).is_dir():
        print(f"[ERROR] Not a directory: {draft_dir}", file=sys.stderr)
        sys.exit(1)

    output_dir = args.output or os.path.join(os.getcwd(), "output")
    os.makedirs(output_dir, exist_ok=True)

    # Determine what to export
    if args.all:
        formats = list(FORMATS_ALL)
        want_xml = True
        want_json = True
    else:
        fmt_str = args.format
        if fmt_str:
            formats = list(FORMATS_ALL) if fmt_str == "all" else [
                f.strip().lower() for f in fmt_str.split(",") if f.strip() in FORMATS_ALL]
        else:
            formats = []

        want_json = args.json
        if args.json_only:
            want_xml = False
            want_json = True
        elif args.xml:
            want_xml = True
        elif not formats:
            # No -f, no --xml, no --json-only: default to XML
            want_xml = True
        else:
            want_xml = False

    print(f"[DRAFT]  {draft_dir}")
    print(f"[OUTPUT] {output_dir}")
    print(f"[CORE]   {exe}")

    # 1. Subtitle export
    if formats:
        print(f"[SUBS]   Formats: {', '.join(f.upper() for f in formats)}")
        try:
            exporter = SubtitleExporter(exe)
            result = exporter.export(draft_dir, output_dir, formats)
            if result:
                for fmt, path in sorted(result.items()):
                    print(f"[{fmt.upper()}] {path}")
            else:
                print("[SUBS]   No subtitles found.")
        except Exception as e:
            print(f"[ERROR]  Subtitle export failed: {e}", file=sys.stderr)

    # 2. XML + JSON export
    if want_xml or want_json:
        try:
            print("[LOAD]   Loading draft via plugin-core...")
            timeline = _load_draft_via_core(exe, draft_dir)
            name = sanitize_filename(timeline.get("name", "timeline"))

            if want_xml:
                xml_path = os.path.join(output_dir, f"{name}.xml")
                generate_xml(timeline, xml_path)
                print(f"[XML]    {xml_path}")

            if want_json:
                json_path = os.path.join(output_dir, f"{name}_timeline.json")
                generate_json(timeline, json_path)
                print(f"[JSON]   {json_path}")
        except Exception as e:
            print(f"[ERROR]  XML/JSON export failed: {e}", file=sys.stderr)

    print(f"\n[DONE]")


# ═══════════════════════════════════════════════════════════════════════════════
# 9. Entry Point
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    cli_mode()


if __name__ == "__main__":
    main()
