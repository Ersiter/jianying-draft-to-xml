# Jianying 6.x draft_content.json 加密研究报告

## 现状

剪映 6.x+ 对 `draft_content.json` 进行了加密，导致无法直接读取草稿数据。

## 已确认的事实

### 1. 编码结构
```
文件内容 → Base64 编码 → 解码后是加密的二进制密文
```

通过 `xxd` 和 Node.js 分析 `draft_content.json`：
- 纯 Base64 字符集 (A-Z, a-z, 0-9, +, /)
- 无 JSON 结构 (`{` 开头)
- Base64 解码后得到 `ef b8 c0 a6 14 bd...` 二进制数据 (9481 bytes)
- **非 16 字节对齐** → 排除 AES-CBC/ECB 模式

### 2. 加密算法
- **算法**: 推测为 AES-GCM 或 ChaCha20-Poly1305 (AEAD 认证加密)
  - 证据: 密文大小非 16 对齐，符合 AEAD 模式特征
  - 结构可能是: `[12字节 Nonce] + [密文] + [16字节 Auth Tag]`
- **密钥来源**: 内嵌在剪映应用二进制文件中，无法通过逆向工程轻易获取
- **CapCut 国际版未加密** — 仅中国版剪映加密 (来源: [capcut-cli](https://github.com/renezander030/capcut-cli))

### 3. jianying_assistant 工具
- 位于 `D:\Users\ersit\AppData\Local\jianying_assistant`（实际在 Roaming 目录）
- **不是解密工具**，是「宇辰剪映小助手」— 从 API 获取草稿数据并生成剪映可识别的草稿文件
- 来源: [GitHub](https://github.com/2547989830-lang/jianying-assistant)

### 4. crypto_key_store.dat
该文件是**媒体文件加密密钥库**（用于云端同步的素材加密），与 `draft_content.json` 的加密无关。
- 跳过前 4 字节头 (`00 00 02 18`) 后 zlib 可解压
- 内容包含 `cipher_key`、`cipher_type`、`uri` 等字段
- 指向云端加密的视频文件

### 4. 备份文件
剪映会保留明文备份，我们的脚本已支持自动回退：
```
优先级: draft_content.json → template.json → template.json.bak → draft_content.json.bak
```

## 社区状况

| 项目 | 状态 | 备注 |
|------|------|------|
| [capcut-cli](https://github.com/renezander030/capcut-cli) | 仅检测加密，不解密 | "算法有法律风险，社区方案在变动" |
| [JyDraft](https://github.com/HTWMedia/JyDraft) | README 称支持解密 | 实际代码无 AES 实现，走云端渲染 |
| [pyJianYingDraft](https://github.com/GuanYixuan/pyJianYingDraft) | 不支持加密草稿 | Issue #142 讨论了模板替代方案 |
| [duoec/duo-video](https://github.com/duoec/duo-video) | Java 实现 | 被 capcut-cli 引用为参考，但为 Java |
| [douyinchaijie](https://github.com/lvxiaotu/douyinchaijie) | 适配器模式 | 需外部解密工具 |

## 可能的破解方向

### 方向 A: 从应用二进制提取 AES 密钥
- 需要找到剪映安装目录的 `JianyingPro.exe` 或相关 DLL
- 使用 strings / IDA / Ghidra 搜索 AES 密钥
- 优点: 一劳永逸
- 缺点: 需要逆向工程能力，版本更新可能更换密钥

### 方向 B: 内存抓取
- 运行剪映，用 Frida / x64dbg 在内存中抓取解密后的 JSON
- 优点: 不需要找到密钥，直接获取明文
- 缺点: 需要剪映正在运行，不方便自动化

### 方向 C: 利用明文备份
- 我们的脚本已经实现了: 自动读取 `template.json` / `template.json.bak`
- 这是目前最可靠的方案
- 缺点: 备份不一定存在

### 方向 D: 使用 CapCut 国际版
- CapCut 国际版 **不加密** `draft_content.json`
- 如果工作流允许，直接用国际版编辑

## 建议

**短期**: 继续依赖明文备份回退方案 (已实现)
**中期**: 如果能找到剪映安装路径，尝试用 `strings` 搜索 AES 密钥模式
**长期**: 关注社区进展，特别是 duoec/duo-video 的 Java 实现是否有人移植到 Python

## 附: 搜索 AES 密钥的方法

如果能找到剪映安装目录:
```bash
# 在二进制文件中搜索可能的 AES 密钥 (16/24/32 字节)
strings JianyingPro.exe | grep -E "^[A-Za-z0-9+/]{16,44}=$"
# 或搜索已知的 AES 常量
xxd JianyingPro.exe | grep "637c777b"
```

搜索 GitHub:
```
gh search code "AES" "jianying" --language python
gh search code "0102030405060708" "jianying"
```
