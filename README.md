# 萌迹 · Baby Steps

**宝宝照片 / 视频自动整理工具 —— 按拍摄日期自动分类，免费开源，无广告，无内购。**

A free, open-source tool that automatically sorts baby photos and videos into dated folders based on when they were actually taken. No ads, no in-app purchases, no bundled software.

[简体中文](#简体中文) | [English](#english)

---

## 简体中文

### 这是做什么的

手机相机直出、微信导出、各种 App 导出的照片/视频，文件名往往乱七八糟，格式五花八门。**萌迹**会自动识别每个文件的拍摄日期，按"第几个月"分类复制到目标文件夹，不用再一张张手动挪。

专门为记录宝宝成长设计：文件夹按"出生后第几个月"分档，而不是按普通日历月份，更贴近你实际想按"月龄"整理相册的习惯。

### 功能特点

| 功能 | 说明 |
|---|---|
| 识别规则可自定义 | 存放在 `babystep_data/rules.json`，遇到新命名格式不用改代码，加一条配置即可；也可以用软件内置的 AI 提示词模板，丢给任意 AI 对话工具帮你生成新规则 |
| 出生日期 / 分档间隔可配置 | 界面里直接设置，保存后写入 `babystep_data/config.json` |
| EXIF / 视频元数据交叉校验 | 文件名规则识别不了的文件，会尝试读取图片 EXIF 或视频元数据里的拍摄时间兜底 |
| 通用猜日期 | 文件名匹配不上任何规则时，会在文件名里找一段合理的日期/时间戳兜底猜测，并标注"猜测"提醒核对 |
| 三级可信度分类存放 | 文件名识别 / 元数据识别 / 修改时间兜底，三种可信度的结果分开存放，一眼看出哪些该重点核对 |
| 缩略图 / 文件信息预览 | 双击结果列表里的一行，弹出缩略图（图片）或文件信息（视频）核对 |
| 增量处理 | 上次已成功归类过的文件，这次自动跳过，不用重新计算哈希，提速 |
| 内容去重 | 按文件哈希判断是否真的重复，而不是只看文件名，避免同一张照片被重复复制 |
| 系统垃圾文件过滤 | `Thumbs.db`、`.DS_Store` 等自动跳过，不会被误归类 |
| 检查更新 | 启动时自动静默检查一次，也可手动点按钮检查；发现新版本可选择前往下载 / 跳过此版本 / 下次再说 |
| 中英文双语界面 | 右上角一键切换，所有提示、日志、帮助文档均随语言联动 |

### 整理结果放在哪，可信度从高到低

为了让你一眼看出某个文件的日期是"实打实从文件名读出来的"还是"猜/蒙出来的"，不同可信度的文件会分开存放，而不是混在一起：

1. **目标根文件夹下的"几月（日期范围）"文件夹** —— 最可靠。文件名本身带日期，靠正式规则识别出来的；或者所有正式规则都不匹配、靠通用规则猜出来的（会标注"⚠通用规则猜测，建议核对"）。
2. **`元数据识别` 子文件夹** —— 第二可靠。文件名完全解析不出日期，但照片 EXIF 或视频自带元数据里记录了拍摄时间。
3. **`文件修改时间兜底` 子文件夹** —— 相对最不可靠。文件名和元数据都没法判断日期，最后靠文件系统的"修改时间"兜底猜的，建议重点抽查这个文件夹。
4. **`待人工确认` 文件夹** —— 以上三种方式全部失败，需要手动打开查看内容后自行归类。

以上四类文件夹共享同一套"月份区间"计算逻辑：只要目标文件夹里任意一处已经有某个月份的文件夹（哪怕是你手动改过名字、合并过日期区间），其余三类归类时都会复用同一个文件夹名和日期区间，不会出现"同一个月份在不同地方对不上号"的情况。

### 下载安装

**普通用户**：前往 [Releases](../../releases) 页面下载对应版本的 exe，双击即可使用，无需安装 Python 或任何依赖。

> ⚠️ **防伪提醒**：本项目完全免费，无广告，无内购，不会要求你绑定安装其他软件。如果你拿到的版本提示付费、弹广告、或要求捆绑安装其他软件，那不是官方原版，请提高警惕，建议从本仓库或 Releases 页面重新下载。

**开发者 / 想直接跑源码**：

```bash
python 萌迹.py
```

标准库即可跑最基础的整理功能。如果想启用"EXIF/视频元数据交叉校验"和"缩略图预览"这两项，需要额外安装：

```bash
pip install pillow hachoir
```

（HEIC 格式的缩略图预览可能还需要额外装 `pillow-heif`，非必需）

第一次运行会自动在脚本所在目录创建 `babystep_data` 子文件夹，并在里面生成 `config.json` 和 `rules.json`（如果还没有的话）。

### 使用步骤

1. 填 **"待整理文件夹"**：存放待处理照片/视频的文件夹路径。
2. 填 **"目标根文件夹"**：整理好之后放到哪个文件夹下面。
3. 先点 **【① 预览】** —— 这一步不会真的复制任何文件，只是列出"每个文件会被放进哪个文件夹"，方便核对是否正确。
4. 核对无误后，再点 **【② 开始整理】** 才会真正复制文件（使用的是"复制"而非"移动"，原文件始终安全，不会被删除或改动）。
5. 双击结果列表里的任意一行，可以预览缩略图或查看文件信息，再次肉眼确认。

### rules.json 规则写法

`rules.json` 是一个 JSON 数组，每条规则包含 `name`（规则说明，仅用于展示）、`type`（解析类型）、`pattern`（正则表达式）三个字段：

| type | 说明 | pattern 要求 |
|---|---|---|
| `digits8` | 文件名里有连续 8 位数字表示 `YYYYMMDD` | 1 个捕获组，捕获到的数字前 8 位会被当作日期 |
| `digits8_dashed` | 日期带横杠分隔，如 `YYYY-MM-DD` | 3 个捕获组，依次对应年 / 月 / 日 |
| `epoch_ms` | 文件名里是一段 13 位毫秒级 Unix 时间戳（常见于微信导出） | 1 个捕获组 |
| `epoch_s` | 文件名里是一段 10 位秒级 Unix 时间戳（常见于监控摄像头 / IoT 设备） | 1 个捕获组 |

示例：

```json
{
  "name": "示例：某App导出格式",
  "type": "digits8",
  "pattern": "^SomeApp_(\\d{8})_\\d{6}"
}
```

### 遇到识别不了的新文件名格式怎么办

不需要自己研究正则表达式，打开软件里的 **【📖 使用说明】**，点 **"复制下方 AI 提示词模板"** 按钮，把模板和你的新文件名例子一起丢给任意 AI 对话工具（Claude、ChatGPT、豆包、DeepSeek 等都可以），它会帮你按上表格式生成一条新规则。复制粘贴进 `rules.json`、点软件里的 **"重新加载规则文件"** 按钮即可生效，无需重启软件。

### 版本更新机制

`version.json` 记录了当前最新版本号、更新日志和下载地址，软件内置的"检查更新"功能会读取这个文件（依次尝试 jsdelivr CDN 和 GitHub 原始地址，其中一个访问不了会自动换下一个）。发现新版本时，你可以选择"前往下载"、"跳过此版本"（下次静默检查不会再提醒同一版本，但手动点检查按钮依然会提示）或"下次再说"。

### 隐私说明

除了"检查更新"这一个功能会连接 GitHub / jsdelivr 读取版本信息外，本软件的所有照片整理操作都是**完全离线**的，不会以任何形式上传、读取或分析你的照片内容。识别规则、配置、日志全部存储在你自己的电脑上。

### 常见问题（FAQ）

**Q: 打包成的 exe 被杀毒软件拦截/报毒，是中毒了吗？**
不是。用 PyInstaller 打包的 Python 程序（尤其是 `--onefile` 单文件模式）因为运行时会自解压到临时目录，这个行为特征跟部分恶意软件相似，容易被杀毒软件误判，这是已知的通病，属于误报，加个信任例外即可。如果很介意，打包时改用 `--onedir` 模式可以降低误报率（代价是分发时要给一整个文件夹而不是单个 exe）。

**Q: 为什么有些视频文件的"元数据识别"没生效？**
需要先安装 `hachoir` 库（`pip install hachoir`）。另外，有些视频在传输/压缩过程中本身就会丢失元数据（比如经过微信压缩的视频），这种情况即使装了库也读不出来，会自动走"文件名兜底猜测"或"修改时间兜底"。

**Q: 中英文切换会不会把已经整理好的文件夹搞乱？**
不会。生成的文件夹名（比如 `5.5月(2024.10.16-2024.11.15)`）固定使用这一种格式，不会跟随界面语言变化，保证同一批照片不管用哪种界面语言整理，都会被分类进同一个文件夹。

**Q: 支持 macOS / Linux 吗？**
理论上源码可以跑（用的是 Python 标准库 tkinter，跨平台），但目前只在 Windows 上打包和测试过，Releases 页面提供的也只有 Windows exe。macOS/Linux 用户可以直接用源码运行。

### 反馈与贡献

欢迎在 [Issues](../../issues) 里反馈问题、提出新功能建议，或者直接提交新的 `rules.json` 命名规则（附上文件名例子会更方便核对）。

### 许可协议

本项目采用 [MIT License](LICENSE) 开源协议。这意味着你可以自由使用、复制、修改、合并、发布、分发本项目，只需保留原始的版权声明和许可声明即可。

### 作者

c和弦（cchord135）

---

## English

### What is this

Photos and videos straight off your phone, exported from WeChat, or saved from various apps often end up with messy, inconsistent filenames. **Baby Steps** automatically works out the capture date of each file and sorts it into a "month X" folder, so you don't have to drag files around by hand.

Purpose-built for tracking a baby's growth: folders are organised by "months since birth" rather than plain calendar months, matching how most parents actually think about their baby photo albums.

### Features

| Feature | Description |
|---|---|
| Customisable filename rules | Stored in `babystep_data/rules.json` — add a new rule for a new filename format without touching any code, or use the built-in AI prompt template to have any AI chat tool write the rule for you |
| Configurable birth date / month intervals | Set directly in the app, saved to `babystep_data/config.json` |
| EXIF / video metadata cross-check | Falls back to reading the capture time from a photo's EXIF data or a video's own metadata when the filename can't be parsed |
| Generic date guessing | If nothing matches, scans the filename for something that looks like a date/timestamp as a last resort, and flags it as "guessed" for review |
| Three-tier confidence sorting | Filename / metadata / modified-time results are kept in separate folders, so you can tell at a glance which ones deserve a closer look |
| Thumbnail / file-info preview | Double-click any row in the results list to see a thumbnail (photos) or file details (videos) |
| Incremental processing | Files successfully sorted in a previous run are skipped automatically, without recomputing hashes, for a faster re-run |
| Content-based deduplication | Compares actual file content (via hash), not just filenames, so the same photo is never copied twice |
| System junk file filtering | `Thumbs.db`, `.DS_Store`, etc. are automatically skipped, never mis-sorted |
| Update checking | Checks silently on launch, or manually via a button; when a new version is found you can download, skip that version, or be reminded later |
| Bilingual interface | One-click toggle in the top-right corner — every label, log message, and the help guide switch language together |

### Where sorted files end up, from most to least reliable

So you can tell at a glance whether a date was read straight off the filename or guessed, files of different confidence levels are kept apart rather than mixed together:

1. **A "month X (date range)" folder directly inside the destination folder** — most reliable. The filename itself encoded the date and matched a proper rule, or — if no rule matched — was guessed by scanning the filename (flagged "⚠ Guessed - please double-check").
2. **The `元数据识别` (Metadata match) subfolder** — second most reliable. The filename gave no usable date, but the photo's EXIF or the video's own metadata recorded a capture time.
3. **The `文件修改时间兜底` (Modified-time fallback) subfolder** — least reliable of the three. Neither the filename nor metadata worked, so the file's "last modified" timestamp was used as a last resort — worth double-checking anything here.
4. **The `待人工确认` (Needs Review) folder** — all three methods failed; you'll need to open the file and sort it manually.

All four tiers share the same month-folder logic: if any of them already has a folder for a given period (even one you renamed or merged by hand), the others will reuse that exact folder name and date range rather than calculating it independently.

### Download & Installation

**For everyday users**: grab the exe for your version from the [Releases](../../releases) page and just double-click it — no Python or dependencies required.

> ⚠️ **A note on authenticity**: this project is completely free, with no ads and no in-app purchases, and it will never ask you to bundle-install other software. If a copy you've downloaded asks for payment, shows ads, or tries to bundle other software, it isn't the official version — please be cautious, and re-download from this repository or its Releases page.

**For developers / running from source**:

```bash
python 萌迹.py
```

The standard library alone covers the core sorting functionality. To enable EXIF/video metadata cross-checking and thumbnail previews, also install:

```bash
pip install pillow hachoir
```

(HEIC thumbnail previews may additionally need `pillow-heif`, which is optional.)

On first run, a `babystep_data` subfolder is created automatically next to the script, containing freshly generated `config.json` and `rules.json` files if none exist yet.

### Basic workflow

1. **"Folder to sort"**: the folder holding your unsorted photos/videos.
2. **"Destination folder"**: where the sorted files should end up.
3. Click **[1. Preview]** first — nothing gets copied at this stage, it just shows where each file would go, so you can check it looks right.
4. Once you're happy, click **[2. Start Organising]** to actually copy the files (this tool only ever copies — never deletes or moves — so your originals stay safe).
5. Double-click any row in the results list for a thumbnail or file-info check.

### Writing your own rules.json rules

`rules.json` is a JSON array; each rule has three fields — `name` (a description, display only), `type` (how to parse it), and `pattern` (a regular expression):

| type | Meaning | Pattern requirements |
|---|---|---|
| `digits8` | 8 consecutive digits encode `YYYYMMDD` | 1 capture group; the first 8 digits captured are used as the date |
| `digits8_dashed` | Dashed date format, e.g. `YYYY-MM-DD` | 3 capture groups, for year / month / day respectively |
| `epoch_ms` | A 13-digit millisecond Unix timestamp (common in WeChat exports) | 1 capture group |
| `epoch_s` | A 10-digit second-level Unix timestamp (common on security cameras / IoT devices) | 1 capture group |

Example:

```json
{
  "name": "Example: some app's export format",
  "type": "digits8",
  "pattern": "^SomeApp_(\\d{8})_\\d{6}"
}
```

### Handling a brand-new filename format

No need to learn regular expressions yourself. Open **[📖 User Guide]** inside the app, click **"Copy AI Prompt Template"**, and paste it into any AI chat tool (Claude, ChatGPT, etc.) along with a few examples of the unrecognised filenames — it'll return a new rule in the format above. Paste it into `rules.json`, click **"Reload Rules File"** in the app, and it takes effect immediately — no restart needed.

### How the update mechanism works

`version.json` records the latest version number, changelog, and download link; the app's update checker reads this file (trying a jsdelivr CDN mirror first, then falling back to the raw GitHub URL). When a new version is found, you can choose to download it, skip that version (silent checks won't nag you about it again, though manually clicking the button still will), or be reminded later.

### Privacy

Aside from the update-check feature, which contacts GitHub/jsdelivr to read version info, every part of this tool's photo-sorting runs **entirely offline** — nothing about your photo content is ever uploaded, read remotely, or analysed externally. Rules, settings, and logs all live on your own machine.

### FAQ

**Q: My antivirus flags/blocks the packaged exe — is it actually infected?**
No. PyInstaller-built executables (especially in `--onefile` mode, which self-extracts to a temp folder on every launch) share a behavioural pattern with some malware, so they're commonly flagged as a false positive — this is a well-known quirk, not an actual infection. Just add a trust exception. If it bothers you, packaging with `--onedir` instead reduces false positives (at the cost of distributing a whole folder instead of a single exe).

**Q: Why doesn't metadata detection work for some of my videos?**
You'll need `hachoir` installed (`pip install hachoir`). Also, some videos genuinely lose their metadata during transfer or compression (e.g. videos re-compressed by WeChat) — in that case even with the library installed there's nothing to read, and the tool falls back to a filename guess or the modified-time fallback.

**Q: Will switching languages mess up folders I've already sorted?**
No. Generated folder names (e.g. `5.5月(2024.10.16-2024.11.15)`) always keep this exact format regardless of the display language, so the same batch of photos always lands in the same folder no matter which language you use.

**Q: Does this work on macOS / Linux?**
The source should run there in principle (it's built on Python's cross-platform tkinter), but packaging and testing have only been done on Windows so far, and only a Windows exe is provided on the Releases page. macOS/Linux users can run it from source.

### Feedback & Contributing

Bug reports, feature requests, and new `rules.json` patterns are all welcome via [Issues](../../issues) — including a filename example makes it much easier to verify.

### License

This project is released under the [MIT License](LICENSE). You're free to use, copy, modify, merge, publish, and distribute it, as long as the original copyright and license notice are kept.

### Author

c和弦 (cchord135)
