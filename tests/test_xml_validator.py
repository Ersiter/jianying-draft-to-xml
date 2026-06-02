#!/usr/bin/env python3
"""
FCP7 XML 验证测试套件

测试 jianying_to_xml_v2.py 生成的 XML 是否符合 FCP7 规范和 DaVinci Resolve 要求。

用法:
    python tests/test_xml_validator.py
    python tests/test_xml_validator.py --xml path/to/output.xml   # 验证指定 XML
    python tests/test_xml_validator.py --verbose                   # 详细输出
"""

import json
import os
import sys
import tempfile
import argparse
from pathlib import Path
from xml.etree.ElementTree import parse as xml_parse, Element, fromstring

# 添加项目根目录到 path
SCRIPT_DIR = Path(__file__).parent
PROJECT_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(PROJECT_ROOT))


# ── 测试框架 (轻量级, 无第三方依赖) ──────────────────

class TestResult:
    def __init__(self, name: str, passed: bool, message: str = ""):
        self.name = name
        self.passed = passed
        self.message = message

    def __str__(self):
        status = "[PASS]" if self.passed else "[FAIL]"
        s = f"  {status}  {self.name}"
        if self.message and not self.passed:
            s += f"\n         -> {self.message}"
        return s


class Validator:
    """FCP7 XML Validator"""

    def __init__(self, xml_path: str, verbose: bool = False):
        self.xml_path = xml_path
        self.verbose = verbose
        self.results: list[TestResult] = []
        self.tree = None
        self.root = None

    def load(self) -> bool:
        """Load XML file"""
        try:
            self.tree = xml_parse(self.xml_path)
            self.root = self.tree.getroot()
            return True
        except Exception as e:
            self.results.append(TestResult("XML parseable", False, str(e)))
            return False

    def run_all(self) -> list[TestResult]:
        """Run all validations"""
        if not self.load():
            return self.results

        self._test_root_structure()
        self._test_xmeml_version()
        self._test_sequence_required_elements()
        self._test_rate_ntsc()
        self._test_media_order()
        self._test_video_format_tag()
        self._test_audio_format_tag()
        self._test_clipitem_fields()
        self._test_clipitem_duration_semantics()
        self._test_clipitem_sourcetrack()
        self._test_clipitem_rate()
        self._test_file_elements()
        self._test_file_media_details()
        self._test_link_elements()
        self._test_masterclipid()
        self._test_element_ordering()
        self._test_pathurl_format()
        self._test_no_empty_tracks()
        return self.results

    def _ok(self, name: str, msg: str = ""):
        self.results.append(TestResult(name, True, msg))
        if self.verbose:
            print(f"    [OK] {name}")

    def _fail(self, name: str, msg: str):
        self.results.append(TestResult(name, False, msg))
        print(f"    [!!] {name}: {msg}")

    # ── Test Cases ──────────────────────────────────────

    def _test_root_structure(self):
        """Root element must be xmeml"""
        if self.root.tag == "xmeml":
            self._ok("Root is <xmeml>")
        else:
            self._fail("Root is <xmeml>", f"Found <{self.root.tag}>")

    def _test_xmeml_version(self):
        """xmeml version attribute must be 4 or 5"""
        ver = self.root.get("version")
        if ver in ("4", "5"):
            self._ok(f"xmeml version=\"{ver}\"")
        else:
            self._fail("xmeml version", f"Expected 4 or 5, got: {ver}")

    def _test_sequence_required_elements(self):
        """sequence must contain name, duration, rate, media"""
        seq = self.root.find("sequence")
        if seq is None:
            self._fail("sequence exists", "Not found <sequence>")
            return

        for tag in ("name", "duration", "rate", "media"):
            elem = seq.find(tag)
            if elem is not None:
                self._ok(f"sequence/{tag} exists")
            else:
                self._fail(f"sequence/{tag} exists", "Missing")

    def _test_rate_ntsc(self):
        """All rate elements must contain ntsc and timebase"""
        rates = self.root.iter("rate")
        missing_ntsc = []
        missing_tb = []
        for i, rate in enumerate(rates):
            if rate.find("ntsc") is None:
                missing_ntsc.append(i)
            if rate.find("timebase") is None:
                missing_tb.append(i)

        if not missing_ntsc:
            self._ok("All <rate> have <ntsc>")
        else:
            self._fail("All <rate> have <ntsc>", f"Indices {missing_ntsc} missing")

        if not missing_tb:
            self._ok("All <rate> have <timebase>")
        else:
            self._fail("All <rate> have <timebase>", f"Indices {missing_tb} missing")

        # Validate ntsc values
        for rate in self.root.iter("rate"):
            ntsc = rate.find("ntsc")
            if ntsc is not None and ntsc.text not in ("TRUE", "FALSE"):
                self._fail("ntsc value valid", f"Expected TRUE/FALSE, got: {ntsc.text}")
                return
        self._ok("All ntsc values are TRUE/FALSE")

    def _test_media_order(self):
        """media: video must come before audio"""
        media = self.root.find(".//media")
        if media is None:
            self._fail("media/video/audio order", "Not found <media>")
            return

        children = list(media)
        tags = [c.tag for c in children]
        video_idx = next((i for i, t in enumerate(tags) if t == "video"), -1)
        audio_idx = next((i for i, t in enumerate(tags) if t == "audio"), -1)

        if video_idx >= 0 and audio_idx >= 0 and video_idx < audio_idx:
            self._ok("media: video before audio")
        elif video_idx >= 0 and audio_idx == -1:
            self._ok("media: only video (no audio)")
        else:
            self._fail("media: video before audio", f"Order: {tags}")

    def _test_video_format_tag(self):
        """video (main track section) must have format tag (DaVinci requirement)"""
        # Find the main video section under sequence/media/video, not file/media/video
        media = self.root.find(".//sequence/media")
        if media is None:
            media = self.root.find(".//media")
        if media is None:
            self._fail("video/format exists", "Not found <media>")
            return

        video = media.find("video")
        if video is None:
            self._fail("video/format exists", "Not found <video>")
            return

        fmt = video.find("format")
        if fmt is not None:
            self._ok("video/<format> exists (DaVinci requirement)")
        else:
            self._fail("video/<format> exists", "Missing - DaVinci may import empty timeline")

    def _test_audio_format_tag(self):
        """audio (main track section) should have format tag"""
        # Find the main audio section under sequence/media/audio
        media = self.root.find(".//sequence/media")
        if media is None:
            media = self.root.find(".//media")
        if media is None:
            return

        audio = media.find("audio")
        if audio is None:
            return  # No audio section, skip

        fmt = audio.find("format")
        if fmt is not None:
            self._ok("audio/<format> exists")
        else:
            self._fail("audio/<format> exists", "Missing - suggest adding samplecharacteristics")

    def _test_clipitem_fields(self):
        """Each clipitem must have name, duration, rate, start, end, in, out, file"""
        required = ["name", "duration", "rate", "start", "end", "in", "out"]
        clipitems = list(self.root.iter("clipitem"))

        if not clipitems:
            self._ok("clipitem required fields (no clipitem)")
            return

        all_ok = True
        for ci in clipitems:
            cid = ci.get("id", "?")
            for tag in required:
                if ci.find(tag) is None:
                    self._fail(f"clipitem '{cid}' has {tag}", "Missing")
                    all_ok = False

            # file can be full or reference
            file_elem = ci.find("file")
            if file_elem is None:
                self._fail(f"clipitem '{cid}' has file", "Missing")
                all_ok = False

        if all_ok:
            self._ok(f"All clipitem have required fields ({len(clipitems)} items)")

    def _test_clipitem_duration_semantics(self):
        """clipitem duration should be source media total duration (>= end - start)"""
        clipitems = list(self.root.iter("clipitem"))
        if not clipitems:
            self._ok("duration semantics (no clipitem)")
            return

        issues = []
        for ci in clipitems:
            cid = ci.get("id", "?")
            dur = ci.find("duration")
            start = ci.find("start")
            end = ci.find("end")
            if dur is None or start is None or end is None:
                continue

            try:
                dur_val = int(dur.text)
                start_val = int(start.text)
                end_val = int(end.text)

                # FCP7 uses -1 for computed start/end in transitions
                if start_val == -1 or end_val == -1:
                    continue  # Skip transition-affected clips

                clip_dur = end_val - start_val

                if clip_dur > 0 and dur_val < clip_dur:
                    issues.append(f"{cid}: duration={dur_val} < end-start={clip_dur}")
            except (ValueError, TypeError):
                pass

        if not issues:
            self._ok("clipitem duration >= end-start (source media total)")
        else:
            self._fail("clipitem duration semantics", "; ".join(issues[:3]))

    def _test_clipitem_sourcetrack(self):
        """Each clipitem should have sourcetrack"""
        clipitems = list(self.root.iter("clipitem"))
        if not clipitems:
            self._ok("sourcetrack (no clipitem)")
            return

        missing = []
        for ci in clipitems:
            cid = ci.get("id", "?")
            if ci.find("sourcetrack") is None:
                missing.append(cid)

        if not missing:
            self._ok(f"All clipitem have <sourcetrack> ({len(clipitems)} items)")
        else:
            self._fail("clipitem has <sourcetrack>", f"Missing: {', '.join(missing[:5])}")

    def _test_clipitem_rate(self):
        """Each clipitem should have rate (ntsc + timebase)"""
        clipitems = list(self.root.iter("clipitem"))
        if not clipitems:
            self._ok("clipitem rate (no clipitem)")
            return

        missing = []
        for ci in clipitems:
            cid = ci.get("id", "?")
            rate = ci.find("rate")
            if rate is None:
                missing.append(cid)
            elif rate.find("ntsc") is None or rate.find("timebase") is None:
                missing.append(cid)

        if not missing:
            self._ok(f"所有 clipitem 含完整 <rate> ({len(clipitems)} 个)")
        else:
            self._fail("clipitem has full <rate>", f"Missing: {', '.join(missing[:5])}")

    def _test_file_elements(self):
        """file element must have id attribute"""
        files = list(self.root.iter("file"))
        if not files:
            self._ok("file id attribute (no file)")
            return

        missing = []
        for f in files:
            if f.get("id") is None:
                missing.append("file")

        if not missing:
            self._ok(f"All <file> have id attribute ({len(files)} items)")
        else:
            self._fail("file has id attribute", f"{len(missing)} missing")

    def _test_file_media_details(self):
        """At least one full file definition should have media/video/samplecharacteristics"""
        files = list(self.root.iter("file"))

        has_detail = False
        for f in files:
            media = f.find("media")
            if media is not None:
                video = media.find("video")
                if video is not None:
                    sc = video.find("samplecharacteristics")
                    if sc is not None:
                        has_detail = True
                        break

        if has_detail:
            self._ok("At least one <file> has media/video/samplecharacteristics")
        else:
            self._fail("<file> media details", "No file has media/video/samplecharacteristics")

    def _test_link_elements(self):
        """If the same material_id appears in both video and audio tracks, link elements are required"""
        # Collect material_ids from video and audio clipitems
        video_mids = set()
        audio_mids = set()

        media = self.root.find(".//sequence/media")
        if media is None:
            media = self.root.find(".//media")
        if media is None:
            self._ok("link elements (no media)")
            return

        video_elem = media.find("video")
        if video_elem is not None:
            for track in video_elem.findall("track"):
                for ci in track.findall("clipitem"):
                    file_elem = ci.find("file")
                    if file_elem is not None:
                        fid = file_elem.get("id", "")
                        video_mids.add(fid)

        audio_elem = media.find("audio")
        if audio_elem is not None:
            for track in audio_elem.findall("track"):
                for ci in track.findall("clipitem"):
                    file_elem = ci.find("file")
                    if file_elem is not None:
                        fid = file_elem.get("id", "")
                        audio_mids.add(fid)

        shared_mids = video_mids & audio_mids

        if not shared_mids:
            # No material appears in both video and audio tracks - no linking needed
            self._ok("link elements (no shared material between video/audio tracks)")
            return

        # Some materials appear in both - check for link elements
        all_clips = []
        if video_elem is not None:
            for track in video_elem.findall("track"):
                all_clips.extend(track.findall("clipitem"))
        if audio_elem is not None:
            for track in audio_elem.findall("track"):
                all_clips.extend(track.findall("clipitem"))

        clips_with_links = sum(1 for ci in all_clips if ci.find("link") is not None)
        if clips_with_links > 0:
            self._ok(f"link elements exist ({clips_with_links} clipitems have link)")
        else:
            self._fail("link elements", f"Shared materials {shared_mids} in both tracks but no links")

    def _test_masterclipid(self):
        """clipitem should have masterclipid"""
        clipitems = list(self.root.iter("clipitem"))
        if not clipitems:
            self._ok("masterclipid (no clipitem)")
            return

        missing = [ci.get("id", "?") for ci in clipitems if ci.find("masterclipid") is None]

        if not missing:
            self._ok(f"All clipitem have <masterclipid> ({len(clipitems)} items)")
        else:
            self._fail("clipitem has <masterclipid>", f"Missing: {', '.join(missing[:5])}")

    def _test_element_ordering(self):
        """clipitem elements should follow FCP7 ordering"""
        expected_order = ["name", "masterclipid", "duration", "rate", "start", "end", "in", "out",
                          "file", "sourcetrack", "link"]

        clipitems = list(self.root.iter("clipitem"))
        if not clipitems:
            self._ok("Element ordering (no clipitem)")
            return

        order_ok = True
        for ci in clipitems[:3]:  # Check first 3
            cid = ci.get("id", "?")
            tags = [c.tag for c in ci]
            present = [(t, expected_order.index(t)) for t in tags if t in expected_order]
            for i in range(len(present) - 1):
                if present[i][1] > present[i + 1][1]:
                    self._fail("Element ordering", f"clipitem '{cid}': {present[i][0]} should not be after {present[i+1][0]}")
                    order_ok = False
                    break
            if not order_ok:
                break

        if order_ok:
            self._ok("clipitem element ordering follows FCP7 spec")

    def _test_pathurl_format(self):
        """pathurl should use file:/// format"""
        pathurls = list(self.root.iter("pathurl"))
        if not pathurls:
            self._ok("pathurl format (no pathurl)")
            return

        bad = []
        for pu in pathurls:
            url = pu.text or ""
            if url and not url.startswith("file://"):
                bad.append(url[:50])

        if not bad:
            self._ok(f"pathurl format correct (file://) ({len(pathurls)} items)")
        else:
            self._fail("pathurl format", f"Not file:// format: {', '.join(bad[:3])}")

    def _test_no_empty_tracks(self):
        """Should not have completely empty tracks (no child elements)"""
        all_tracks = list(self.root.iter("track"))
        empty_tracks = []
        for t in all_tracks:
            if len(list(t)) == 0:
                empty_tracks.append(t)

        if not empty_tracks:
            self._ok(f"No empty tracks ({len(all_tracks)} tracks)")
        else:
            self._fail("Empty tracks", f"{len(empty_tracks)}/{len(all_tracks)} tracks have no children")


# ── 转换 + 验证集成测试 ──────────────────────────────

def test_with_sample_draft(verbose: bool = False) -> bool:
    """Use sample_draft for full convert+validate integration test"""
    print("\n=== Integration Test: sample_draft -> XML -> Validate ===\n")

    sample_dir = PROJECT_ROOT / "sample_draft"
    if not (sample_dir / "draft_content.json").exists():
        print("  ⚠ sample_draft/draft_content.json 不存在, 跳过集成测试")
        return True

    # 导入转换器
    try:
        from jianying_to_xml_v2 import build_timeline, generate_xml, generate_timeline_json
    except ImportError:
        print("  ⚠ 无法导入 jianying_to_xml_v2, 跳过集成测试")
        return True

    # 转换
    import tempfile
    with tempfile.TemporaryDirectory() as tmpdir:
        xml_path = os.path.join(tmpdir, "test_output.xml")
        json_path = os.path.join(tmpdir, "test_output.json")

        print("  [1] Load sample_draft...")
        with open(sample_dir / "draft_content.json", "r", encoding="utf-8") as f:
            raw = json.load(f)

        print("  [2] Build Timeline...")
        timeline = build_timeline(raw, sample_dir)

        print(f"      {len(timeline.tracks)} tracks, {len(timeline.materials)} materials")
        print(f"      fps={timeline.fps}, ntsc={('TRUE' if __import__('jianying_to_xml_v2', fromlist=['is_ntsc_fps']).is_ntsc_fps(timeline.fps) else 'FALSE')}")

        print("  [3] Generate XML...")
        generate_xml(timeline, xml_path)
        print("  [4] Generate JSON...")
        generate_timeline_json(timeline, json_path)

        # Check file size
        xml_size = os.path.getsize(xml_path)
        json_size = os.path.getsize(json_path)
        print(f"      XML: {xml_size:,} bytes, JSON: {json_size:,} bytes")

        print("  [5] Validate XML...")
        validator = Validator(xml_path, verbose=verbose)
        results = validator.run_all()

        # Output results
        passed = sum(1 for r in results if r.passed)
        failed = sum(1 for r in results if not r.passed)
        total = len(results)

        print(f"\n  --- Result: {passed}/{total} passed ---\n")
        for r in results:
            print(str(r))

        if failed > 0:
            print(f"\n  FAIL: {failed} items not passed")
        else:
            print(f"\n  ALL PASSED!")

        return failed == 0


# ── 主入口 ────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="FCP7 XML Validation Test Suite")
    parser.add_argument("--xml", type=str, default=None, help="Validate a specific XML file")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")
    args = parser.parse_args()

    all_ok = True

    if args.xml:
        # Validate specific XML
        print(f"\n=== Validate: {args.xml} ===\n")
        validator = Validator(args.xml, verbose=args.verbose)
        results = validator.run_all()

        passed = sum(1 for r in results if r.passed)
        failed = sum(1 for r in results if not r.passed)
        total = len(results)

        print(f"\n  --- Result: {passed}/{total} passed ---\n")
        for r in results:
            print(str(r))

        if failed > 0:
            print(f"\n  FAIL: {failed} items not passed")
            all_ok = False
        else:
            print(f"\n  ALL PASSED!")
    else:
        # Run integration test
        all_ok = test_with_sample_draft(verbose=args.verbose)

    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
