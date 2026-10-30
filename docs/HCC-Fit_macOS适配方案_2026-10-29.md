# HCC-Fit macOS 适配方案（2026-10-29）

> 结论：**可以适配 macOS**。当前是 PySide6 + 纯 Python 核心，Windows 专属代码很少；
> 主要成本在 macOS 打包 `.app`、中文字体/PDF、HTTPS 证书、签名与公证。
> 本机是 Windows，**不能交叉编译 macOS 包**，最终构建/验收需要一台 Mac 或 macOS CI。

---

## 1. 现状评估

| 模块 | macOS 可行性 | 说明 |
|---|---|---|
| PySide6 UI | ✅ 高 | 同一套 Qt 代码；需测窗口尺寸、菜单栏、暗色模式 |
| QThread / 信号 | ✅ 高 | 无平台依赖 |
| 场景卡/规则/补充资料 | ✅ 高 | 纯 Python |
| `QStandardPaths` 数据目录 | ✅ 高 | macOS 会落到 `~/Library/Application Support/...` |
| PDF 导出 | ✅ 中 | `QPdfWriter` 跨平台；需换 PingFang 字体并实测中文 |
| 中文字体 | ⚠️ 需改 | 当前有 PingFang 回退，但标题写死 `Microsoft YaHei`；需统一 `cjk_font_family()` |
| PDF 文本提取 | ✅ 高 | `pdftext.py` 是纯标准库，不依赖 `pdftotext` |
| SciencePal HTTPS | ⚠️ 需改 | `urllib` 在 macOS 打包后可能缺 CA 证书；建议加 `certifi` + `ssl.create_default_context` |
| 资源路径 | ⚠️ 需改 | `.app` 的 `Contents/Resources` 与当前 `BASE/resources` 不一致 |
| Windows `.bat` | ❌ 不适用 | 需加 `.command` / `build_macos.sh` |
| PyInstaller 打包 | ⚠️ 需重做 | Windows spec 不能直接用；需 macOS spec、`.app`、`upx=False` |
| 签名/公证 | ⚠️ 额外 | 未签名会被 Gatekeeper 拦；正式分发需 Apple Developer |

**结论：业务代码约 80–90% 可直接复用，2–4 天可出未签名测试版；签名/公证再加 1 天左右。**

---

## 2. 需要改的代码

### 2.1 字体

- `main_window.py` 标题写死 `QFont("Microsoft YaHei", 16, ...)` → 改成 `cjk_font_family()`；
- `export.py` 已有 `PingFang SC` 回退，但默认参数仍是 `"Microsoft YaHei"`；
- macOS 用 `PingFang SC`，PDF 用 `QFontDatabase.addApplicationFont("/System/Library/Fonts/PingFang.ttc")`；
- 测试中文 PDF 是否方框、是否嵌入字体。

### 2.2 资源路径

当前：

```python
BASE = Path(__file__).resolve().parent.parent
RESOURCES = BASE / "resources"
```

macOS `.app` 中资源通常在：

```
HCC-Fit.app/Contents/Resources/resources
```

需要按 frozen + `sys.platform == "darwin"` 判断：

```python
def _base_dir():
    if getattr(sys, "frozen", False):
        exe = Path(sys.executable).resolve()
        if sys.platform == "darwin" and exe.parent.name == "MacOS":
            return exe.parent.parent          # Contents
        return exe.parent
    return Path(__file__).resolve().parent.parent

RESOURCES = _base_dir() / ("Resources/resources" if frozen_mac else "resources")
```

并在 macOS 打包后验证 `terms.csv`、`visit.schema.json` 能被找到。

### 2.3 HTTPS 证书

`core/sciencepal.py`、`app/ai_extract.py` 用 `urllib.request.urlopen`。
macOS 打包后 Python 可能找不到系统 CA，出现 `CERTIFICATE_VERIFY_FAILED`。

建议：

- 依赖加 `certifi`；
- 统一：

```python
import ssl, certifi
ctx = ssl.create_default_context(cafile=certifi.where())
urlopen(req, context=ctx, timeout=...)
```

- PyInstaller 加 `--hidden-import certifi`；
- 在 macOS 上实测 SciencePal 和 AI 端点。

### 2.4 平台入口

- 新增 `run_desktop.command`（双击启动）：

```bash
#!/bin/bash
cd "$(dirname "$0")"
open "HCC-Fit.app"
```

- 新增 `build_macos.sh`；
- 删除/保留 Windows `.bat` 不影响。

### 2.5 UI 细节

- 窗口 1420×900 在 13 寸 Mac 上可能偏大，建议 1280×820 + 最小 1100×720；
- 暗色模式下当前自定义白底/深色文字可能对比度不足；建议首版固定浅色 Fusion 调色板；
- macOS 菜单栏：无自定义菜单也能 Cmd+Q；文件对话框正常；
- `QDesktopServices.openUrl` 打开 Finder，正常；
- 关闭窗口时线程检查逻辑跨平台，正常。

---

## 3. macOS 打包

新增 `HCC-Fit-macos.spec`：

- `console=False`；
- `upx=False`（macOS 不建议 UPX）；
- `datas=[('resources','resources')]`；
- `BUNDLE(...)` 生成 `HCC-Fit.app`；
- `info_plist` 设置：
  - `CFBundleIdentifier`：`ai.hccfit.desktop`
  - `CFBundleShortVersionString`：`0.1.0`
  - `LSMinimumSystemVersion`：`12.0`（按 Qt/PySide6 实际支持调整）
  - `NSHighResolutionCapable`：`True`
- 构建命令：

```bash
python -m PyInstaller --noconfirm HCC-Fit-macos.spec
```

产物：

```
dist/HCC-Fit.app
```

- Apple Silicon 构建 arm64；
- 如依赖都是 universal2，可 `--target-arch universal2` 同时支持 Intel；
- 否则分别构建 arm64 / x86_64 两个包。

**不能在 Windows 上打 macOS 包**，PyInstaller 不支持交叉编译；需要 Mac 或 macOS CI。

---

## 4. 签名与公证

### 4.1 仅朋友内测（未签名）

- 把 `HCC-Fit.app` 压缩成 zip 发过去；
- 朋友解压后**右键 → 打开**；
- 如仍被拦：

```bash
xattr -dr com.apple.quarantine /Applications/HCC-Fit.app
```

- 这不是长期方案，Gatekeeper 会提示“无法验证开发者”。

### 4.2 正式分发（建议）

需要 Apple Developer Program（约 $99/年）：

1. 生成 **Developer ID Application** 证书；
2. `codesign --deep --force --options runtime --sign "Developer ID Application: ..." HCC-Fit.app`；
3. `ditto -c -k --keepParent HCC-Fit.app HCC-Fit.zip`；
4. `xcrun notarytool submit HCC-Fit.zip --apple-id ... --team-id ... --password ... --wait`；
5. `xcrun stapler staple HCC-Fit.app`；
6. 用 `hdiutil create` 做 `.dmg`。

签名后用户双击即可，不再需要右键打开。

---

## 5. 测试矩阵

| 项目 | Apple Silicon | Intel | 说明 |
|---|---|---|---|
| macOS 13 / 14 / 15 | 必测 | 可选 | 至少一个当前主流版本 |
| 离线 demo | 必测 | 必测 | 不联网能跑 |
| 真实 SciencePal | 必测 | 可选 | HTTPS、Key、3–5 分钟 |
| 中文 PDF | 必测 | 必测 | PingFang、无方框 |
| 拖拽 PDF / 文件选择 | 必测 | 可选 | Finder 路径 |
| 场景卡/追问/确认 | 必测 | 可选 | 窗口尺寸、暗色 |
| 补充资料/新旧报告 | 必测 | 可选 | 全量重跑 |
| 导出 PDF/JSON | 必测 | 必测 | 打开/保存对话框 |
| 暗色模式 | 必测 | 可选 | 对比度 |
| Gatekeeper | 必测 | 可选 | 未签名/已签名 |

打包后自检：

```bash
HCC-Fit.app/Contents/MacOS/HCC-Fit --smoke
echo $?
```

应返回 0，并生成 `smoke_report.pdf` 和 `input_session`。

---

## 6. 工时

| 阶段 | 内容 | 预估 |
|---|---|---|
| P0 | 字体、资源路径、证书、平台入口 | 1 天 |
| P1 | macOS spec/`.app` 构建、`--smoke` | 1 天 |
| P2 | UI 尺寸/暗色/PDF/真实 Key 回归 | 1 天 |
| P3 | 签名、公证、DMG（可选） | 0.5–1 天 + Apple 账号 |

**未签名内测版：约 3 天；签名正式版：约 4 天**（不含 Apple 账号审核/排队）。

---

## 7. 前提与需要确认

1. **目标 Mac 架构**：Apple Silicon（M1–M4）还是 Intel？  
   - 建议先做 Apple Silicon arm64；Intel 用户可另做 x86_64 或 universal2。
2. **macOS 版本**：建议最低 12.0（Monterey）或 13.0（Ventura），按朋友机器定。
3. **分发方式**：
   - 朋友内测：未签名 zip + 右键打开即可；
   - 正式分发：需要 Apple Developer 账号做签名/公证。
4. **构建机器**：
   - 本机 Windows 不能打 macOS 包；
   - 需要一台 Mac，或 GitHub Actions macOS runner，或云 Mac。
5. **是否保留 Windows 版**：建议同一套代码，两个构建脚本，分别出 Windows zip / macOS `.app`。

---

## 8. 建议下一步

1. 先确认朋友的 Mac 是 **Apple Silicon 还是 Intel**、macOS 版本；
2. 如果能提供一台 Mac / macOS CI，我先做 **P0 代码适配**（字体、资源路径、certifi、入口脚本），再出未签名 `.app`；
3. 朋友能接受“右键打开/去隔离”就先内测；要正式分发再走 Apple Developer 签名/公证；
4. Windows 版继续保留，两个平台共用 `app/`、`core/`、`resources/`，只分构建脚本和平台路径。

---

*本方案只读现有代码，未修改任何文件；确认目标 Mac 架构和构建条件后开始 P0。*
