#!/usr/bin/env python3
"""
剪映(CapCut) 草稿 → FCP7 XML + Timeline JSON 转换器

将剪映工程文件转换为达芬奇(DaVinci Resolve)可导入的 FCP7 XML 格式。
同时输出 Timeline 数据 JSON 便于查看和手动核对。

用法:
    python jianying_to_xml.py <草稿路径> [-o <输出目录>] [--json-only]

<草稿路径> 可以是:
    - 包含 draft_content.json 的文件夹路径
    - draft_content.json 文件本身的路径

输出:
    - <项目名>.xml          : FCP7 XML，可导入达芬奇
    - <项目名>_timeline.json : Timeline 结构化数据

达芬奇导入方式:
    File → Import Timeline → Import AAF, EDL, XML... → 选择 .xml 文件

版本兼容:
    - 剪映 5.9 及以下: draft_content.json 为明文 JSON  支持解析
    - 剪映 6.x 及以上: draft_content.json 已加密      暂不支持

参考:
    - JianyingDraft.PY: https://github.com/notinmood/JianyingDraft.PY
    - pyJianYingDraft: https://github.com/Slihao/JianYingDraft
"""

import json
import os
import sys
import uuid
import argparse
from pathlib import Path
from typing import Optional, Any
from xml.etree.ElementTree import Element, SubElement, ElementTree, tostring
from xml.dom import minidom
from urllib.parse import quote
from dataclasses import dataclass, field

VERSION = "1.0.0"

# ── 常量 ──────────────────────────────────────────────
MICROSECOND = 1_000_000
NANOSECOND = 1_000_000_000
DEFAULT_FPS = 30.0
DEFAULT_WIDTH = 1920
DEFAULT_HEIGHT = 1080


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
    material_audios: list = field(default_factory=list)  # 独立音频素材
    material_videos: list = field(default_factory=list)  # 独立视频素材
    material_texts: list = field(default_factory=list)
    material_stickers: list = field(default_factory=list)


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
        # 有 canvas_config 但 duration 极大 → 可能是纳秒
        return "ns"
    if not has_canvas and duration > 10_000_000_000:
        # 无 canvas_config 且 duration 极大 → 也可能是纳秒
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


def windows_path_to_url(filepath: str) -> str:
    """将 Windows 文件路径转换为 file:/// URL"""
    p = Path(filepath)
    if not p.is_absolute():
        p = p.resolve()
    try:
        return p.as_uri()
    except Exception:
        # fallback: 手动编码
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
        raise FileNotFoundError(f"找不到草稿文件: {draft_file}")

    with open(draft_file, "r", encoding="utf-8") as f:
        try:
            return json.load(f), draft_dir
        except json.JSONDecodeError:
            pass  # 尝试备用文件

    # 尝试备用文件 (剪映 6.x+ 加密时的明文备份)
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
                    print(f"  ℹ draft_content.json 已加密，使用备用文件: {candidate.name}")
                    return data, draft_dir
                except json.JSONDecodeError:
                    continue

    raise ValueError(
        f"无法解析 JSON: draft_content.json 已加密且无可用的明文备份文件。\n"
        f"尝试过的备用文件: {', '.join(c.name for c in candidates)}"
    )


def parse_materials(raw: dict, time_unit: str, draft_dir: Path) -> dict[str, Material]:
    """解析 materials 区块，返回 id → Material 映射。draft_dir 用于解析相对路径。"""
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
        """将相对路径解析为绝对路径"""
        if not raw_path:
            return ""
        p = Path(raw_path)
        if p.is_absolute():
            return str(p)
        # 相对路径基于草稿目录
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


# ── FCP7 XML 生成 ─────────────────────────────────────
def _make_file_elem(parent: Element, material: Material, fps: float) -> Element:
    """为 clipitem 创建 <file> 子元素"""
    file_elem = SubElement(parent, "file", id=f"file-{short_id(material.material_id)}")

    name_elem = SubElement(file_elem, "name")
    name_elem.text = material.name or Path(material.path).name or "unknown"

    path_elem = SubElement(file_elem, "pathurl")
    if material.path:
        path_elem.text = windows_path_to_url(material.path)

    rate_elem = SubElement(file_elem, "rate")
    tb = SubElement(rate_elem, "timebase")
    tb.text = str(int(fps))

    duration_elem = SubElement(file_elem, "duration")
    duration_elem.text = str(us_to_frames(material.duration, fps))

    return file_elem


def _make_rate_elem(parent: Element, fps: float) -> Element:
    rate = SubElement(parent, "rate")
    tb = SubElement(rate, "timebase")
    tb.text = str(int(fps))
    return rate


def _build_clipitem(seg: Segment, material: Material, fps: float) -> Element:
    """构建单个 clipitem 节点"""
    clip = Element("clipitem", id=f"clip-{short_id(seg.segment_id)}")

    # 名称
    name_elem = SubElement(clip, "name")
    name_elem.text = material.name or Path(material.path).name or f"clip-{short_id(seg.segment_id)}"

    SubElement(clip, "enabled").text = "FALSE" if seg.mute else "TRUE"

    # 时间计算
    timeline_start_frame = us_to_frames(seg.target_start, fps)
    timeline_end_frame = us_to_frames(seg.target_start + seg.target_duration, fps)
    source_in_frame = us_to_frames(seg.source_start, fps)
    source_out_frame = us_to_frames(seg.source_start + seg.source_duration, fps)

    dur_elem = SubElement(clip, "duration")
    dur_elem.text = str(timeline_end_frame - timeline_start_frame)

    start_elem = SubElement(clip, "start")
    start_elem.text = str(timeline_start_frame)

    end_elem = SubElement(clip, "end")
    end_elem.text = str(timeline_end_frame)

    in_elem = SubElement(clip, "in")
    in_elem.text = str(source_in_frame)

    out_elem = SubElement(clip, "out")
    out_elem.text = str(source_out_frame)

    # 帧率
    _make_rate_elem(clip, fps)

    # 文件引用
    _make_file_elem(clip, material, fps)

    # 音量
    if seg.volume != 1.0:
        vol = SubElement(clip, "volume")
        SubElement(vol, "parameter", authoringApp="FCP").text = f"{seg.volume * 100:.1f}"

    # 变换 (位置/缩放/旋转/透明度)
    if seg.rotation != 0.0 or seg.pos_x != 0.0 or seg.pos_y != 0.0 or seg.scale_x != 1.0 or seg.scale_y != 1.0 or seg.alpha != 1.0:
        _build_transform_filter(clip, seg)

    return clip


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
        # FCP7 使用百分比
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
    """生成 FCP7 XML (xmeml) 文件"""
    # 根节点
    xmeml = Element("xmeml", version="5")

    # sequence
    seq = SubElement(xmeml, "sequence")
    name_elem = SubElement(seq, "name")
    name_elem.text = timeline.name

    total_frames = us_to_frames(timeline.duration_us, timeline.fps)
    dur_elem = SubElement(seq, "duration")
    dur_elem.text = str(max(total_frames, 1))

    # rate
    rate = SubElement(seq, "rate")
    tb = SubElement(rate, "timebase")
    tb.text = str(int(timeline.fps))
    ntsc = SubElement(rate, "ntsc")
    ntsc.text = "FALSE"

    # media
    media = SubElement(seq, "media")

    # ── 视频部分 ──
    video = SubElement(media, "video")

    # format
    fmt = SubElement(video, "format")
    sc = SubElement(fmt, "samplecharacteristics")
    w = SubElement(sc, "width")
    w.text = str(timeline.width)
    h = SubElement(sc, "height")
    h.text = str(timeline.height)
    par = SubElement(sc, "pixelaspectratio")
    par.text = "square"
    # 逐行扫描
    af = SubElement(sc, "anamorphic")
    af.text = "FALSE"
    fr = SubElement(sc, "fielddominance")
    fr.text = "none"

    # 按 render_index 排序视频轨道
    video_tracks = [t for t in timeline.tracks if t.track_type == "video"]
    video_tracks.sort(key=lambda t: t.render_index)

    for vtrack in video_tracks:
        xm_track = SubElement(video, "track")
        # 将第一个视频轨道设为 V1 通道
        for seg in vtrack.segments:
            material = timeline.materials.get(seg.material_id)
            if material is None:
                print(f"  ⚠ 跳过找不到素材的片段: {seg.segment_id}", file=sys.stderr)
                continue
            clipitem = _build_clipitem(seg, material, timeline.fps)
            xm_track.append(clipitem)

    # ── 音频部分 ──
    audio = SubElement(media, "audio")

    audio_tracks = [t for t in timeline.tracks if t.track_type == "audio"]
    audio_tracks.sort(key=lambda t: t.render_index)

    for atrack in audio_tracks:
        # 为每个音频轨道添加一个空的占位轨道头
        xm_track = SubElement(audio, "track")
        for seg in atrack.segments:
            material = timeline.materials.get(seg.material_id)
            if material is None:
                print(f"  ⚠ 跳过找不到素材的片段: {seg.segment_id}", file=sys.stderr)
                continue
            clipitem = _build_clipitem(seg, material, timeline.fps)
            xm_track.append(clipitem)

    # 如果没有任何音频轨道，添加一个空的占位（达芬奇可能需要）
    if not audio_tracks:
        SubElement(audio, "track")

    # 美化输出
    raw_xml = tostring(xmeml, encoding="unicode")
    dom = minidom.parseString(raw_xml)
    pretty_bytes = dom.toprettyxml(indent="  ", encoding="utf-8")
    pretty_str = pretty_bytes.decode("utf-8")

    # 去掉 minidom 自动添加的 XML 声明，手动写入含 DOCTYPE 的头
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
            "time_unit_warning": "时间值从原始数据转换而来，单位已统一为微秒。如发现时间对不上，请检查源文件的时间单位。",
            "unsupported": "文本轨道、贴纸轨道、特效轨道、转场等暂未在 XML 中完整支持，但数据已在 tracks 中完整保留。",
            "encrypted_hint": "剪映 6.x+ 版本加密了 draft_content.json，如遇 JSON 解析错误则不支持。",
            "resolve_import": "在达芬奇中使用 File → Import Timeline → Import AAF, EDL, XML... 导入 .xml 文件。",
        },
    }

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)


# ── 主入口 ────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description=f"剪映草稿 → FCP7 XML 转换器 v{VERSION}\n"
                    "将剪映/CapCut 工程文件转换为达芬奇可导入的 XML 格式。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
    python jianying_to_xml.py "C:/Users/me/AppData/Local/JianyingPro/User Data/Projects/compositon/xxx"
    python jianying_to_xml.py ./my_draft/draft_content.json -o ./output
    python jianying_to_xml.py ./my_draft --json-only   # 仅输出 JSON，不生成 XML
        """,
    )
    parser.add_argument("draft_path", help="草稿文件夹路径或 draft_content.json 文件路径")
    parser.add_argument("-o", "--output", default=None, help="输出目录 (默认: 草稿所在目录)")
    parser.add_argument("--json-only", action="store_true", help="仅输出 Timeline JSON，不生成 XML")
    parser.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    args = parser.parse_args()

    # 确定输出目录
    draft_path = Path(args.draft_path)
    if args.output:
        output_dir = Path(args.output)
    elif draft_path.is_dir():
        output_dir = draft_path
    else:
        output_dir = draft_path.parent
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"📄 读取草稿: {draft_path}")
    try:
        raw, draft_dir = load_draft(args.draft_path)
    except FileNotFoundError as e:
        print(f"❌ {e}", file=sys.stderr)
        sys.exit(1)
    except ValueError as e:
        print(f"❌ {e}", file=sys.stderr)
        sys.exit(1)

    print("🔍 解析 Timeline 数据...")
    try:
        timeline = build_timeline(raw, draft_dir)
    except Exception as e:
        print(f"❌ 解析失败: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)

    # 统计信息
    video_tracks = [t for t in timeline.tracks if t.track_type == "video"]
    audio_tracks = [t for t in timeline.tracks if t.track_type == "audio"]
    other_tracks = [t for t in timeline.tracks if t.track_type not in ("video", "audio")]
    total_clips = sum(len(t.segments) for t in timeline.tracks)
    total_materials = len(timeline.materials)

    print(f"  ✓ 画布: {timeline.width}x{timeline.height} @ {timeline.fps}fps")
    print(f"  ✓ 总时长: {timeline.duration_us / MICROSECOND:.1f} 秒 ({us_to_frames(timeline.duration_us, timeline.fps)} 帧)")
    print(f"  ✓ 轨道: {len(video_tracks)} 视频, {len(audio_tracks)} 音频, {len(other_tracks)} 其他 ({len(timeline.tracks)} 总)")
    print(f"  ✓ 片段: {total_clips} 个")
    print(f"  ✓ 素材: {total_materials} 个")

    if other_tracks:
        print(f"  ℹ 未转换的轨道类型: {set(t.track_type for t in other_tracks)} (数据保留在 JSON 中)")

    # 输出文件名
    safe_name = sanitize_filename(timeline.name)
    json_path = output_dir / f"{safe_name}_timeline.json"
    xml_path = output_dir / f"{safe_name}.xml"

    # 导出 JSON
    print(f"\n📝 导出 Timeline JSON: {json_path}")
    generate_timeline_json(timeline, str(json_path))

    # 导出 XML
    if not args.json_only:
        print(f"🎬 导出 FCP7 XML: {xml_path}")
        generate_xml(timeline, str(xml_path))

    print("\n✅ 完成!")
    if not args.json_only:
        print(f"   在达芬奇中: File → Import Timeline → Import AAF, EDL, XML...")
        print(f"   选择文件: {xml_path}")
    print(f"   Timeline 数据: {json_path}")


if __name__ == "__main__":
    main()
