# DaVinci Resolve FCP7 XML 兼容性研究报告

## 概述

本文档记录将剪映/CapCut 草稿转换为 FCP7 XML 并导入 DaVinci Resolve 的兼容性要求和已知问题。
基于 OpenTimelineIO FCP XML 适配器源码分析、FCP7 XML 规范研究和社区实践整理。

---

## 1. XMEML 版本

| 版本 | 来源 | DaVinci 支持 |
|------|------|:------------:|
| v3   | FCP 4.x/5.x | ✓ |
| v4   | FCP 6.x (Final Cut Studio 2) | ✓ |
| v5   | FCP 7.x (Final Cut Studio 3) | ✓ |

**结论**: DaVinci Resolve 17/18/19 均支持 v5。Premiere Pro 导出的 FCP7 XML 也是 v5。
保持 `version="5"` 即可。

---

## 2. DaVinci Resolve 强制要求 (Critical)

### 2.1 `<format>` 标签

```xml
<video>
  <format/>  <!-- 即使为空也必须存在 -->
  <track>...</track>
</video>
```
> OTIO 源码注释: *"This is a fix for Davinci Resolve. After the 'video' tag, it expects a `<format>` tag, even if empty."*

### 2.2 媒体顺序

`<media>` 内必须 **video 在前, audio 在后**:
```xml
<media>
  <video>...</video>   <!-- 必须在前 -->
  <audio>...</audio>   <!-- 必须在后 -->
</media>
```

### 2.3 `<rate>` 必须包含 `<ntsc>` 和 `<timebase>`

```xml
<rate>
  <ntsc>FALSE</ntsc>      <!-- 缺失会导致导入失败 -->
  <timebase>30</timebase>  <!-- 必须是整数 -->
</rate>
```

**NTSC 判断规则** (ntsc 不代表"NTSC制式", 而代表非整数帧率):
| 实际帧率 | ntsc | timebase |
|---------|------|----------|
| 23.976  | TRUE | 24 |
| 24.000  | FALSE | 24 |
| 25.000  | FALSE | 25 |
| 29.970  | TRUE | 30 |
| 30.000  | FALSE | 30 |
| 59.940  | TRUE | 60 |
| 60.000  | FALSE | 60 |

公式: `ntsc_rate = timebase × 1000 / 1001`

**必须在以下位置都设置 rate**: sequence 级、每个 clipitem 级、每个 file 级。

### 2.4 `<pathurl>` 格式

使用 RFC 2396 file URI:
```xml
<pathurl>file:///C:/Users/path/to/media.mp4</pathurl>
```
- Windows: `file:///C:/path/to/file.mp4` (Python `Path.as_uri()` 格式正确)
- macOS/Linux: `file:///home/user/media/clip.mov`
- 空格编码为 `%20`

---

## 3. clipitem 元素规范

### 3.1 核心语义

```xml
<clipitem id="clipitem-1">
  <name>Clip Name</name>
  <duration>500</duration>      <!-- 源素材总时长 (帧) -->
  <rate>...</rate>
  <start>100</start>            <!-- 时间线上起始位置 (帧) -->
  <end>400</end>                <!-- 时间线上结束位置 (帧) -->
  <in>50</in>                   <!-- 源素材入点 (帧) -->
  <out>350</out>                <!-- 源素材出点 (帧) -->
  <file id="file-1">...</file>
  <sourcetrack>...</sourcetrack>
  <link>...</link>
</clipitem>
```

| 元素 | 含义 | 公式 |
|------|------|------|
| `start` | 时间线上的起始帧 | `target_start` 转帧 |
| `end` | 时间线上的结束帧 | `start + (out - in)` |
| `in` | 源素材入点帧 | `source_start` 转帧 |
| `out` | 源素材出点帧 | `in + source_duration_frames` |
| `duration` | **源素材总时长** (非片段时长!) | 完整素材长度转帧 |

**关键约束**: `end - start` 必须等于 `out - in`。

**特殊值**: `<in>-1</in>` 和 `<out>-1</out>` 表示"无裁剪，使用全部源素材"。

### 3.2 `<sourcetrack>` 标签

每个 clipitem 必须有 `<sourcetrack>` 指明媒体类型:
```xml
<sourcetrack>
  <mediatype>video</mediatype>  <!-- 或 audio -->
  <trackindex>1</trackindex>
</sourcetrack>
```

### 3.3 `<link>` 链接标签

当视频和音频来自同一源素材时，必须通过 `<link>` 标签关联:

```xml
<!-- 视频 clipitem -->
<clipitem id="clipitem-v1">
  ...
  <sourcetrack><mediatype>video</mediatype><trackindex>1</trackindex></sourcetrack>
  <link>
    <linkclipref>clipitem-v1</linkclipref>
    <mediatype>video</mediatype>
    <trackindex>1</trackindex>
    <clipindex>1</clipindex>
  </link>
  <link>
    <linkclipref>clipitem-a1</linkclipref>
    <mediatype>audio</mediatype>
    <trackindex>1</trackindex>
    <clipindex>1</clipindex>
  </link>
</clipitem>

<!-- 音频 clipitem (同源) -->
<clipitem id="clipitem-a1">
  ...
  <file id="file-1"/>  <!-- 仅引用，不重复完整定义 -->
  <sourcetrack><mediatype>audio</mediatype><trackindex>1</trackindex></sourcetrack>
  <!-- 同样的 link 集合 -->
  <link>
    <linkclipref>clipitem-v1</linkclipref>
    <mediatype>video</mediatype>
    <trackindex>1</trackindex>
    <clipindex>1</clipindex>
  </link>
  <link>
    <linkclipref>clipitem-a1</linkclipref>
    <mediatype>audio</mediatype>
    <trackindex>1</trackindex>
    <clipindex>1</clipindex>
  </link>
</clipitem>
```

**链接规则**:
- 每个 link group 中的所有 clipitem 包含 **完全相同的 `<link>` 集合**
- `<linkclipref>` 引用目标 clipitem 的 `id` 属性
- 只有主 clipitem (通常视频) 持有完整 `<file>` 定义，其他用 `<file id="file-N"/>` 引用

### 3.4 `<masterclipid>` 标签

```xml
<masterclipid>masterclip-1</masterclipid>
```
同源素材的所有 clipitem 共享同一个 masterclipid，DaVinci 用此进行素材分组。

---

## 4. `<file>` 元素完整结构

```xml
<file id="file-1">
  <name>media.mp4</name>
  <pathurl>file:///C:/media.mp4</pathurl>
  <rate>
    <ntsc>FALSE</ntsc>
    <timebase>30</timebase>
  </rate>
  <duration>9000</duration>  <!-- 源素材总帧数 -->
  <media>
    <video>
      <samplecharacteristics>
        <width>1920</width>
        <height>1080</height>
      </samplecharacteristics>
    </video>
    <audio>
      <channelcount>2</channelcount>
      <samplecharacteristics>
        <samplerate>48000</samplerate>
        <size>16-bit</size>
      </samplecharacteristics>
    </audio>
  </media>
</file>
```

---

## 5. 音频轨道 `<format>` 结构

```xml
<audio>
  <format>
    <samplecharacteristics>
      <samplerate>48000</samplerate>
      <size>16-bit</size>
      <channelcount>2</channelcount>
    </samplecharacteristics>
  </format>
  <track>...</track>
</audio>
```

---

## 6. 元素顺序要求

FCP7 XML 对子元素顺序严格。clipitem 内正确顺序:

```
name → masterclipid → duration → rate → start → end → in → out
→ file → sourcetrack → link → filter → ...
```

---

## 7. 转场 (`<transitionitem>`) 支持

### 7.1 结构

转场放在 `<track>` 内两个 `<clipitem>` 之间:

```xml
<track>
  <clipitem id="clipitem-1">
    ...
    <end>-1</end>       <!-- 由转场计算, 设为 -1 -->
    <out>165</out>       <!-- 原始 150 + 15 帧溶解尾部 -->
  </clipitem>

  <transitionitem>
    <rate><ntsc>FALSE</ntsc><timebase>30</timebase></rate>
    <start>0</start>
    <end>30</end>
    <alignment>center</alignment>
    <effect>
      <name>Cross Dissolve</name>
      <effectid>Cross Dissolve</effectid>
      <effectcategory>Dissolves</effectcategory>
      <effecttype>transition</effecttype>
      <mediatype>video</mediatype>
    </effect>
  </transitionitem>

  <clipitem id="clipitem-2">
    ...
    <start>-1</start>    <!-- 由转场计算, 设为 -1 -->
    <in>-15</in>         <!-- 原始 0 - 15 帧溶解头部 -->
  </clipitem>
</track>
```

### 7.2 Clip Overlap 模型

转场需要相邻片段**扩展 in/out 点**来提供过渡帧:

```
时间线:  |--Clip A--|==TRANSITION==|--Clip B--|
                     ^^^^^^^^^^^^^^^^
                    重叠区域

Clip A: out 延长 (提供尾部帧)
Clip B: in  前移 (提供头部帧)
```

- `alignment=center`: 各提供一半帧数 (最常用)
- `alignment=start`: 全部来自 Clip B 头部
- `alignment=end`: 全部来自 Clip A 尾部
- `alignment=startblack`: 从黑场淡入 (片头)
- `alignment=endblack`: 淡出到黑场 (片尾)

### 7.3 DaVinci 支持的转场类型

| 转场 | effectid | DaVinci |
|------|----------|:-------:|
| Cross Dissolve | `Cross Dissolve` | ✓ 可靠 |
| Dip to Black | `Dip to Black` | ✓ 可靠 |
| Dip to White | `Dip to White` | ✓ 可靠 |
| Additive Dissolve | `Additive Dissolve` | △ 部分 |
| Wipe / Page Peel / Iris | 各种 | ✗ 忽略 |

**建议**: 全部映射为 `Cross Dissolve`，淡入淡出映射为 `Dip to Black`。

### 7.4 剪映转场映射

```python
# 剪映转场名 → FCP7 effectid
TRANSITION_MAP = {
    "淡入淡出": "Cross Dissolve",  # 默认
    "淡入":     "Dip to Black",    # startblack
    "淡出":     "Dip to Black",    # endblack
    "叠化":     "Cross Dissolve",
    "黑场":     "Dip to Black",
    "白场":     "Dip to White",
    # 其他全部 → Cross Dissolve
}
```

---

## 8. DaVinci 不支持的元素

| 元素 | 说明 | 替代方案 |
|------|------|---------|
| `<generatoritem>` | FCP7 文字/标题生成器 | DaVinci 导入为空白/黑色，无法使用 |
| FCP7 特效参数 | FCP7 专有效果 | 不可转移 |
| FCP7 色彩校正 | FCP7 色彩参数 | 不可转移 |
| 嵌套序列 | Nested Sequences | 经常展平或失败 |
| 标记/备注 | Markers/Notes | 丢失 |
| 速度渐变 | Speed Ramps | 展平为平均速度 |

---

## 8. 常见导入失败原因

| 现象 | 原因 | 修复 |
|------|------|------|
| "Could not be opened" | XML 格式错误、编码错误、版本不对 | 检查 UTF-8 编码、版本号 |
| 空时间线 | 缺少 `<format/>` 标签 | 在 `<video>` 后添加空 `<format/>` |
| 所有素材离线 | pathurl 指向不存在的文件 | 使用绝对 file:/// URI |
| 帧率不匹配 | rate 不一致 | sequence 和所有 clipitem rate 统一 |
| 音视频不同步 | NTSC 标志不匹配 (29.97 vs 30.0) | 正确设置 ntsc + timebase |
| 导入卡死/崩溃 | XML 过大或循环引用 | 避免自引用 |

---

## 9. DaVinci 版本兼容性

| 特性 | Resolve 17 | Resolve 18 | Resolve 19 |
|------|:----------:|:----------:|:----------:|
| FCP7 XML 导入 | ✓ (有 edge-case bug) | ✓ (改进) | ✓ (进一步改进) |
| 复合片段 | 有限支持 | 更好 | 最佳 |
| 速度变化 | 常丢失 | 改进 | 进一步改进 |
| 转场 | Cross Dissolve / Dip to Black / Dip to White 可靠 | 同上 | 同上 |
| 文字生成器 | ✗ 不导入 | ✗ 不导入 | ✗ 不导入 |

**目标基线**: Resolve 17+ 即可覆盖绝大多数用户。

---

## 10. 当前转换器 (jianying_to_xml.py) 问题清单

### 严重 (会导致导入失败)

| # | 问题 | 位置 | 修复方案 |
|---|------|------|---------|
| 1 | `_make_rate_elem` 缺少 `<ntsc>` 标签 | 全局 | NTSC 检测 + 所有 rate 加 ntsc |
| 2 | clipitem 的 `<duration>` 设为片段时长而非素材总时长 | `_build_clipitem` | 改用 material.duration |
| 3 | 缺少 `<sourcetrack>` 标签 | `_build_clipitem` | 添加 sourcetrack |
| 4 | 缺少 `<link>` 链接标签 | `generate_xml` | 同源素材视频/音频 link 关联 |
| 5 | `<file>` 缺少 media/video/audio 详情 | `_make_file_elem` | 添加 samplecharacteristics |
| 6 | 音频轨道缺少 `<format>` 结构 | `generate_xml` | 添加 audio format |

### 中等 (可能影响导入质量)

| # | 问题 | 位置 | 修复方案 |
|---|------|------|---------|
| 7 | 缺少 `<masterclipid>` | `_build_clipitem` | 添加 masterclipid |
| 8 | 元素顺序不符合 FCP7 规范 | `_build_clipitem` | 重排 SubElement 顺序 |
| 9 | 音量参数格式不规范 | `_build_clipitem` | 改用 name/value 子元素 |
| 10 | NTSC 帧率未检测 (29.97→30+ntsc=TRUE) | `generate_xml` | 添加 fps→ntsc 映射 |

---

## 11. 参考资源

- [OpenTimelineIO FCP XML Adapter](https://github.com/OpenTimelineIO/otio-fcp-adapter) — 权威的 FCP7 XML 读写实现
- [Blackmagic Design Forum](https://forum.blackmagicdesign.com) — DaVinci XML 导入社区经验
- [Intelligent Assistance 7toX/Xto7](https://intelligentassistance.com) — FCP7 XML 转换工具
- [whizzrd/xmemlviewer](https://github.com/whizzrd/xmemlviewer) — Web 版 XMEML 查看器

---

*研究日期: 2026-06-02*
*基于 DaVinci Resolve 17/18/19 和 OpenTimelineIO FCP Adapter 源码分析*

---

## 附录: v2.0 转换器测试结果

使用 `tests/test_xml_validator.py` 对 sample_draft 进行验证:

```
=== Integration Test: sample_draft -> XML -> Validate ===

23/23 passed:

[PASS]  Root is <xmeml>
[PASS]  xmeml version="5"
[PASS]  sequence/name exists
[PASS]  sequence/duration exists
[PASS]  sequence/rate exists
[PASS]  sequence/media exists
[PASS]  All <rate> have <ntsc>
[PASS]  All <rate> have <timebase>
[PASS]  All ntsc values are TRUE/FALSE
[PASS]  media: video before audio
[PASS]  video/<format> exists (DaVinci requirement)
[PASS]  audio/<format> exists
[PASS]  All clipitem have required fields (5 items)
[PASS]  clipitem duration >= end-start (source media total)
[PASS]  All clipitem have <sourcetrack> (5 items)
[PASS]  All clipitem have full <rate> (5 items)
[PASS]  All <file> have id attribute (5 items)
[PASS]  At least one <file> has media/video/samplecharacteristics
[PASS]  link elements (no shared material between video/audio tracks)
[PASS]  All clipitem have <masterclipid> (5 items)
[PASS]  clipitem element ordering follows FCP7 spec
[PASS]  pathurl format correct (file://) (5 items)
[PASS]  No empty tracks (4 tracks)

ALL PASSED!
```
