# jianying_assistant 解密方案研究

## 架构总览

```
jianying_assistant/
├── jianying_automation.exe    # Flutter GUI 应用 (Dart + Go)
├── plugins/
│   ├── plugin-core.exe        # Go 核心 CLI (10.8MB) ← 关键!
│   ├── sdk.py                 # Python SDK (WebSocket 通信)
│   ├── utils.py               # 工具函数 (含 async_call_core)
│   ├── plugin_host.py         # 插件宿主
│   └── draft_downgrader/      # 草稿降级插件
│       ├── core.py            # 调用 plugin-core.exe
│       ├── main.py            # UI 逻辑
│       └── cli.py             # CLI 入口
└── data/
    ├── app.so                 # Flutter 编译产物
    └── flutter_assets/        # Flutter 资源
```

## 核心发现: plugin-core.exe

### 直接 CLI 命令 (不需要 plugin 模式)

```
plugin-core draft   create | delete | info | update | validate
plugin-core track   create | delete | get | list | update
plugin-core segment add | delete | list | move | split | update
plugin-core material add | delete | get | list
plugin-core effect  add | delete | list | update
plugin-core filter  add | delete | list | update
plugin-core transition add | delete | list | update
plugin-core text    add | delete | list | update
plugin-core subtitle import
plugin-core keyframe ...
plugin-core mask    ...
```

### Plugin 模式调用

```bash
plugin-core plugin --input <tempfile.json>
```

输入 JSON 格式:
```json
{
  "plugin_id": "draft_downgrader",
  "params": {
    "action": "get_version",
    "draft_dir": "C:/path/to/draft"
  }
}
```

输出 JSON 格式:
```json
{
  "ok": true,
  "data": { "current_version": "157.0.0" },
  "meta": {
    "action": "plugin:draft_downgrader",
    "executed_at": "2026-06-02T04:07:47Z"
  }
}
```

## 解密机制分析

### 二进制特征 (strings 分析)

| 特征 | 匹配数 | 含义 |
|------|--------|------|
| `aes` | 46 | AES 加密算法 |
| `gcm` + `NewGCM` | 73 | **AES-GCM 模式** (确认!) |
| `cipher` | 45 | 密码学操作 |
| `Decrypt` / `Encrypt` | 19 / 39 | 加解密函数 |
| `NewCBCDecrypter` | 1 | CBC 模式也存在 (可能用于其他用途) |
| `draft_content.json` | 7 | 草稿文件操作 |
| `base64` / `BytesBase64` | 12 | Base64 编解码 |
| `DecryptTicket` / `EncryptTicket` | - | 票据加解密 (密钥管理?) |

### 解密流程 (推测)

```
draft_content.json (Base64 encoded)
    ↓ Base64 decode
    ↓ AES-GCM decrypt (key embedded in Go binary)
    ↓
plaintext JSON
```

与我们之前的研究一致:
- 文件内容 → Base64 解码 → AES-GCM 密文
- 密文结构: `[12字节 Nonce] + [密文] + [16字节 Auth Tag]`
- 密钥内嵌在 plugin-core.exe 中

### draft_downgrader 的调用链

```
Python (core.py)
  → async_call_core() (utils.py)
    → subprocess: plugin-core.exe plugin --input <tempfile>
      → Go 内部: 读取 draft_content.json
      → Go 内部: AES-GCM 解密 (透明处理)
      → Go 内部: 解析 JSON, 获取版本号
      → stdout: JSON 结果
    → Python: 解析 JSON 输出
```

**关键**: Go 核心对加密草稿的处理是**完全透明**的 — Python 端不需要知道草稿是否加密。

## 与当前方案的对比

| 方面 | 当前方案 (jianying_to_xml.py) | 新方案 (plugin-core.exe) |
|------|------------------------------|--------------------------|
| 读取方式 | 直接读 JSON 文件 | 调用 Go CLI |
| 加密草稿 | 回退 template.json | **直接解密** |
| 依赖 | 仅 Python 标准库 | 需要 plugin-core.exe |
| 覆盖率 | 仅明文 + 有备份的草稿 | **所有草稿** (含加密) |
| 转场数据 | 无法读取转场关联 | transition list 可读 |
| 文本数据 | 仅 JSON 中的原始字段 | text list 可读 |

## 集成方案

### 方案 A: 直接调用 plugin-core.exe (推荐)

在 `jianying_to_xml_v2.py` 的 `load_draft()` 中增加 `plugin-core.exe` 路径:

```python
def load_draft(path: str, plugin_core: str = None) -> tuple[dict, Path]:
    """加载草稿, 支持加密"""
    # 尝试 1: 直接读取 (明文)
    try:
        return _try_load_json(path), Path(path)
    except (json.JSONDecodeError, ValueError):
        pass

    # 尝试 2: template.json 备份
    try:
        return _try_fallback_files(path), Path(path)
    except FileNotFoundError:
        pass

    # 尝试 3: plugin-core.exe 解密
    if plugin_core:
        return _load_via_plugin_core(path, plugin_core)

    raise ValueError("无法读取草稿 (已加密且无 plugin-core.exe)")
```

### 方案 B: 仅在加密时回退到 plugin-core

保持现有逻辑不变，仅在所有 fallback 失败后提示用户指定 plugin-core.exe 路径。

### 实现: _load_via_plugin_core

```python
def _load_via_plugin_core(draft_dir: str, plugin_core: str) -> tuple[dict, Path]:
    """通过 plugin-core.exe 读取加密草稿"""
    import subprocess, tempfile

    # 使用 plugin 模式 + read_draft action
    # 或直接用 SDK 的 read_draft_file 接口

    # 方法 1: 通过 draft_downgrader 的 get_version 验证可读
    # 方法 2: 直接用 material list 获取数据
    # 方法 3: 新增一个 "read_content" action

    # 最简方案: 用 Python 调用 plugin-core 的 SDK 接口
    input_data = {
        "plugin_id": "draft_reader",  # 或用现有 plugin_id
        "params": {
            "action": "read_content",
            "draft_dir": draft_dir
        }
    }
    # ... subprocess 调用
```

### 注意事项

1. **plugin-core.exe 不支持 read_content action** — 需要通过 Flutter SDK 接口 (`ctx.read_draft_file`) 读取
2. **直接 CLI 命令** (`draft info`, `material list` 等) 可能也需要 draft_dir 参数，但 help 中没有显示
3. **plugin-core.exe 约 11MB** — 打包时需要考虑体积

## 可行的快速方案

**最短路径**: 直接调用 `plugin-core.exe` 的 CLI 命令 (非 plugin 模式):

```bash
plugin-core.exe draft info --input "<draft_dir>"
plugin-core.exe material list --input "<draft_dir>"
plugin-core.exe track list --input "<draft_dir>"
plugin-core.exe transition list --input "<draft_dir>"
plugin-core.exe text list --input "<draft_dir>"
```

**已验证可行** (sample_draft 测试通过):

```json
// draft info --input "D:/AssHole/sample_draft"
{
  "ok": true,
  "data": {
    "duration_us": 15000000,
    "fps": 30,
    "height": 1080,
    "is_encrypted": false,     // ← 自动检测加密状态!
    "segment_count": 6,
    "track_count": 5,
    "width": 1920
  }
}

// transition list --input "D:/AssHole/sample_draft"
{
  "ok": true,
  "data": [
    { "duration": 500000, "id": "trans-001", "name": "淡入淡出", "type": "transition" }
  ],
  "total": 1
}

// track list --input "D:/AssHole/sample_draft"
{
  "ok": true,
  "data": [
    { "index": 0, "type": "video", "segment_count": 2, "duration_us": 13000000 },
    { "index": 1, "type": "video", "segment_count": 1, "duration_us": 3000000 },
    { "index": 2, "type": "audio", "segment_count": 1, "duration_us": 15000000 },
    ...
  ]
}
```

## 建议

**短期 (立即可做)**:
- 在转换器中添加 `--plugin-core` 参数指向 `plugin-core.exe`
- `load_draft()` 的 fallback 链中加入: `draft info --input <dir>` 检测加密 → `material list` 获取素材 → `track list` 获取轨道
- 加密草稿: 直接通过 plugin-core 的 CLI 命令读取 (解密在 Go 内部透明完成)
- 无需 template.json fallback

**中期**:
- 用 `plugin-core.exe segment list --input <dir>` 获取完整片段数据
- 用 `plugin-core.exe transition list --input <dir>` 获取转场关联
- 用 `plugin-core.exe text list --input <dir>` 获取文本数据
- 将 plugin-core 输出映射到现有的 TimelineData 数据模型

**长期**:
- 从 Go 二进制中提取 AES 密钥 (strings/IDA)，实现纯 Python 解密
- 或将 plugin-core 的解密逻辑移植为独立 Python 模块

---

*研究日期: 2026-06-02*
*基于 jianying_assistant v2025.05.22 plugin-core.exe 二进制分析*
