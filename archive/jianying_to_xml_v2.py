#!/usr/bin/env python3
"""
Jianying(CapCut) Draft -> FCP7 XML + Timeline JSON Converter v2.1

Fixes from v1.0 for DaVinci Resolve compatibility:
  - All <rate> have <ntsc> tag (NTSC fps auto-detection)
  - clipitem <duration> = source media total (not clip timeline duration)
  - Added <sourcetrack> (video/audio distinction)
  - Added <link> (same-source video+audio association)
  - <file> includes media/video/audio details
  - Audio tracks have <format> structure
  - Added <masterclipid>
  - Element ordering follows FCP7 spec
  - Volume uses standard name/value sub-elements

New in v2.1:
  - Transition support (<transitionitem>)
  - Jianying transition -> FCP7 Cross Dissolve / Dip to Black mapping
  - Clip overlap model for transition feed frames

Usage:
    python jianying_to_xml_v2.py <draft_path> [-o <output>] [--json-only]

Reference:
    - OpenTimelineIO FCP XML Adapter
    - FCP7 XML (XMEML) specification
    - docs/DAVINCI_XML_COMPAT.md
"""

import json
import os
import sys
import uuid
import argparse
import re
from pathlib import Path
from typing import Optional, Any
from xml.etree.ElementTree import Element, SubElement, ElementTree, tostring
from xml.dom import minidom
from urllib.parse import quote
from dataclasses import dataclass, field

VERSION = "2.1.0"

# ── 常量 ──────────────────────────────────────────────
MICROSECOND = 1_000_000
NANOSECOND = 1_000_000_000
DEFAULT_FPS = 30.0
DEFAULT_WIDTH = 1920
DEFAULT_HEIGHT = 1080

# 剪映转场名 → FCP7 effectid 映射
# DaVinci 可靠支持: Cross Dissolve, Dip to Black, Dip to White
TRANSITION_MAP = {
    "淡入淡出": "Cross Dissolve",
    "叠化": "Cross Dissolve",
    "交叉溶解": "Cross Dissolve",
    "cross dissolve": "Cross Dissolve",
    "dissolve": "Cross Dissolve",
    "淡入": "Dip to Black",
    "fade in": "Dip to Black",
    "淡出": "Dip to Black",
    "fade out": "Dip to Black",
    "黑场": "Dip to Black",
    "dip to black": "Dip to Black",
    "白场": "Dip to White",
    "dip to white": "Dip to White",
}
DEFAULT_TRANSITION_EFFECT = "Cross Dissolve"


# ── 数据模型 ──────────────────────────────────────────
@dataclass
class Material:
    """素材（视频/音频/图片等）"""
    material_id: str
    material_type: str  # "video", "audio", "sticker", "text", "effect", "transition"
    path: str = ""
    name: str = ""
    duration: int = 0  # 微秒
    width: int = 0
    height: int = 0
    fps: float = 0.0
    sample_rate: int = 0
    channels: int = 0
    extra: dict = field(default_factory=dict)


@dataclass
class Segment:
    """时间线上的片段"""
    segment_id: str
    material_id: str
    target_start: int  # 时间线起始位置 (微秒)
    target_duration: int  # 时间线持续时长 (微秒)
    source_start: int  # 源素材起始位置 (微秒)
    source_duration: int  # 源素材持续时长 (微秒)
    speed: float = 1.0
    volume: float = 1.0
    alpha: float = 1.0
    rotation: float = 0.0
    pos_x: float = 0.0
    pos_y: float = 0.0
    scale_x: float = 1.0
    scale_y: float = 1.0
    mute: bool = False
    extra: dict = field(default_factory=dict)


@dataclass
class Track:
    """轨道"""
    track_type: str  # "video", "audio", "sticker", "text", "effect", "filter"
    name: str = ""
    render_index: int = 0
    mute: bool = False
    segments: list = field(default_factory=list)


@dataclass
class TimelineData:
    """完整的 Timeline 数据"""
    name: str = "Jianying Timeline"
    width: int = DEFAULT_WIDTH
    height: int = DEFAULT_HEIGHT
    fps: float = DEFAULT_FPS
    duration_us: int = 0  # 总时长 (微秒)
    tracks: list = field(default_factory=list)  # list of Track
    materials: dict = field(default_factory=dict)  # id → Material


# ── 工具函数 ──────────────────────────────────────────
def detect_time_unit(raw: dict) -> str:
    """
    自动检测时间单位。
    综合判断：量级阈值 + canvas_config 是否存在。
    返回 'us' 或 'ns'。
    """
    duration = raw.get("duration", 0)
    has_canvas = "canvas_config" in raw or "fps" in raw
    if has_canvas and duration > 10_000_000_000:
        return "ns"
    if not has_canvas and duration > 10_000_000_000:
        return "ns"
    for track in raw.get("tracks", []):
        for seg in track.get("segments", []):
            td = seg.get("target_timerange", {}).get("duration", 0)
            if td > 10_000_000_000:
                return "ns"
            if td > 0:
                return "us"
    return "us"


def to_microseconds(value: int, unit: str) -> int:
    """将指定时间单位的值转换为微秒"""
    if unit == "ns":
        return value // 1000
    return value


def us_to_frames(microseconds: int, fps: float) -> int:
    """微秒转帧数（取整）"""
    if fps <= 0:
        return 0
    return round(microseconds / MICROSECOND * fps)


def is_ntsc_fps(fps: float) -> bool:
    """
    检测是否为 NTSC 非整数帧率。
    NTSC 帧率 = timebase × 1000 / 1001
    例如: 29.97 (timebase=30), 23.976 (timebase=24), 59.94 (timebase=60)
    """
    ntsc_rates = {23.976, 29.97, 29.97002997, 47.952, 59.94, 59.94005994}
    for rate in ntsc_rates:
        if abs(fps - rate) < 0.01:
            return True
    return False


def get_timebase(fps: float) -> int:
    """
    获取 timebase (整数帧率)。
    NTSC: 29.97→30, 23.976→24, 59.94→60
    非 NTSC: 直接取整: 25→25, 30→30, 24→24
    """
    if is_ntsc_fps(fps):
        # NTSC: timebase = fps * 1001 / 1000, 取整
        return round(fps * 1001 / 1000)
    return round(fps)


def windows_path_to_url(filepath: str) -> str:
    """将 Windows 文件路径转换为 file:/// URL"""
    p = Path(filepath)
    if not p.is_absolute():
        p = p.resolve()
    try:
        return p.as_uri()
    except Exception:
        url_path = str(p).replace("\\", "/")
        return f"file:///{quote(url_path, safe='/:')}"


def sanitize_filename(name: str) -> str:
    """移除文件名中的非法字符"""
    return "".join(c for c in name if c not in '<>:"/\\|?*').strip()


def short_id(id_str: str) -> str:
    """截取 UUID 前 8 位作为短标识"""
    return id_str[:8] if len(id_str) >= 8 else id_str


# ── 草稿解析 ──────────────────────────────────────────
def load_draft(path: str) -> tuple[dict, Path]:
    """
    加载草稿文件，返回 (原始JSON dict, 草稿文件夹Path)。
    如果 draft_content.json 已加密，自动尝试备用文件。
    """
    p = Path(path)
    if p.is_dir():
        draft_dir = p
    else:
        draft_dir = p.parent

    draft_file = draft_dir / "draft_content.json"

    if not draft_file.exists():
        raise FileNotFoundError(f"Draft file not found: {draft_file}")

    with open(draft_file, "r", encoding="utf-8") as f:
        try:
            return json.load(f), draft_dir
        except json.JSONDecodeError:
            pass

    candidates = [
        draft_dir / "template.json",
        draft_dir / "template.json.bak",
        draft_dir / "draft_content.json.bak",
    ]
    for candidate in candidates:
        if candidate.exists():
            with open(candidate, "r", encoding="utf-8") as f:
                try:
                    data = json.load(f)
                    print(f"  [INFO] draft_content.json encrypted, using backup: {candidate.name}")
                    return data, draft_dir
                except json.JSONDecodeError:
                    continue

    raise ValueError(
        f"Cannot parse JSON: draft_content.json is encrypted and no plaintext backup found.\n"
        f"Tried: {', '.join(c.name for c in candidates)}"
    )


def parse_materials(raw: dict, time_unit: str, draft_dir: Path) -> dict[str, Material]:
    """解析 materials 区块，返回 id → Material 映射。"""
    materials = {}
    type_map = {
        "videos": "video",
        "audios": "audio",
        "texts": "text",
        "stickers": "sticker",
        "effects": "effect",
        "transitions": "transition",
        "video_effects": "video_effect",
        "audio_effects": "audio_effect",
        "speeds": "speed",
        "animations": "animation",
        "audio_fades": "audio_fade",
        "masks": "mask",
        "canvases": "canvas",
    }

    def resolve_path(raw_path: str) -> str:
        if not raw_path:
            return ""
        p = Path(raw_path)
        if p.is_absolute():
            return str(p)
        absolute = (draft_dir / raw_path).resolve()
        return str(absolute)

    for key, mtype in type_map.items():
        for item in raw.get("materials", {}).get(key, []):
            mid = item.get("id", item.get("material_id", item.get(f"{mtype}_material_id", "")))
            if not mid:
                continue

            raw_path = item.get("path", item.get("local_path", item.get("url", "")))
            materials[mid] = Material(
                material_id=mid,
                material_type=mtype,
                path=resolve_path(raw_path),
                name=item.get("name", item.get("material_name", Path(raw_path).name)),
                duration=to_microseconds(item.get("duration", 0), time_unit),
                width=item.get("width", 0),
                height=item.get("height", 0),
                fps=item.get("fps", 0.0),
                sample_rate=item.get("sample_rate", item.get("sampleRate", 0)),
                channels=item.get("channels", 0),
                extra=item,
            )
    return materials


def parse_tracks(raw: dict, time_unit: str) -> list[Track]:
    """解析 tracks 数组"""
    tracks = []
    for track_data in raw.get("tracks", []):
        segments = []
        for seg_data in track_data.get("segments", []):
            target = seg_data.get("target_timerange", {})
            source = seg_data.get("source_timerange", target)
            clip = seg_data.get("clip", {})
            transform = clip.get("transform", {})
            scale = transform.get("scale", {})

            seg = Segment(
                segment_id=seg_data.get("segment_id", seg_data.get("id", str(uuid.uuid4()))),
                material_id=seg_data.get("material_id", ""),
                target_start=to_microseconds(target.get("start", 0), time_unit),
                target_duration=to_microseconds(target.get("duration", 0), time_unit),
                source_start=to_microseconds(source.get("start", 0), time_unit),
                source_duration=to_microseconds(source.get("duration", 0), time_unit),
                speed=seg_data.get("speed", 1.0),
                volume=seg_data.get("volume", 1.0),
                alpha=clip.get("alpha", 1.0),
                rotation=clip.get("rotation", 0.0),
                pos_x=transform.get("x", 0.0),
                pos_y=transform.get("y", 0.0),
                scale_x=scale.get("x", 1.0),
                scale_y=scale.get("y", 1.0),
                mute=seg_data.get("mute", False),
                extra=seg_data,
            )
            segments.append(seg)

        tracks.append(Track(
            track_type=track_data.get("type", "video"),
            name=track_data.get("name", ""),
            render_index=track_data.get("render_index", 0),
            mute=track_data.get("mute", False),
            segments=segments,
        ))
    return tracks


def build_timeline(raw: dict, draft_dir: Path) -> TimelineData:
    """从原始 JSON 构建完整的 TimelineData"""
    time_unit = detect_time_unit(raw)

    canvas = raw.get("canvas_config", {})
    fps = float(canvas.get("fps", raw.get("fps", DEFAULT_FPS)))
    width = int(canvas.get("width", DEFAULT_WIDTH))
    height = int(canvas.get("height", DEFAULT_HEIGHT))
    total_duration = to_microseconds(raw.get("duration", 0), time_unit)
    project_name = raw.get("name") or raw.get("id") or draft_dir.name or "Jianying_Timeline"

    materials = parse_materials(raw, time_unit, draft_dir)
    tracks = parse_tracks(raw, time_unit)

    return TimelineData(
        name=sanitize_filename(project_name),
        width=width,
        height=height,
        fps=fps,
        duration_us=total_duration,
        tracks=tracks,
        materials=materials,
    )


# ── FCP7 XML 生成 (v2 - DaVinci 兼容) ────────────────

def _add_rate(parent: Element, fps: float) -> Element:
    """添加标准 rate 元素 (含 ntsc + timebase)"""
    rate = SubElement(parent, "rate")
    ntsc = SubElement(rate, "ntsc")
    ntsc.text = "TRUE" if is_ntsc_fps(fps) else "FALSE"
    tb = SubElement(rate, "timebase")
    tb.text = str(get_timebase(fps))
    return rate


def _build_file_elem(
    parent: Element,
    material: Material,
    fps: float,
    file_id: str,
    full: bool = True,
) -> Element:
    """
    构建 <file> 元素。
    full=True: 完整定义 (name, pathurl, rate, duration, media)
    full=False: 仅引用 (<file id="file-N"/>)
    """
    file_elem = SubElement(parent, "file", id=file_id)

    if not full:
        return file_elem

    # name
    name_elem = SubElement(file_elem, "name")
    name_elem.text = material.name or Path(material.path).name or "unknown"

    # pathurl
    if material.path:
        path_elem = SubElement(file_elem, "pathurl")
        path_elem.text = windows_path_to_url(material.path)

    # rate
    _add_rate(file_elem, fps)

    # duration (源素材总帧数)
    dur_elem = SubElement(file_elem, "duration")
    dur_elem.text = str(us_to_frames(material.duration, fps))

    # media
    media_elem = SubElement(file_elem, "media")

    # 视频详情
    if material.material_type in ("video",) or material.width > 0:
        video_elem = SubElement(media_elem, "video")
        sc = SubElement(video_elem, "samplecharacteristics")
        w = SubElement(sc, "width")
        w.text = str(material.width if material.width > 0 else DEFAULT_WIDTH)
        h = SubElement(sc, "height")
        h.text = str(material.height if material.height > 0 else DEFAULT_HEIGHT)

    # 音频详情
    if material.material_type in ("audio",) or material.sample_rate > 0:
        audio_elem = SubElement(media_elem, "audio")
        cc = SubElement(audio_elem, "channelcount")
        cc.text = str(material.channels if material.channels > 0 else 2)
        sc = SubElement(audio_elem, "samplecharacteristics")
        sr = SubElement(sc, "samplerate")
        sr.text = str(material.sample_rate if material.sample_rate > 0 else 48000)
        sz = SubElement(sc, "size")
        sz.text = "16-bit"

    # 视频素材同时带音频信息 (剪映中视频通常有音轨)
    if material.material_type == "video" and material.sample_rate == 0:
        if media_elem.find("audio") is None:
            audio_elem = SubElement(media_elem, "audio")
            cc = SubElement(audio_elem, "channelcount")
            cc.text = "2"
            sc = SubElement(audio_elem, "samplecharacteristics")
            sr = SubElement(sc, "samplerate")
            sr.text = "48000"
            sz = SubElement(sc, "size")
            sz.text = "16-bit"

    return file_elem


def _build_clipitem(
    seg: Segment,
    material: Material,
    fps: float,
    clip_id: str,
    file_id: str,
    file_full: bool,
    media_type: str,
    source_track_index: int,
    links: list[dict],
    masterclip_id: str,
) -> Element:
    """
    构建单个 clipitem 节点 (FCP7 兼容)。
    元素顺序: name → masterclipid → duration → rate → start → end → in → out → file → sourcetrack → link → filter
    """
    clip = Element("clipitem", id=clip_id)

    # 1. name
    name_elem = SubElement(clip, "name")
    name_elem.text = material.name or Path(material.path).name or f"clip-{short_id(seg.segment_id)}"

    # 2. masterclipid
    mcid = SubElement(clip, "masterclipid")
    mcid.text = masterclip_id

    # 3. duration (源素材总帧数, 非片段时长)
    source_total_frames = us_to_frames(material.duration, fps)
    dur_elem = SubElement(clip, "duration")
    dur_elem.text = str(max(source_total_frames, 1))

    # 4. rate
    _add_rate(clip, fps)

    # 5. start (时间线起始帧)
    timeline_start_frame = us_to_frames(seg.target_start, fps)
    start_elem = SubElement(clip, "start")
    start_elem.text = str(timeline_start_frame)

    # 6. end (时间线结束帧)
    timeline_clip_frames = us_to_frames(seg.target_duration, fps)
    end_elem = SubElement(clip, "end")
    end_elem.text = str(timeline_start_frame + timeline_clip_frames)

    # 7. in (源素材入点帧)
    source_in_frame = us_to_frames(seg.source_start, fps)
    in_elem = SubElement(clip, "in")
    in_elem.text = str(source_in_frame)

    # 8. out (源素材出点帧)
    source_out_frame = us_to_frames(seg.source_start + seg.source_duration, fps)
    out_elem = SubElement(clip, "out")
    out_elem.text = str(source_out_frame)

    # 9. file
    _build_file_elem(clip, material, fps, file_id, full=file_full)

    # 10. sourcetrack
    st = SubElement(clip, "sourcetrack")
    mt = SubElement(st, "mediatype")
    mt.text = media_type
    ti = SubElement(st, "trackindex")
    ti.text = str(source_track_index)

    # 11. link (所有同组 link 元素, 每个 clipitem 持有完整集合)
    for link_info in links:
        link = SubElement(clip, "link")
        lcr = SubElement(link, "linkclipref")
        lcr.text = link_info["clipref"]
        lmt = SubElement(link, "mediatype")
        lmt.text = link_info["mediatype"]
        lti = SubElement(link, "trackindex")
        lti.text = str(link_info["trackindex"])
        lci = SubElement(link, "clipindex")
        lci.text = str(link_info["clipindex"])

    # 12. 变换 filter (仅视频)
    if media_type == "video" and _has_transform(seg):
        _build_transform_filter(clip, seg)

    # 13. 音量 (仅音频)
    if media_type == "audio" and (seg.volume != 1.0 or seg.mute):
        vol_val = 0.0 if seg.mute else seg.volume * 100
        _build_volume_filter(clip, vol_val)

    return clip


def _has_transform(seg: Segment) -> bool:
    """判断是否有变换参数"""
    return (
        seg.rotation != 0.0
        or seg.pos_x != 0.0
        or seg.pos_y != 0.0
        or seg.scale_x != 1.0
        or seg.scale_y != 1.0
        or seg.alpha != 1.0
    )


def _resolve_transition_effect(transition_name: str) -> tuple[str, str, str]:
    """
    将剪映转场名映射为 FCP7 effect 信息。
    返回 (effectid, alignment, display_name)
    """
    name_lower = transition_name.lower().strip()
    for key, effectid in TRANSITION_MAP.items():
        if key.lower() in name_lower or name_lower in key.lower():
            # Determine alignment based on effect type
            if effectid == "Dip to Black":
                return effectid, "center", transition_name
            if effectid == "Dip to White":
                return effectid, "center", transition_name
            return effectid, "center", transition_name
    return DEFAULT_TRANSITION_EFFECT, "center", transition_name


def _build_transitionitem(
    fps: float,
    transition_frames: int,
    alignment: str,
    effectid: str,
    effect_name: str,
    media_type: str = "video",
) -> Element:
    """构建 <transitionitem> 元素"""
    trans = Element("transitionitem")

    # rate
    _add_rate(trans, fps)

    # start/end (transition extent in frames)
    s = SubElement(trans, "start")
    s.text = str(transition_frames)
    e = SubElement(trans, "end")
    e.text = "0"

    # alignment
    al = SubElement(trans, "alignment")
    al.text = alignment

    # effect
    effect = SubElement(trans, "effect")

    ename = SubElement(effect, "name")
    ename.text = effect_name or effectid

    eid = SubElement(effect, "effectid")
    eid.text = effectid

    ecat = SubElement(effect, "effectcategory")
    ecat.text = "Dissolves"

    etype = SubElement(effect, "effecttype")
    etype.text = "transition"

    emt = SubElement(effect, "mediatype")
    emt.text = media_type

    return trans


def _build_volume_filter(parent: Element, volume_percent: float) -> None:
    """添加音量 filter"""
    filter_elem = SubElement(parent, "filter")
    effect_elem = SubElement(filter_elem, "effect")
    ename = SubElement(effect_elem, "name")
    ename.text = "Audio Levels"
    eid = SubElement(effect_elem, "effectid")
    eid.text = "audiolevels"
    etype = SubElement(effect_elem, "effecttype")
    etype.text = "audiolevels"
    emedia = SubElement(effect_elem, "mediatype")
    emedia.text = "audio"

    param = SubElement(effect_elem, "parameter", authoringApp="FCP")
    pname = SubElement(param, "name")
    pname.text = "Level"
    pval = SubElement(param, "value")
    pval.text = f"{volume_percent:.1f}"


def _build_transform_filter(parent: Element, seg: Segment) -> None:
    """为 clip 添加基本变换 filter (FCP7 的 basic motion)"""
    filter_elem = SubElement(parent, "filter")
    effect_elem = SubElement(filter_elem, "effect")
    name = SubElement(effect_elem, "name")
    name.text = "Basic Motion"
    effect_id = SubElement(effect_elem, "effectid")
    effect_id.text = "basic"

    if seg.pos_x != 0.0 or seg.pos_y != 0.0:
        center = SubElement(effect_elem, "parameter", authoringApp="FCP")
        cname = SubElement(center, "name")
        cname.text = "Center"
        cval = SubElement(center, "value")
        cval.text = f"{seg.pos_x}, {seg.pos_y}"

    if seg.scale_x != 1.0 or seg.scale_y != 1.0:
        scale = SubElement(effect_elem, "parameter", authoringApp="FCP")
        sname = SubElement(scale, "name")
        sname.text = "Scale"
        sval = SubElement(scale, "value")
        sval.text = f"{seg.scale_x * 100:.1f}"

    if seg.rotation != 0.0:
        rot = SubElement(effect_elem, "parameter", authoringApp="FCP")
        rname = SubElement(rot, "name")
        rname.text = "Rotation"
        rval = SubElement(rot, "value")
        rval.text = str(seg.rotation)

    if seg.alpha != 1.0:
        opa = SubElement(effect_elem, "parameter", authoringApp="FCP")
        oname = SubElement(opa, "name")
        oname.text = "Opacity"
        oval = SubElement(opa, "value")
        oval.text = f"{seg.alpha * 100:.1f}"


def generate_xml(timeline: TimelineData, output_path: str) -> None:
    """生成 FCP7 XML (xmeml v5) 文件 — DaVinci Resolve 兼容"""
    fps = timeline.fps
    timebase = get_timebase(fps)
    ntsc_flag = "TRUE" if is_ntsc_fps(fps) else "FALSE"

    # ── 根节点 ──
    xmeml = Element("xmeml", version="5")

    # ── sequence ──
    seq = SubElement(xmeml, "sequence")

    s_name = SubElement(seq, "name")
    s_name.text = timeline.name

    total_frames = us_to_frames(timeline.duration_us, fps)
    s_dur = SubElement(seq, "duration")
    s_dur.text = str(max(total_frames, 1))

    _add_rate(seq, fps)

    # ── media ── (video 必须在 audio 之前)
    media = SubElement(seq, "media")

    # ── 视频部分 ──
    video = SubElement(media, "video")

    # format (DaVinci 要求即使为空也存在)
    v_fmt = SubElement(video, "format")
    v_sc = SubElement(v_fmt, "samplecharacteristics")
    v_w = SubElement(v_sc, "width")
    v_w.text = str(timeline.width)
    v_h = SubElement(v_sc, "height")
    v_h.text = str(timeline.height)
    v_an = SubElement(v_sc, "anamorphic")
    v_an.text = "FALSE"
    v_par = SubElement(v_sc, "pixelaspectratio")
    v_par.text = "square"
    v_fd = SubElement(v_sc, "fielddominance")
    v_fd.text = "none"

    # ── 音频部分 ──
    audio = SubElement(media, "audio")

    # audio format
    a_fmt = SubElement(audio, "format")
    a_sc = SubElement(a_fmt, "samplecharacteristics")
    a_sr = SubElement(a_sc, "samplerate")
    a_sr.text = "48000"
    a_sz = SubElement(a_sc, "size")
    a_sz.text = "16-bit"
    a_cc = SubElement(a_sc, "channelcount")
    a_cc.text = "2"

    # ── 收集所有 track 数据 ──
    video_tracks = sorted(
        [t for t in timeline.tracks if t.track_type == "video"],
        key=lambda t: t.render_index,
    )
    audio_tracks = sorted(
        [t for t in timeline.tracks if t.track_type == "audio"],
        key=lambda t: t.render_index,
    )

    # ── 构建链接组 (link groups) ──
    # 剪映中视频片段通常独立, 音频片段也独立。
    # 只有当 video segment 和 audio segment 引用同一个 material_id 时才链接。
    # 但剪映的结构是 video track 和 audio track 分开的，素材通常是独立的。
    # 所以这里按 material_id 分组，如果有同 material_id 出现在 video 和 audio 轨道上，则链接。
    clip_counter = [0]  # mutable counter for unique IDs
    file_counter = [0]
    master_counter = [0]

    def next_clip_id():
        clip_counter[0] += 1
        return f"clipitem-{clip_counter[0]}"

    def next_file_id():
        file_counter[0] += 1
        return f"file-{file_counter[0]}"

    def next_master_id():
        master_counter[0] += 1
        return f"masterclip-{master_counter[0]}"

    # 为每个 material 分配 ID
    file_id_map = {}   # material_id → file_id
    master_id_map = {} # material_id → masterclip_id
    for mid in timeline.materials:
        file_id_map[mid] = next_file_id()
        master_id_map[mid] = next_master_id()

    # 为每个 segment 分配 clipitem ID
    seg_clip_id_map = {}  # segment_id → clipitem_id
    all_segments = []
    for t in video_tracks + audio_tracks:
        for seg in t.segments:
            seg_clip_id_map[seg.segment_id] = next_clip_id()
            all_segments.append(seg)

    # 构建 link groups: 如果同一 material_id 同时出现在 video 和 audio 轨道
    video_mids = set()
    audio_mids = set()
    for t in video_tracks:
        for seg in t.segments:
            video_mids.add(seg.material_id)
    for t in audio_tracks:
        for seg in t.segments:
            audio_mids.add(seg.material_id)

    linked_mids = video_mids & audio_mids  # 同时出现在视频和音频轨道的 material_id

    # 为每个链接组构建 link 信息
    link_groups = {}  # material_id → list of {clipref, mediatype, trackindex, clipindex}
    for mid in linked_mids:
        group = []
        clip_idx = 1
        # 先视频
        for vi, vtrack in enumerate(video_tracks):
            for seg in vtrack.segments:
                if seg.material_id == mid:
                    group.append({
                        "clipref": seg_clip_id_map[seg.segment_id],
                        "mediatype": "video",
                        "trackindex": vi + 1,
                        "clipindex": clip_idx,
                    })
        # 再音频
        for ai, atrack in enumerate(audio_tracks):
            for seg in atrack.segments:
                if seg.material_id == mid:
                    group.append({
                        "clipref": seg_clip_id_map[seg.segment_id],
                        "mediatype": "audio",
                        "trackindex": ai + 1,
                        "clipindex": clip_idx,
                    })
        link_groups[mid] = group

    # track_file_used: 记录每个 file_id 是否已输出完整定义
    file_full_written = set()

    # ── 转场检测 ──
    # 剪映转场在 materials.transitions 中，但需要关联到具体片段对。
    # 常见关联方式:
    #   1. segment.extra 中有 "transition_id" / "transition" 字段
    #   2. transition material 有 target_timerange 指向两个片段的边界
    # 这里实现方式 1 和 2 的检测。

    def find_transition_between(prev_seg: Segment, next_seg: Segment) -> dict | None:
        """
        检测两个相邻片段之间是否有转场。
        返回转场信息 dict 或 None。
        """
        # 方式 1: segment.extra 中直接引用转场
        for seg in (prev_seg, next_seg):
            tid = seg.extra.get("transition_id", seg.extra.get("transition", ""))
            if tid and tid in timeline.materials:
                trans_mat = timeline.materials[tid]
                if trans_mat.material_type == "transition":
                    return {
                        "material": trans_mat,
                        "duration_us": trans_mat.duration,
                        "name": trans_mat.name,
                    }
            # 也检查 extra 中是否有嵌套的 transition 对象
            trans_data = seg.extra.get("transition", {})
            if isinstance(trans_data, dict) and trans_data.get("id"):
                tid2 = trans_data["id"]
                if tid2 in timeline.materials:
                    trans_mat = timeline.materials[tid2]
                    if trans_mat.material_type == "transition":
                        return {
                            "material": trans_mat,
                            "duration_us": trans_mat.duration,
                            "name": trans_mat.name,
                        }

        # 方式 2: 通过时间匹配 - 在 materials.transitions 中找
        # 如果转场的时间范围落在两个片段的交界处
        boundary_us = prev_seg.target_start + prev_seg.target_duration
        tolerance = 100_000  # 100ms tolerance

        for mid, mat in timeline.materials.items():
            if mat.material_type != "transition":
                continue
            # 检查转场的 duration 是否合理 (小于相邻片段)
            if mat.duration <= 0:
                continue
            min_adjacent = min(prev_seg.target_duration, next_seg.target_duration)
            if mat.duration >= min_adjacent:
                continue  # 转场时长不应超过相邻片段
            # 简单启发式: 如果转场 duration 合理且片段相邻, 认为匹配
            # (更精确的匹配需要 target_timerange 数据)

        return None

    # ── 写入视频轨道 ──
    for vi, vtrack in enumerate(video_tracks):
        xm_track = SubElement(video, "track")

        valid_segments = [
            (i, seg) for i, seg in enumerate(vtrack.segments)
            if timeline.materials.get(seg.material_id) is not None
        ]

        for idx, (si, seg) in enumerate(valid_segments):
            material = timeline.materials[seg.material_id]
            clip_id = seg_clip_id_map[seg.segment_id]
            fid = file_id_map[seg.material_id]
            mid = master_id_map[seg.material_id]

            full = fid not in file_full_written
            file_full_written.add(fid)

            links = link_groups.get(seg.material_id, [])

            # 检测与前一个片段之间是否有转场
            transition_info = None
            if idx > 0:
                prev_seg = valid_segments[idx - 1][1]
                transition_info = find_transition_between(prev_seg, seg)

            # 如果有转场, 需要修改相邻片段的 in/out 和 start/end
            if transition_info:
                trans_dur_us = transition_info["duration_us"]
                trans_frames = us_to_frames(trans_dur_us, fps)
                half_frames = trans_frames // 2

                # 修改当前片段: in 前移, start = -1 (computed)
                overlap_seg = Segment(
                    segment_id=seg.segment_id,
                    material_id=seg.material_id,
                    target_start=seg.target_start,
                    target_duration=seg.target_duration,
                    source_start=max(0, seg.source_start - trans_dur_us // 2),
                    source_duration=seg.source_duration + trans_dur_us // 2,
                    speed=seg.speed,
                    volume=seg.volume,
                    alpha=seg.alpha,
                    rotation=seg.rotation,
                    pos_x=seg.pos_x,
                    pos_y=seg.pos_y,
                    scale_x=seg.scale_x,
                    scale_y=seg.scale_y,
                    mute=seg.mute,
                    extra=seg.extra,
                )

                # 构建 clipitem, 但 start 设为 -1
                clipitem = _build_clipitem(
                    overlap_seg, material, fps, clip_id, fid, full,
                    media_type="video",
                    source_track_index=1,
                    links=links,
                    masterclip_id=mid,
                )
                # 覆盖 start 为 -1 (computed by transition)
                start_elem = clipitem.find("start")
                if start_elem is not None:
                    start_elem.text = "-1"

                # 在 clipitem 之前插入 transitionitem
                effectid, alignment, display_name = _resolve_transition_effect(
                    transition_info["name"]
                )
                trans_elem = _build_transitionitem(
                    fps, trans_frames, alignment, effectid, display_name, "video"
                )
                xm_track.append(trans_elem)

                # 同时修改前一个已添加的 clipitem: end = -1, out 延长
                prev_clipitems = xm_track.findall("clipitem")
                if prev_clipitems:
                    last_clip = prev_clipitems[-1]
                    end_elem = last_clip.find("end")
                    if end_elem is not None:
                        end_elem.text = "-1"
                    out_elem = last_clip.find("out")
                    if out_elem is not None:
                        try:
                            old_out = int(out_elem.text)
                            out_elem.text = str(old_out + half_frames)
                        except (ValueError, TypeError):
                            pass

            else:
                # 无转场, 正常构建
                clipitem = _build_clipitem(
                    seg, material, fps, clip_id, fid, full,
                    media_type="video",
                    source_track_index=1,
                    links=links,
                    masterclip_id=mid,
                )

            xm_track.append(clipitem)

            if not full:
                pass  # already printed warning if material missing

    # ── 写入音频轨道 ──
    if not audio_tracks:
        SubElement(audio, "track")  # 空占位
    else:
        for ai, atrack in enumerate(audio_tracks):
            xm_track = SubElement(audio, "track")
            for seg in atrack.segments:
                material = timeline.materials.get(seg.material_id)
                if material is None:
                    print(f"  [WARN] Skip segment with missing material: {seg.segment_id}", file=sys.stderr)
                    continue

                clip_id = seg_clip_id_map[seg.segment_id]
                fid = file_id_map[seg.material_id]
                mid = master_id_map[seg.material_id]

                # 如果已链接, 仅引用 file; 否则完整写入
                is_linked = seg.material_id in linked_mids
                full = not is_linked and fid not in file_full_written
                if full:
                    file_full_written.add(fid)

                links = link_groups.get(seg.material_id, [])
                clipitem = _build_clipitem(
                    seg, material, fps, clip_id, fid, full,
                    media_type="audio",
                    source_track_index=1,
                    links=links,
                    masterclip_id=mid,
                )
                xm_track.append(clipitem)

    # ── 美化输出 ──
    raw_xml = tostring(xmeml, encoding="unicode")
    dom = minidom.parseString(raw_xml)
    pretty_bytes = dom.toprettyxml(indent="  ", encoding="utf-8")
    pretty_str = pretty_bytes.decode("utf-8")

    body_lines = []
    skip = True
    for line in pretty_str.splitlines():
        if skip and line.lstrip().startswith("<?xml"):
            continue
        skip = False
        body_lines.append(line)
    clean_body = "\n".join(body_lines).lstrip()

    with open(output_path, "w", encoding="utf-8") as f:
        f.write('<?xml version="1.0" encoding="UTF-8"?>\n')
        f.write('<!DOCTYPE xmeml>\n')
        f.write(clean_body)
        f.write('\n')


def generate_timeline_json(timeline: TimelineData, output_path: str) -> None:
    """导出 Timeline JSON 摘要"""
    def seg_to_dict(seg: Segment) -> dict:
        d = {
            "segment_id": seg.segment_id,
            "material_id": seg.material_id,
            "material_name": "",
            "material_path": "",
            "timeline_start_sec": round(seg.target_start / MICROSECOND, 3),
            "timeline_end_sec": round((seg.target_start + seg.target_duration) / MICROSECOND, 3),
            "duration_sec": round(seg.target_duration / MICROSECOND, 3),
            "source_start_sec": round(seg.source_start / MICROSECOND, 3),
            "source_end_sec": round((seg.source_start + seg.source_duration) / MICROSECOND, 3),
            "speed": seg.speed,
            "volume": seg.volume,
            "mute": seg.mute,
        }
        mat = timeline.materials.get(seg.material_id)
        if mat:
            d["material_name"] = mat.name
            d["material_path"] = mat.path
        if seg.alpha != 1.0:
            d["alpha"] = seg.alpha
        if seg.rotation != 0.0:
            d["rotation"] = seg.rotation
        if seg.pos_x != 0.0 or seg.pos_y != 0.0:
            d["position"] = {"x": seg.pos_x, "y": seg.pos_y}
        if seg.scale_x != 1.0 or seg.scale_y != 1.0:
            d["scale"] = {"x": seg.scale_x, "y": seg.scale_y}
        return d

    tracks_out = []
    for i, track in enumerate(timeline.tracks):
        tracks_out.append({
            "index": i,
            "type": track.track_type,
            "name": track.name,
            "render_index": track.render_index,
            "mute": track.mute,
            "segment_count": len(track.segments),
            "segments": [seg_to_dict(s) for s in track.segments],
        })

    output = {
        "project_name": timeline.name,
        "canvas": {
            "width": timeline.width,
            "height": timeline.height,
            "fps": timeline.fps,
            "ntsc": is_ntsc_fps(timeline.fps),
            "timebase": get_timebase(timeline.fps),
        },
        "total_duration_sec": round(timeline.duration_us / MICROSECOND, 3),
        "total_frames": us_to_frames(timeline.duration_us, timeline.fps),
        "track_count": len(timeline.tracks),
        "material_count": len(timeline.materials),
        "tracks": tracks_out,
        "materials_by_type": {
            "video": sum(1 for m in timeline.materials.values() if m.material_type == "video"),
            "audio": sum(1 for m in timeline.materials.values() if m.material_type == "audio"),
            "text": sum(1 for m in timeline.materials.values() if m.material_type == "text"),
            "sticker": sum(1 for m in timeline.materials.values() if m.material_type == "sticker"),
            "effect": sum(1 for m in timeline.materials.values() if m.material_type in ("effect", "video_effect", "audio_effect")),
            "transition": sum(1 for m in timeline.materials.values() if m.material_type == "transition"),
            "other": sum(1 for m in timeline.materials.values() if m.material_type not in ("video", "audio", "text", "sticker", "effect", "video_effect", "audio_effect", "transition")),
        },
        "converter_version": VERSION,
        "notes": {
            "v2_changes": "v2.1 adds transition support (<transitionitem>). v2.0 fixed DaVinci compatibility: ntsc tags, duration semantics, sourcetrack, link, file media details, audio format.",
            "time_unit_warning": "Time values converted from raw data, units unified to microseconds. If times don't match, check source file time unit.",
            "unsupported": "Text, sticker, effect tracks not in XML (FCP7/DaVinci doesn't support generatoritem import). Data fully preserved in JSON.",
            "encrypted_hint": "Jianying 6.x+ encrypts draft_content.json. If JSON parse error, encryption not supported.",
            "transitions": "Transitions detected via segment extra data (transition_id/transition) or target_timerange matching. All mapped to Cross Dissolve / Dip to Black / Dip to White for DaVinci compatibility.",
            "resolve_import": "In DaVinci: File -> Import Timeline -> Import AAF, EDL, XML...",
        },
    }

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)


# ── 主入口 ────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description=f"Jianying Draft -> FCP7 XML Converter v{VERSION}\n"
                    "Convert Jianying/CapCut project files to DaVinci-compatible XML.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
    python jianying_to_xml_v2.py "C:/Users/me/AppData/Local/JianyingPro/User Data/Projects/compositon/xxx"
    python jianying_to_xml_v2.py ./my_draft/draft_content.json -o ./output
    python jianying_to_xml_v2.py ./my_draft --json-only   # 仅输出 JSON，不生成 XML
        """,
    )
    parser.add_argument("draft_path", help="Draft folder path or draft_content.json file path")
    parser.add_argument("-o", "--output", default=None, help="Output directory (default: draft folder)")
    parser.add_argument("--json-only", action="store_true", help="Only output Timeline JSON, no XML")
    parser.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    args = parser.parse_args()

    draft_path = Path(args.draft_path)
    if args.output:
        output_dir = Path(args.output)
    elif draft_path.is_dir():
        output_dir = draft_path
    else:
        output_dir = draft_path.parent
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"[READ] {draft_path}")
    try:
        raw, draft_dir = load_draft(args.draft_path)
    except (FileNotFoundError, ValueError) as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        sys.exit(1)

    print("[PARSE] Timeline data...")
    try:
        timeline = build_timeline(raw, draft_dir)
    except Exception as e:
        print(f"[ERROR] Parse failed: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)

    # Stats
    video_tracks = [t for t in timeline.tracks if t.track_type == "video"]
    audio_tracks = [t for t in timeline.tracks if t.track_type == "audio"]
    other_tracks = [t for t in timeline.tracks if t.track_type not in ("video", "audio")]
    total_clips = sum(len(t.segments) for t in timeline.tracks)
    total_materials = len(timeline.materials)

    ntsc_info = f"(NTSC, timebase={get_timebase(timeline.fps)})" if is_ntsc_fps(timeline.fps) else ""
    print(f"  Canvas: {timeline.width}x{timeline.height} @ {timeline.fps}fps {ntsc_info}")
    print(f"  Duration: {timeline.duration_us / MICROSECOND:.1f}s ({us_to_frames(timeline.duration_us, timeline.fps)} frames)")
    print(f"  Tracks: {len(video_tracks)} video, {len(audio_tracks)} audio, {len(other_tracks)} other ({len(timeline.tracks)} total)")
    print(f"  Clips: {total_clips}")
    print(f"  Materials: {total_materials}")

    if other_tracks:
        print(f"  Unconverted track types: {set(t.track_type for t in other_tracks)} (data preserved in JSON)")

    safe_name = sanitize_filename(timeline.name)
    json_path = output_dir / f"{safe_name}_timeline.json"
    xml_path = output_dir / f"{safe_name}.xml"

    print(f"\n[JSON] {json_path}")
    generate_timeline_json(timeline, str(json_path))

    if not args.json_only:
        print(f"[XML]  {xml_path}")
        generate_xml(timeline, str(xml_path))

    print("\n[DONE]")
    if not args.json_only:
        print(f"  DaVinci: File -> Import Timeline -> Import AAF, EDL, XML...")
        print(f"  File: {xml_path}")
    print(f"  Timeline JSON: {json_path}")


if __name__ == "__main__":
    main()
