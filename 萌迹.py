# -*- coding: utf-8 -*-
"""
萌迹 —— 宝宝照片/视频整理工具 v2(通用版)
======================================
比 v1 增加的能力:
    1. 文件名识别规则外部化到 babystep_data 子文件夹下的 rules.json —— 以后遇到新命名格式,
       直接在 rules.json 里加一条,不用改这份 .py 代码。
    2. 出生日期 / 分档间隔可以在界面里设置, 保存后写入 babystep_data 子文件夹下的 config.json。
    3. 读取图片EXIF / 视频元数据里的"拍摄时间", 跟文件名解析结果交叉校验,
       文件名规则识别不了的文件也能靠这个兜底。
    4. 文件名完全匹配不上任何规则时, 会尝试"通用猜日期"(在文件名里找一段
       合理的8位数字当日期), 并标注为"猜测", 提醒你核对。
    5. 双击预览列表里的一行, 弹出缩略图核对(图片), 或文件信息(视频)。
    6. 增量处理: 上次已经成功归类过的文件, 这次会跳过, 不用重新计算哈希, 提速。
    7. 自动过滤 Thumbs.db / .DS_Store 等系统垃圾文件。

依赖:
    - 标准库即可跑最基础功能(不装额外库也能用, 只是没有EXIF/视频元数据校验)
    - 如果想启用"元数据交叉校验"和"缩略图预览", 需要:
        pip install pillow hachoir
      (HEIC格式的缩略图预览可能还需要额外装 pillow-heif, 非必需)

使用方法:
    双击运行, 或命令行: python 萌迹.py
    第一次运行会自动在脚本所在目录创建 babystep_data 子文件夹, 并在里面生成
    config.json 和 rules.json(如果没有的话)
"""

import os
import re
import csv
import json
import sys
import shutil
import hashlib
import calendar
import threading
import urllib.request
import webbrowser
from datetime import date, datetime, timedelta, timezone
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, ttk

# ------------------------- 可选依赖(缺失也不影响基础功能) -------------------------

try:
    from PIL import Image, ImageTk
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

try:
    from hachoir.parser import createParser
    from hachoir.metadata import extractMetadata
    HACHOIR_AVAILABLE = True
except ImportError:
    HACHOIR_AVAILABLE = False

IMAGE_EXTS = {'.jpg', '.jpeg', '.png', '.bmp', '.tiff', '.heic', '.heif'}

# ------------------------- 路径 & 默认配置 -------------------------

SCRIPT_DIR = os.path.dirname(os.path.abspath(sys.executable)) if getattr(sys, 'frozen', False) \
    else os.path.dirname(os.path.abspath(__file__))


def resource_path(filename):
    """
    定位'内嵌资源'(比如图标)的路径:
    - 打包成exe后, 优先去PyInstaller解压出来的临时目录(--add-data 打包进去的文件在这里)
    - 没打包时(直接跑.py), 就是脚本所在目录
    这跟 config.json/rules.json 用的 SCRIPT_DIR 是两套路径逻辑:
    config/rules 需要放在exe外面、用户能自己编辑; 图标这种资源则适合内嵌进exe, 不需要外部文件也能用。
    """
    base = getattr(sys, '_MEIPASS', None) or SCRIPT_DIR
    return os.path.join(base, filename)

DATA_DIR = os.path.join(SCRIPT_DIR, "babystep_data")
os.makedirs(DATA_DIR, exist_ok=True)


def _migrate_old_file(filename):
    """兼容旧版本: 如果 config.json/rules.json 之前直接放在软件同目录下, 自动挪进 babystep_data 里"""
    old_path = os.path.join(SCRIPT_DIR, filename)
    new_path = os.path.join(DATA_DIR, filename)
    if os.path.exists(old_path) and not os.path.exists(new_path):
        try:
            shutil.move(old_path, new_path)
        except Exception:
            pass


_migrate_old_file("config.json")
_migrate_old_file("rules.json")

CONFIG_PATH = os.path.join(DATA_DIR, "config.json")
RULES_PATH = os.path.join(DATA_DIR, "rules.json")

def get_mtime_date(path: str):
    """兜底中的兜底: 文件名规则和EXIF/视频元数据都失败时, 用文件系统的'修改时间'猜日期。
    对着监控摄像头/存储卡直接复制这类"文件从没被二次编辑过"的场景比较可靠,
    但如果文件曾被其他软件转存/编辑过(修改时间=转存时间), 就不准, 所以只作为最后一道兜底,
    并且在结果里明确标注, 提醒人工核对。"""
    try:
        ts = os.path.getmtime(path)
        return datetime.fromtimestamp(ts, tz=get_tz()).date()
    except OSError:
        return None


# ------------------------- 版本更新检查 -------------------------
# 每发一个新版本, 记得同步改这里的号(跟 GitHub 仓库里 version.json 的 "version" 保持对应逻辑:
# version.json 里永远填"最新已发布版本", 这里填"这份代码/这个exe自己的版本")
APP_VERSION = "1.0.4"

# 按顺序尝试, 前面的失败了(超时/被墙/网络问题)就换下一个, 都失败才算检查失败
UPDATE_CHECK_URLS = [
    "https://cdn.jsdelivr.net/gh/cchord135/mengji-babysteps@main/version.json",
    "https://raw.githubusercontent.com/cchord135/mengji-babysteps/refs/heads/main/version.json",
]


def _version_tuple(v):
    """把 '1.0.1' 这样的版本号字符串转成 (1, 0, 1) 方便比较大小; 非数字部分会被忽略"""
    parts = [int(p) for p in re.findall(r'\d+', v or '')]
    return tuple(parts) if parts else (0,)


def fetch_latest_version_info(timeout=5):
    """依次尝试 UPDATE_CHECK_URLS 里的地址, 拿到 version.json 解析后的内容(dict)。
    全部失败(没网/超时/服务器那边文件有问题)则返回 None, 调用方要能处理这种情况,
    绝不能因为查不到更新就让主程序崩掉或卡住。"""
    for url in UPDATE_CHECK_URLS:
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'mengji-babystep-update-checker'})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode('utf-8')
            data = json.loads(raw)
            if isinstance(data, dict) and 'version' in data:
                return data
        except Exception:
            continue
    return None


MANUAL_REVIEW_FOLDER = "待人工确认"
METADATA_FOLDER = "元数据识别"
MTIME_FALLBACK_FOLDER = "文件修改时间兜底"
INDEX_FILENAME = ".萌迹_索引.json"

JUNK_FILENAMES = {"thumbs.db", "desktop.ini", ".ds_store", "ehthumbs.db"}
JUNK_DIR_NAMES = {"@eadir", ".thumbnails", "$recycle.bin", "system volume information"}

DEFAULT_CONFIG = {
    "birth_date": "2024-06-15",
    "monthly_stage_months": 12,
    "interval_after_months": 6,
    "timezone_hours": 8,
    "skipped_version": "",  # 用户在"检查更新"弹窗里点过"跳过此版本"的话, 记在这里, 下次启动的静默检查不会再为同一版本打扰他
}

DEFAULT_RULES = [
    {"name": "Screenshot_/Record_(带横杠日期时间)", "type": "digits8_dashed",
     "pattern": r'^(?:Screenshot|Record)_(\d{4})-(\d{2})-(\d{2})-\d{2}-\d{2}-\d{2}(?:-\d+)?_'},
    {"name": "MEITU_(美图秀秀)", "type": "digits8", "pattern": r'^MEITU_(\d{8})_\d{6}'},
    {"name": "lv_0_", "type": "digits8", "pattern": r'^lv_0_(\d{8})\d{6}'},
    {"name": "Wuta_", "type": "digits8", "pattern": r'^Wuta_(\d{8})_\d{6}'},
    {"name": "L前缀相册导出", "type": "digits8", "pattern": r'^L\d+_\d+_(\d{8})\d{6,9}'},
    {"name": "微信 mmexport/wx_camera(毫秒时间戳)", "type": "epoch_ms",
     "pattern": r'^(?:mmexport|wx_camera_)(\d{13})'},
    {"name": "小米/米家摄像头(MmSs_秒级时间戳)", "type": "epoch_s",
     "pattern": r'^\d{1,3}M\d{1,3}S_(\d{10})'},
    {"name": "IMG_/VID_(日期_时间, 两个下划线)", "type": "digits8",
     "pattern": r'^(?:IMG|VID)_(\d{8})_\d{5,6}'},
    {"name": "IMG_/VID_(日期时间连写, 一个下划线)", "type": "digits8",
     "pattern": r'^(?:IMG|VID)_(\d{8})\d{6}'},
    {"name": "IMG/VID(无下划线连写)", "type": "digits8",
     "pattern": r'^(?:IMG|VID)(\d{8})\d{6}'},
]


AI_PROMPT_TEMPLATE_ZH = """我在用一个Python写的家庭照片整理工具, 它靠 rules.json 里的规则来识别照片/视频文件名里的日期。
现在遇到一种新的命名格式, 现有规则识别不了, 请帮我参考已有格式, 在 rules.json 里加一条新规则。

【新格式的文件名例子, 一个或几个都行】:


【我现在 rules.json 里已有的内容, 照着这个格式续写】:
{
  "name": "示例规则名",
  "type": "digits8",
  "pattern": "^前缀_(\\\\d{8})_\\\\d{6}"
}

要求:
1. 只需要返回新增的这一条JSON规则(一个 {} 对象), 不用把整个文件都写一遍
2. type 只能是这四种之一: "digits8"(文件名里有连续8位数字YYYYMMDD, 只需1个捕获组) /
   "digits8_dashed"(日期是 YYYY-MM-DD 这种带横杠的, 需要3个捕获组分别对应年/月/日) /
   "epoch_ms"(文件名里是一串13位毫秒级Unix时间戳, 只需1个捕获组) /
   "epoch_s"(文件名里是一串10位秒级Unix时间戳, 常见于监控摄像头/IoT设备, 只需1个捕获组)
3. pattern 必须是合法的Python正则表达式, 用 ^ 开头锚定文件名开头
4. 帮我说明这条规则是怎么匹配这个文件名的, 方便我核对
"""

AI_PROMPT_TEMPLATE_EN = """I'm using a Python-based family photo organiser. It relies on a rules.json file to work out
the date hidden in each photo/video filename.

I've just run into a new filename format that none of the existing rules can handle. Could you
help me write one more rule, following the same style as what's already there?

[Example filenames in the new format - paste one or a few here]:


[What's currently in my rules.json - please follow this same style]:
{
  "name": "example rule name",
  "type": "digits8",
  "pattern": "^prefix_(\\\\d{8})_\\\\d{6}"
}

A few requirements:
1. Just send back the one new rule (a single {} object) - no need to rewrite the whole file
2. "type" has to be one of these four: "digits8" (the filename has 8 consecutive digits for
   YYYYMMDD, one capture group) / "digits8_dashed" (the date uses dashes, e.g. YYYY-MM-DD, three
   capture groups for year/month/day) / "epoch_ms" (a 13-digit millisecond Unix timestamp, one
   capture group) / "epoch_s" (a 10-digit second-level Unix timestamp, common on security
   cameras/IoT devices, one capture group)
3. "pattern" needs to be valid Python regex, anchored with ^ at the start of the filename
4. Please also explain how the rule matches the example filename so I can double-check it
"""

HELP_TEXT_ZH = """════════════════════════════════
  萌迹 —— 使用说明
════════════════════════════════

【这个工具是做什么的】
把杂乱命名的照片/视频(手机相机直出、微信导出、各种App导出等),
按拍摄日期自动分类复制到"第几月"这样的文件夹里, 不用手动一张张挪。


【基本操作步骤】
1. 填"待整理文件夹": 存放待处理照片/视频的文件夹路径(可以点"浏览"选)
2. 填"目标根文件夹": 整理好之后要放到哪个文件夹下面
3. 先点【① 预览】—— 这一步不会真的复制任何文件, 只是列出"每个文件
   会被放进哪个文件夹", 方便你核对对不对
4. 核对没问题后, 再点【② 开始整理】才会真正复制文件(不会删除或移动
   原始文件, 使用的是"复制", 原文件始终安全)
5. 双击结果列表里的任意一行, 可以弹出这张照片的缩略图, 肉眼再确认一遍


【整理结果会放进哪几个文件夹, 可信度从高到低】
这个工具判断一个文件"拍摄日期"的手段有好几层, 越靠前的手段越可靠。为了让你一眼
就知道某个文件的日期是"实打实从文件名读出来的"还是"猜/蒙出来的", 不同可信度的
文件会被分开放, 而不是混在一起:

1. 【目标根文件夹】直接底下的"几月(日期范围)"文件夹 —— 最可靠
   文件名本身就带日期(比如 IMG_20241020_091810.jpg), 靠 rules.json 里的正式
   规则识别出来的, 或者文件名规则识别不了、但用"文件名里找一段像日期的数字"
   通用猜出来的(这种会标"⚠通用规则猜测, 建议核对")。
2. 【元数据识别】子文件夹 —— 第二可靠
   文件名完全没法解析出日期, 但照片的EXIF信息或视频自带的元数据里记录了拍摄
   时间, 就靠这个归类。内部还是按"几月(日期范围)"分档, 跟主文件夹是同一套
   命名, 只是单独放进这个子文件夹里, 方便你知道"这批是靠元数据判断的, 没有
   文件名那么确凿", 有空时可以重点抽查一下。
3. 【文件修改时间兜底】子文件夹 —— 相对最不可靠
   文件名解析不出日期, 照片/视频也没有可读的EXIF/元数据(常见于部分监控
   摄像头、老式设备导出的视频), 最后靠文件系统记录的"修改时间"兜底猜的。
   如果这个文件后来被别的软件转存/编辑过, "修改时间"可能等于转存时间而不是
   真正的拍摄时间, 所以建议重点核对这个文件夹里的内容。
4. 【待人工确认】文件夹 —— 完全猜不出来
   文件名、元数据、文件修改时间三种手段全都失败(比如文件本身损坏, 或者
   命名和元数据都被清空过), 只能你自己打开看内容, 手动决定放进哪个月份。

这四类文件夹, 月份分档用的是同一套逻辑: 如果【目标根文件夹】里已经有某个月份
的文件夹了(哪怕是你自己手动改过名字、合并过日期区间), 【元数据识别】和
【文件修改时间兜底】这两个子文件夹在建同一档的时候, 会直接复用同一个文件夹
名字和日期区间, 不会各算各的导致三边对不上号。


【结果列表里常见的提示是什么意思】
- "⚠通用规则猜测, 建议核对"
    文件名没匹配上任何已知格式, 靠"文件名里随便找一段像日期的数字/时间戳"
    猜出来的, 准确率没有正式规则高, 建议重点看一眼
- "⚠与文件元数据日期不一致"
    文件名解析出的日期, 跟照片/视频自带的拍摄时间对不上, 可能其中一个
    是错的, 建议双击这张照片看一眼内容, 判断哪个日期更靠谱
- "文件名无法识别, 已改用文件元数据日期"
    这个文件被放进了【元数据识别】子文件夹, 见上面的说明
- "⚠文件名和元数据均无法识别, 已改用文件'修改时间'兜底, 建议核对"
    这个文件被放进了【文件修改时间兜底】子文件夹, 见上面的说明
- "内容与已有文件重复, 已跳过"
    目标文件夹里已经有一模一样内容的文件了(不只是文件名一样, 是内容
    完全相同), 不会重复复制
- "增量跳过(之前处理过)"
    这个文件在上一次正式整理时已经成功处理过了, 这次自动跳过, 提速用
- 最后如果某个文件三种手段全都识别不了日期, 会被放进"待人工确认"这个
  文件夹, 需要你自己看内容决定放到哪个月份


════════════════════════════════
  重点: 以后遇到"认不出来的新命名格式"怎么办?
════════════════════════════════

这个工具能认出哪些命名格式, 完全取决于跟它放在同目录 babystep_data
文件夹里的 "rules.json"这个文件。以后你的宝宝换了新手机、你装了新的修图App,
导出的文件名格式变了, 工具认不出来很正常, 不代表工具坏了。

【最简单的解决办法: 直接把问题丢给AI】
你完全不需要自己去研究正则表达式怎么写, 按下面步骤操作就行:

第1步: 点这个说明窗口下方的【复制下方AI提示词模板】按钮
第2步: 打开任意一个AI对话工具(比如跟我最初用来做这个工具的
       Claude、或者豆包、DeepSeek、ChatGPT都可以), 粘贴这个模板
第3步: 把模板里两处空着的地方填上:
       - 那几个认不出来的文件名(直接打字或截图都行)
       - 打开 babystep_data 文件夹里的 rules.json(用记事本打开), 把里面的
         内容整个复制粘贴进去
第4步: AI会返回一小段JSON格式的新规则, 长这样:
       {
         "name": "规则名字",
         "type": "digits8",
         "pattern": "^某某前缀_(\\\\d{8})"
       }
第5步: 打开 rules.json, 在最后一条规则的 "}" 后面加一个逗号 ",",
       换行, 把AI给你的这一段粘贴进去, 保存文件
第6步: 回到本软件, 点界面上的【重新加载规则文件(rules.json)】按钮
       (不用重启整个程序), 再跑一次【① 预览】看看能不能认出来了

如果嫌自己粘贴麻烦, 也可以直接把"新文件名 + rules.json内容"一起
发给我(如果你还在跟我对话的话), 我直接帮你把新规则写好, 你复制粘贴
进 rules.json 就行, 全程不用你自己动脑子想正则表达式。


【config.json 是什么, 什么时候需要改】
存的是"出生日期"和"多少个月一档"这两个设置, 正常情况下直接在软件
界面顶部改、点"保存配置"就行, 不需要手动编辑这个文件, 也不需要用
到AI帮忙。


【安全性提醒】
- 本工具默认使用"复制", 不会删除或移动"待整理文件夹"里的原始文件,
  可以放心多跑几次
- 正式整理前建议先跑一次【预览】确认没问题
- rules.json 改坏了(比如JSON格式写错、少了逗号)不会损坏你的照片,
  最多是这个软件打不开或者规则不生效, 把文件删掉重新打开软件会自动
  重新生成一份默认的

【关于语言切换的一点说明】
生成的文件夹名字(比如"5.5月(2024.10.16-2024.11.15)")固定使用这种
格式, 不会因为界面语言切换而改变, 这样才能保证同一批照片不管用哪种
界面语言整理, 都会被放进同一个文件夹里, 不会因为切换语言而分裂成
两套文件夹。
"""

HELP_TEXT_EN = """════════════════════════════════
  Baby Steps — User Guide
════════════════════════════════

WHAT THIS TOOL DOES
Sorts a messy pile of photos and videos - straight off the phone, exported from WeChat, saved
from various apps - into tidy "month X" folders based on when they were actually taken. No more
dragging files around by hand.

BASIC WORKFLOW
1. "Folder to sort": point this at the folder holding your unsorted photos/videos (use Browse if
   that's easier)
2. "Destination folder": where the sorted files should end up
3. Click [1. Preview] first - nothing gets copied at this stage, it just shows you where each
   file would go, so you can check it looks right
4. Once you're happy with the preview, click [2. Start Organising] to actually copy the files
   (originals are never deleted or moved - this tool only ever copies, so your source files stay
   safe)
5. Double-click any row in the results list to pop up a thumbnail of that photo for a quick
   visual check

WHICH FOLDER YOUR FILES END UP IN, FROM MOST TO LEAST RELIABLE
This tool has several tiers of evidence for figuring out a file's date, and the more reliable
ones come first. So you can tell at a glance whether a date was "read straight off the filename"
or "guessed", files at different confidence levels are kept apart rather than mixed together:

1. Straight inside the DESTINATION folder, in a "month X (date range)" folder - most reliable
   The filename itself encodes the date (e.g. IMG_20241020_091810.jpg), matched by a proper rule
   in rules.json, or - if no rule matched - guessed by scanning the filename for something that
   looks like a date/timestamp (flagged "Guessed - please double-check").
2. The "Metadata match" subfolder - second most reliable
   The filename gave no usable date at all, but the photo's EXIF data or the video's own metadata
   recorded a capture time, so that's used instead. Still sorted into the same "month X (date
   range)" folders internally, just kept in this separate subfolder so you know this batch is
   metadata-derived rather than filename-confirmed - worth a spot-check when you have time.
3. The "Modified-time fallback" subfolder - least reliable of the three
   The filename couldn't be parsed and there was no readable EXIF/metadata either (common with
   some security cameras and older devices' video exports), so the file's own "last modified"
   timestamp was used as a last resort. If the file was ever re-saved or edited by another piece
   of software afterwards, this timestamp may reflect that re-save rather than the real capture
   date - worth double-checking anything in this folder.
4. The "Needs Review" folder - couldn't be guessed at all
   All three methods failed (e.g. a corrupted file, or one with both its name and metadata
   stripped) - you'll need to open it and decide which month it belongs in yourself.

All four tiers share the same month-folder logic: if the destination folder already has a
folder for a given month (even one you renamed by hand or merged date ranges on), the "Metadata
match" and "Modified-time fallback" subfolders will reuse that exact same folder name and date
range when they need one for the same period, rather than each calculating it independently and
ending up out of sync.

WHAT THE STATUS MESSAGES MEAN
- "Guessed - please double-check"
    The filename didn't match any known format, so the date was guessed by scanning for
    something that looks like a date/timestamp. Less reliable than a proper rule - worth a
    second look.
- "Doesn't match the file's own metadata"
    The date read from the filename disagrees with the photo/video's embedded capture time. One
    of them is probably wrong - double-click the file to see which date actually looks right.
- "Couldn't read the filename, used the file's metadata date instead"
    This file was filed under the "Metadata match" subfolder - see above.
- "Couldn't read filename or metadata - fell back to the file's 'last modified' timestamp"
    This file was filed under the "Modified-time fallback" subfolder - see above.
- "Duplicate content - skipped"
    A file with identical content already exists in the destination folder (not just the same
    name - the actual content matches), so it wasn't copied again.
- "Skipped (already processed)"
    This file was already sorted successfully in a previous run, so it's skipped this time to
    save time.
- Anything all three methods genuinely can't date gets placed in a "Needs Review" folder for you
  to sort out by hand.

════════════════════════════════
  THE IMPORTANT BIT: what to do about a brand-new filename format
════════════════════════════════

Which filename formats this tool recognises is entirely down to a file called "rules.json" that
sits inside the "babystep_data" folder next to the program. When your baby's on a new phone, or
you start using some new photo app, and the filenames look nothing like before - that's expected,
not a bug.

EASIEST FIX: HAND IT TO AN AI
You don't need to learn regular expressions for this. Just do the following:

Step 1: Click the [Copy AI Prompt Template] button below
Step 2: Open any AI chat tool (Claude, ChatGPT, or whatever you normally use) and paste the
        template in
Step 3: Fill in the two blanks in the template:
        - A few examples of the filenames it can't recognise
        - The current contents of rules.json, found inside the "babystep_data" folder next to
          the program (open it in a text editor and copy the whole thing in)
Step 4: The AI will hand back a short JSON snippet that looks like:
        {
          "name": "rule name",
          "type": "digits8",
          "pattern": "^some_prefix_(\\\\d{8})"
        }
Step 5: Open rules.json, add a comma after the closing "}" of the last rule, paste the new one
        in underneath, and save
Step 6: Back in this program, click [Reload Rules File] (no need to restart), then run Preview
        again to check it's picked it up

If you'd rather not do the copy-pasting yourself, just send me the new filenames plus your
rules.json contents (if we're still talking), and I'll write the new rule for you - you'll only
need to paste it in.

WHAT'S IN config.json, AND WHEN YOU'D NEED IT
Just the baby's birth date and the "how many months per folder" settings. Normally you'd change
these straight from the top of this window and click "Save Settings" - no need to hand-edit this
file or bring an AI into it.

A NOTE ON SAFETY
- This tool always copies, never deletes or moves anything in your source folder - safe to run
  as many times as you like
- Run a Preview before the real thing, just to be sure
- A broken rules.json (bad JSON syntax, a missing comma) won't touch your photos - worst case,
  the rule just won't work, or the app won't start. Delete the file and reopen the app to get a
  fresh default one.

A NOTE ON THE LANGUAGE SWITCH
The generated folder names (e.g. "5.5月(2024.10.16-2024.11.15)") always keep this same format,
regardless of which display language you're using. That's intentional - it means the same batch
of photos always lands in the same folder no matter which language you sort them in, rather than
splitting into two separate sets of folders depending on the language toggle.
"""


def T(key, **kwargs):
    """取当前语言下的文案, 支持 .format() 占位符替换"""
    text = UI_STRINGS.get(CURRENT_LANG, UI_STRINGS['zh']).get(key, key)
    return text.format(**kwargs) if kwargs else text


UI_STRINGS = {
    'zh': {
        'window_title': f'萌迹 v{APP_VERSION}',
        'lang_toggle': '🌐 English',
        'src_label': '待整理文件夹:',
        'dst_label': '目标根文件夹:',
        'browse': '浏览...',
        'cfg_frame_title': '分档规则(会自动保存到 config.json)',
        'birth_label': '出生日期(YYYY-MM-DD):',
        'stage_prefix': '前',
        'stage_mid': '个月每月一档,',
        'interval_prefix': '之后每',
        'interval_suffix': '个月一档',
        'save_config': '保存配置',
        'reload_rules': '重新加载规则文件(rules.json)',
        'dep_exif': '图片EXIF校验: ',
        'dep_video': '视频元数据校验: ',
        'dep_on': '已启用',
        'dep_off_pillow': '未启用(需 pip install pillow)',
        'dep_off_hachoir': '未启用(需 pip install hachoir)',
        'btn_preview': '① 预览(不复制任何文件)',
        'btn_organize': '② 开始整理(正式复制文件)',
        'btn_help': '📖 使用说明',
        'btn_check_update': '🔄 检查更新',
        'btn_check_update_highlight': '🔥 发现新版本! 点击更新',
        'update_checking_title': '检查更新',
        'update_check_fail': '无法连接到更新服务器, 请检查网络后重试。',
        'update_found_title': '🎉 发现新版本',
        'update_found_msg': '当前版本: v{current}\n最新版本: v{latest}\n\n更新内容:\n{changelog}',
        'update_found_pwd_note': '🔑 解压密码: {pwd}\n点击下方"前往下载"后, 密码会自动复制到剪贴板, 解压时直接粘贴(Ctrl+V)即可',
        'btn_update_download': '⬇ 前往下载',
        'btn_update_skip': '跳过此版本',
        'btn_update_later': '下次再说',
        'update_is_latest': '当前已是最新版本(v{current})。',
        'update_open_fail': '无法自动打开下载链接, 请手动复制地址在浏览器打开:\n{url}',
        'pwd_copied_title': '密码已复制',
        'pwd_copied_msg': '✅ 解压密码 {pwd} 已经复制到剪贴板了\n\n接下来会为你打开下载页面, 下载完成后解压时直接粘贴(Ctrl+V)这个密码即可。',
        'result_label': '处理结果(双击某一行可预览缩略图 / 查看文件信息):',
        'col_filename': '文件名',
        'col_result': '识别结果',
        'col_folder': '目标文件夹',
        'help_title': '使用说明',
        'copy_prompt_btn': '复制下方AI提示词模板',
        'close_btn': '关闭',
        'copied_title': '已复制',
        'copied_msg': '提示词模板已复制到剪贴板, 可以直接粘贴给AI使用了。\n记得把里面的【】部分换成你的实际内容(文件名例子 + rules.json当前内容)。',
        'error_title': '错误',
        'err_src': '请先选择正确的【待整理文件夹】路径',
        'err_dst': '请先填写【目标根文件夹】路径',
        'err_cfg': '分档规则里的出生日期/月数格式不对, 请检查',
        'err_cfg_save': '出生日期需为 YYYY-MM-DD 格式, 分档月数需为整数',
        'confirm_title': '确认',
        'confirm_msg': '即将开始正式复制文件(不会删除或移动源文件)。\n建议先做过一次【预览】并确认无误后再继续。\n\n是否继续?',
        'saved_title': '已保存',
        'saved_msg': '配置已保存到:\n{path}',
        'reloaded_title': '已重新加载',
        'reloaded_msg': '已从下列文件重新加载 {n} 条识别规则:\n{path}',
        'done_title': '完成',
        'error_run_title': '出错了',
        'error_run_prefix': '发生错误: ',
        'cannot_preview_type': '该文件类型暂不支持缩略图预览\n(视频/未识别的图片格式)',
        'cannot_preview_err': '无法预览此图片:\n{err}',
        'footer': ('作者: c和弦 (cchord)  |  本项目已在 GitHub 开源, 原版完全免费、无广告、无内购。\n'
                   '如果你拿到的版本提示付费、弹广告或要求捆绑安装其他软件, 那不是原版, 请提高警惕。'),
        'recognized_as': '识别为 {date}',
        'dup_skip': '  [内容与已有文件重复,已跳过]',
        'incr_skip_status': '增量:此前已成功处理,本次跳过',
        'incr_skip_row': '增量跳过(之前处理过)',
        'cannot_recognize': '无法识别日期({note})',
        'guess_note': '⚠通用规则猜测,建议核对',
        'mismatch_note': '⚠与文件元数据日期不一致(元数据为{date}),建议核对',
        'meta_fallback_note': '文件名无法识别,已改用文件元数据日期',
        'mtime_fallback_note': '⚠文件名和元数据均无法识别,已改用文件"修改时间"兜底,建议核对',
        'both_fail_note': '文件名、元数据、文件修改时间均无法识别出日期',
        'guess_rule_name': '通用规则猜测(未匹配任何已知格式)',
        'epoch_suffix': '(毫秒时间戳换算)',
        'epoch_s_suffix': '(秒级时间戳换算)',
        'mode_preview': '【预览模式,以上均未真正复制文件, 仅供核对】',
        'mode_real': '【已按上述结果完成复制】',
        'will_copy': '将会复制',
        'actually_copy': '实际复制',
        'summary': ('{mode}\n\n'
                    '共扫描到文件: {total}\n'
                    '增量跳过(此前已处理过): {incr}\n'
                    '本次成功识别并归类: {ok}\n'
                    '  其中靠文件名识别(主目录): {ok_filename}\n'
                    "  其中靠元数据识别(归入'{metadata_folder}'子文件夹): {ok_metadata}\n"
                    "  其中靠文件修改时间兜底(归入'{mtime_folder}'子文件夹): {ok_mtime}\n"
                    '  其中内容重复已跳过: {dup}\n'
                    '  其中{copy_verb}: {copied}\n'
                    "无法识别日期(已归入'{manual_folder}'): {manual}\n"
                    '涉及新建文件夹数: {newf}\n'
                    '校验: {incr} + {ok} + {manual} = {check}  (应等于总数 {total})\n\n'
                    '详细清单已保存到:\n{log_path}'),
        'csv_headers': ['原文件路径', '解析结果', '目标文件夹', '文件夹是否新建', '处理结果'],
        'csv_new': '新建', 'csv_existing': '已有', 'csv_copy': '复制', 'csv_skip_dup': '跳过(重复)',
        'help_text': HELP_TEXT_ZH,
        'ai_prompt': AI_PROMPT_TEMPLATE_ZH,
    },
    'en': {
        'window_title': f'Baby Steps v{APP_VERSION}',
        'lang_toggle': '🌐 中文',
        'src_label': 'Folder to sort:',
        'dst_label': 'Destination folder:',
        'browse': 'Browse...',
        'cfg_frame_title': 'Sorting rules (auto-saved to config.json)',
        'birth_label': 'Birth date (YYYY-MM-DD):',
        'stage_prefix': 'One folder per month for the first',
        'stage_mid': 'months, then',
        'interval_prefix': '',
        'interval_suffix': 'months per folder after that',
        'save_config': 'Save Settings',
        'reload_rules': 'Reload Rules File (rules.json)',
        'dep_exif': 'Photo EXIF check: ',
        'dep_video': 'Video metadata check: ',
        'dep_on': 'enabled',
        'dep_off_pillow': 'disabled (run: pip install pillow)',
        'dep_off_hachoir': 'disabled (run: pip install hachoir)',
        'btn_preview': '1. Preview (copies nothing)',
        'btn_organize': '2. Start Organising (copies files)',
        'btn_help': '📖 User Guide',
        'btn_check_update': '🔄 Check for Updates',
        'btn_check_update_highlight': '🔥 Update Available! Click Here',
        'update_checking_title': 'Check for Updates',
        'update_check_fail': 'Could not reach the update server. Please check your network and try again.',
        'update_found_title': '🎉 New Version Available',
        'update_found_msg': 'Current version: v{current}\nLatest version: v{latest}\n\nChangelog:\n{changelog}',
        'update_found_pwd_note': '🔑 Extraction password: {pwd}\nClicking "Go to Download" below will copy it to your clipboard — just paste (Ctrl+V) when prompted.',
        'btn_update_download': '⬇ Go to Download',
        'btn_update_skip': 'Skip This Version',
        'btn_update_later': 'Remind Me Later',
        'update_is_latest': 'You already have the latest version (v{current}).',
        'update_open_fail': 'Could not open the download link automatically. Please copy it into your browser:\n{url}',
        'pwd_copied_title': 'Password Copied',
        'pwd_copied_msg': '✅ The extraction password {pwd} has been copied to your clipboard.\n\nThe download page will open next — just paste (Ctrl+V) it when you extract the file.',
        'result_label': 'Results (double-click a row for a thumbnail / file info):',
        'col_filename': 'Filename',
        'col_result': 'Result',
        'col_folder': 'Destination Folder',
        'help_title': 'User Guide',
        'copy_prompt_btn': 'Copy AI Prompt Template',
        'close_btn': 'Close',
        'copied_title': 'Copied',
        'copied_msg': "The prompt template's been copied to your clipboard, ready to paste into an AI chat.\n"
                      "Just fill in the two blanks marked with [ ] - the new filenames, and your current rules.json.",
        'error_title': 'Error',
        'err_src': 'Please choose a valid "Folder to sort" first',
        'err_dst': 'Please fill in the "Destination folder" first',
        'err_cfg': 'The birth date / month settings look wrong - please check them',
        'err_cfg_save': 'Birth date must be in YYYY-MM-DD format, and the month settings must be whole numbers',
        'confirm_title': 'Confirm',
        'confirm_msg': "This will start copying files for real (nothing in the source folder gets deleted "
                       "or moved).\nIt's worth running a Preview first if you haven't already.\n\nContinue?",
        'saved_title': 'Saved',
        'saved_msg': 'Settings saved to:\n{path}',
        'reloaded_title': 'Reloaded',
        'reloaded_msg': 'Loaded {n} rule(s) from:\n{path}',
        'done_title': 'Done',
        'error_run_title': 'Something went wrong',
        'error_run_prefix': 'Error: ',
        'cannot_preview_type': "This file type can't be previewed yet\n(video, or an unrecognised image format)",
        'cannot_preview_err': "Couldn't preview this image:\n{err}",
        'footer': ('Author: c和弦 (cchord)  |  Open-sourced on GitHub - the original is completely free, '
                   'with no ads and no in-app purchases.\n'
                   'If your copy asks for payment, shows ads, or tries to bundle other software, '
                   "it isn't the original - please be careful."),
        'recognized_as': 'Dated {date}',
        'dup_skip': '  [duplicate content - skipped]',
        'incr_skip_status': 'Incremental: already processed successfully before, skipped this run',
        'incr_skip_row': 'Skipped (already processed)',
        'cannot_recognize': "Couldn't work out the date ({note})",
        'guess_note': '⚠ Guessed - please double-check',
        'mismatch_note': "⚠ Doesn't match the file's own metadata (metadata says {date}) - please double-check",
        'meta_fallback_note': "Couldn't read the filename, used the file's metadata date instead",
        'mtime_fallback_note': "⚠ Couldn't read filename or metadata - fell back to the file's "
                                "'last modified' timestamp, please double-check",
        'both_fail_note': "Couldn't get a date from the filename, metadata, or the file's modified time",
        'guess_rule_name': "Best guess (didn't match any known format)",
        'epoch_suffix': ' (converted from a millisecond timestamp)',
        'epoch_s_suffix': ' (converted from a second-level timestamp)',
        'mode_preview': '[PREVIEW MODE - nothing above has actually been copied, for review only]',
        'mode_real': '[Copying complete, as shown above]',
        'will_copy': 'will be copied',
        'actually_copy': 'actually copied',
        'summary': ('{mode}\n\n'
                    'Files scanned: {total}\n'
                    'Skipped (processed before): {incr}\n'
                    'Recognised and sorted this run: {ok}\n'
                    '  via filename (main folder): {ok_filename}\n'
                    "  via metadata (put into the '{metadata_folder}' subfolder): {ok_metadata}\n"
                    "  via file-modified-time fallback (put into the '{mtime_folder}' subfolder): {ok_mtime}\n"
                    '  of which duplicates skipped: {dup}\n'
                    '  of which {copy_verb}: {copied}\n'
                    "Couldn't date (moved to '{manual_folder}'): {manual}\n"
                    'New folders created: {newf}\n'
                    'Check: {incr} + {ok} + {manual} = {check}  (should equal the total, {total})\n\n'
                    'Full log saved to:\n{log_path}'),
        'csv_headers': ['Original path', 'Result', 'Destination folder', 'New folder?', 'Action'],
        'csv_new': 'new', 'csv_existing': 'existing', 'csv_copy': 'copied', 'csv_skip_dup': 'skipped (duplicate)',
        'help_text': HELP_TEXT_EN,
        'ai_prompt': AI_PROMPT_TEMPLATE_EN,
    },
}

CURRENT_LANG = 'zh'




def load_json(path, default):
    """读取JSON配置, 不存在或损坏时写入默认值并返回默认值"""
    if os.path.exists(path):
        try:
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    try:
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(default, f, ensure_ascii=False, indent=2)
    except Exception:
        pass
    return default


CONFIG = load_json(CONFIG_PATH, DEFAULT_CONFIG)
RULES = load_json(RULES_PATH, DEFAULT_RULES)


def get_birth():
    try:
        return datetime.strptime(CONFIG.get('birth_date', '2024-06-15'), '%Y-%m-%d').date()
    except ValueError:
        return date(2024, 6, 15)


def get_tz():
    return timezone(timedelta(hours=CONFIG.get('timezone_hours', 8)))


# ------------------------- 月份文件夹规则计算(出生日期/分档可配置) -------------------------

def add_months(d: date, n: int) -> date:
    total = d.month - 1 + n
    year = d.year + total // 12
    month = total % 12 + 1
    day = min(d.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def formula_folder_for_date(d: date, birth: date, stage1_months: int, interval_months: int):
    """
    按出生日期/分档规则, 计算某日期理论上应属于哪个文件夹。
    stage1_months: 前多少个月按'每月一档'
    interval_months: 之后每隔多少个月一档
    """
    if d < birth:
        return f"0.出生前({birth:%Y.%m.%d}以前)", None, birth - timedelta(days=1)

    n = 1
    prev_end = None
    while True:
        if n <= stage1_months:
            end = add_months(birth, n)
            start = birth if n == 1 else prev_end + timedelta(days=1)
            label = f"{n}.{n}月({start:%Y.%m.%d}-{end:%Y.%m.%d})"
        else:
            k = n - stage1_months
            end_month = stage1_months + interval_months * k
            end = add_months(birth, end_month)
            start = prev_end + timedelta(days=1)
            label = f"{n}.{end_month}月({start:%Y.%m.%d}-{end:%Y.%m.%d})"
        if d <= end:
            return label, start, end
        prev_end = end
        n += 1
        if n > 2000:  # 保护性熔断, 理论上不会走到这里
            return MANUAL_REVIEW_FOLDER, None, None


_RANGE_RE = re.compile(r'\((\d{4})\.(\d{2})\.(\d{2})-(\d{4})\.(\d{2})\.(\d{2})\)')
_BEFORE_RE = re.compile(r'\((\d{4})\.(\d{2})\.(\d{2})以前\)')


def parse_existing_folder(name: str):
    m = _BEFORE_RE.search(name)
    if m:
        y, mo, da = map(int, m.groups())
        cutoff = date(y, mo, da)
        return None, cutoff - timedelta(days=1)
    m = _RANGE_RE.search(name)
    if m:
        y1, m1, d1, y2, m2, d2 = map(int, m.groups())
        return date(y1, m1, d1), date(y2, m2, d2)
    return None


def scan_existing_folders(target_root: str):
    ranges = []
    if not os.path.isdir(target_root):
        return ranges
    for name in os.listdir(target_root):
        full = os.path.join(target_root, name)
        if not os.path.isdir(full):
            continue
        parsed = parse_existing_folder(name)
        if parsed:
            start, end = parsed
            ranges.append((start, end, name))
    return ranges


def find_target_folder(d: date, existing_ranges: list, birth, stage1_months, interval_months):
    for start, end, name in existing_ranges:
        if (start is None or start <= d) and d <= end:
            return name, False
    label, _, _ = formula_folder_for_date(d, birth, stage1_months, interval_months)
    return label, True


# ------------------------- 文件名规则解析(从 rules.json 加载) -------------------------

def parse_filename_by_rules(filename: str, lang: str = 'zh'):
    S = UI_STRINGS.get(lang, UI_STRINGS['zh'])
    for rule in RULES:
        pat = rule.get('pattern')
        rtype = rule.get('type')
        if not pat or not rtype:
            continue
        try:
            m = re.match(pat, filename)
        except re.error:
            continue
        if not m:
            continue
        try:
            if rtype == 'digits8':
                d = datetime.strptime(m.group(1)[:8], '%Y%m%d').date()
                return d, rule.get('name', '')
            elif rtype == 'digits8_dashed':
                y, mo, da = m.group(1), m.group(2), m.group(3)
                d = date(int(y), int(mo), int(da))
                return d, rule.get('name', '')
            elif rtype == 'epoch_ms':
                ts = int(m.group(1))
                dt = datetime.fromtimestamp(ts / 1000, tz=get_tz())
                return dt.date(), rule.get('name', '') + S['epoch_suffix']
            elif rtype == 'epoch_s':
                ts = int(m.group(1))
                dt = datetime.fromtimestamp(ts, tz=get_tz())
                return dt.date(), rule.get('name', '') + S['epoch_s_suffix']
        except (ValueError, OverflowError, OSError):
            continue
    return None, None


def _epoch_to_date(ts: int, unit_divisor: int, min_date: date, max_date: date):
    """把一个数字尝试当unix时间戳(unit_divisor=1为秒, 1000为毫秒)转成日期, 超出合理范围就放弃"""
    try:
        dt = datetime.fromtimestamp(ts / unit_divisor, tz=get_tz())
    except (ValueError, OverflowError, OSError):
        return None
    d = dt.date()
    if min_date <= d <= max_date:
        return d
    return None


def generic_guess_date(filename: str, min_date: date, max_date: date):
    """
    兜底: 文件名规则(rules.json)一条都没匹配上时最后的猜测手段。
    不针对具体品牌前缀, 而是通用扫描文件名里的数字串, 按"数字位数"猜测编码方式:
        8位  -> 当作 YYYYMMDD 日期
        13位 -> 当作毫秒级Unix时间戳(部分安卓相册/微信常见)
        10位 -> 当作秒级Unix时间戳(部分监控摄像头/IoT设备常见)
    只有换算出的日期落在合理范围内才采用, 避免把无关数字(如像素尺寸、序列号)误判成日期。
    """
    for m in re.finditer(r'(\d{13})', filename):
        d = _epoch_to_date(int(m.group(1)), 1000, min_date, max_date)
        if d:
            return d
    for m in re.finditer(r'(\d{8})', filename):
        try:
            d = datetime.strptime(m.group(1), '%Y%m%d').date()
        except ValueError:
            continue
        if min_date <= d <= max_date:
            return d
    for m in re.finditer(r'(\d{10})', filename):
        d = _epoch_to_date(int(m.group(1)), 1, min_date, max_date)
        if d:
            return d
    return None


def parse_filename_date(filename: str, lang: str = 'zh'):
    """返回 (日期或None, 说明文字, 是否是兜底猜测)"""
    S = UI_STRINGS.get(lang, UI_STRINGS['zh'])
    d, rule_name = parse_filename_by_rules(filename, lang)
    if d is not None:
        return d, rule_name, False
    guess = generic_guess_date(filename, date(2000, 1, 1), date.today() + timedelta(days=2))
    if guess is not None:
        return guess, S['guess_rule_name'], True
    return None, None, False


# ------------------------- EXIF / 视频元数据(可选校验) -------------------------

def get_exif_date(path: str):
    if not PIL_AVAILABLE:
        return None
    try:
        img = Image.open(path)
        exif = img.getexif()
        if not exif:
            return None
        # DateTimeOriginal(拍摄时间, 36867)/DateTimeDigitized(36868) 绝大多数机型都存在
        # "Exif子IFD"(tag 0x8769)里, 不在最外层IFD, 必须用 get_ifd() 单独取一次;
        # 拿不到时再退回最外层的 DateTime(306), 这个字段更接近"文件最后修改时间", 没那么可靠, 放最后兜底。
        raw = None
        try:
            exif_sub = exif.get_ifd(0x8769)
            raw = exif_sub.get(36867) or exif_sub.get(36868)
        except Exception:
            raw = None
        if not raw:
            raw = exif.get(306)
        if not raw:
            return None
        dt = datetime.strptime(str(raw).strip(), '%Y:%m:%d %H:%M:%S')
        return dt.date()
    except Exception:
        return None


def get_video_meta_date(path: str):
    if not HACHOIR_AVAILABLE:
        return None
    try:
        parser = createParser(path)
        if not parser:
            return None
        with parser:
            metadata = extractMetadata(parser)
        if not metadata:
            return None
        val = metadata.get('creation_date')
        if isinstance(val, datetime):
            return val.date()
    except Exception:
        return None
    return None


def get_metadata_date(path: str):
    ext = os.path.splitext(path)[1].lower()
    if ext in IMAGE_EXTS:
        return get_exif_date(path)
    return get_video_meta_date(path)


META_AVAILABLE = PIL_AVAILABLE or HACHOIR_AVAILABLE


def resolve_date_for_file(path: str, filename: str, lang: str = 'zh'):
    """
    综合文件名规则解析 + 元数据交叉校验, 得到最终日期和备注说明。
    返回: (最终日期或None, 备注字符串, 日期来源)
    日期来源 source 取值:
        'filename' —— 靠文件名规则(含通用猜测)识别出来的, 最可信, 走主分类区
        'metadata' —— 文件名识别失败, 靠图片EXIF/视频元数据识别出来的, 单独归到"元数据识别"文件夹
        'mtime'    —— 文件名和元数据都失败, 靠文件系统"修改时间"兜底的, 单独归到"文件修改时间兜底"文件夹
        None       —— 什么都识别不出来, 归到"待人工确认"文件夹
    """
    S = UI_STRINGS.get(lang, UI_STRINGS['zh'])
    fname_date, rule_name, is_guess = parse_filename_date(filename, lang)
    meta_date = get_metadata_date(path) if META_AVAILABLE else None

    notes = []
    if fname_date is not None:
        final_date = fname_date
        source = 'filename'
        if rule_name:
            notes.append(rule_name)
        if is_guess:
            notes.append(S['guess_note'])
        if meta_date is not None and abs((meta_date - fname_date).days) > 1:
            notes.append(S['mismatch_note'].format(date=meta_date.isoformat()))
    elif meta_date is not None:
        final_date = meta_date
        source = 'metadata'
        notes.append(S['meta_fallback_note'])
    else:
        mtime_date = get_mtime_date(path)
        if mtime_date is not None:
            final_date = mtime_date
            source = 'mtime'
            notes.append(S['mtime_fallback_note'])
        else:
            final_date = None
            source = None
            notes.append(S['both_fail_note'])

    return final_date, '  '.join(notes), source


# ------------------------- 内容去重(与v1相同逻辑) -------------------------

def file_hash(path: str, block_size: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        while True:
            chunk = f.read(block_size)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def resolve_dest_path(src_path: str, dest_dir: str, filename: str, placed_registry: dict):
    base, ext = os.path.splitext(filename)
    src_size = os.path.getsize(src_path)
    src_hash_cache = {}

    def get_src_hash():
        if 'v' not in src_hash_cache:
            src_hash_cache['v'] = file_hash(src_path)
        return src_hash_cache['v']

    reg = placed_registry.setdefault(dest_dir, {})
    i = 0
    while True:
        candidate_name = filename if i == 0 else f"{base}_{i}{ext}"
        candidate_path = os.path.join(dest_dir, candidate_name)

        if candidate_path in reg:
            cand_size, cand_hash_fn = reg[candidate_path]
        elif os.path.exists(candidate_path):
            cand_size = os.path.getsize(candidate_path)
            cand_hash_fn = (lambda p=candidate_path: file_hash(p))
            reg[candidate_path] = (cand_size, cand_hash_fn)
        else:
            reg[candidate_path] = (src_size, (lambda p=src_path: file_hash(p)))
            return candidate_path, 'copy'

        if cand_size == src_size and cand_hash_fn() == get_src_hash():
            return None, 'skip_duplicate'
        i += 1


# ------------------------- 系统垃圾文件过滤 -------------------------

def is_junk_file(filename: str) -> bool:
    low = filename.lower()
    if low in JUNK_FILENAMES:
        return True
    if low.startswith('._'):  # mac资源分叉文件
        return True
    return False


def collect_files(src_root: str):
    files = []
    for dirpath, dirnames, filenames in os.walk(src_root):
        dirnames[:] = [d for d in dirnames if d.lower() not in JUNK_DIR_NAMES]
        for fn in filenames:
            if is_junk_file(fn):
                continue
            files.append(os.path.join(dirpath, fn))
    return files


# ------------------------- 增量索引(跳过上次已成功处理过的文件) -------------------------

def load_index(target_root):
    path = os.path.join(target_root, INDEX_FILENAME)
    if os.path.exists(path):
        try:
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_index(target_root, index):
    path = os.path.join(target_root, INDEX_FILENAME)
    try:
        os.makedirs(target_root, exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(index, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


# ------------------------- 主处理逻辑 -------------------------

def run_organize(src_root, target_root, dry_run, birth, stage1_months, interval_months,
                  row_callback, done_callback, lang='zh'):
    S = UI_STRINGS.get(lang, UI_STRINGS['zh'])

    meta_root = os.path.join(target_root, METADATA_FOLDER)
    mtime_root = os.path.join(target_root, MTIME_FALLBACK_FOLDER)

    # 共享的"已知月份区间"列表: 主目录 / 元数据识别子目录 / 文件修改时间兜底子目录,
    # 三边只要有一边已经存在某个月份文件夹(哪怕是你手动改过名字/合并过区间的),
    # 另外两边新建同一档时都会直接复用同一个文件夹名字和日期区间, 不会因为物理位置不同
    # 而各算各的、导致"3.3月"在三个地方对应的日期范围各不相同。
    existing_ranges = scan_existing_folders(target_root)
    existing_ranges += scan_existing_folders(meta_root)
    existing_ranges += scan_existing_folders(mtime_root)

    all_files = collect_files(src_root)
    total = len(all_files)

    ok_filename_count = 0
    ok_metadata_count = 0
    ok_mtime_count = 0
    manual_count = 0
    dup_count = 0
    incr_skip_count = 0
    dir_exists_cache = {}  # dest_dir -> 这次运行开始前是否已经存在, 用来判断"新建"以及去重计新建文件夹数
    new_dirs_created = set()
    rows = []
    placed_registry = {}
    index = load_index(target_root)

    for path in all_files:
        fn = os.path.basename(path)

        try:
            st = os.stat(path)
            f_size, f_mtime = st.st_size, st.st_mtime
        except OSError:
            f_size, f_mtime = None, None

        key = os.path.abspath(path)
        cached = index.get(key)
        if cached and f_size is not None and cached.get('size') == f_size \
                and abs(cached.get('mtime', -1) - f_mtime) < 1:
            incr_skip_count += 1
            folder_hint = cached.get('dest_folder', '')
            rows.append([path, S['incr_skip_status'], folder_hint, S['csv_existing'], S['incr_skip_row']])
            row_callback(path, S['incr_skip_row'], folder_hint)
            continue

        d, note, source = resolve_date_for_file(path, fn, lang)

        if d is None:
            dest_root = target_root
            target_folder_name = MANUAL_REVIEW_FOLDER
            manual_count += 1
            status = S['cannot_recognize'].format(note=note)
        else:
            target_folder_name, is_new_label = find_target_folder(
                d, existing_ranges, birth, stage1_months, interval_months)
            if is_new_label:
                parsed = parse_existing_folder(target_folder_name)
                if parsed:
                    existing_ranges.append((parsed[0], parsed[1], target_folder_name))

            if source == 'filename':
                dest_root = target_root
                ok_filename_count += 1
            elif source == 'metadata':
                dest_root = meta_root
                ok_metadata_count += 1
            else:  # 'mtime'
                dest_root = mtime_root
                ok_mtime_count += 1
            status = S['recognized_as'].format(date=d.isoformat()) + (f'  {note}' if note else '')

        dest_dir = os.path.join(dest_root, target_folder_name)

        if dest_dir in dir_exists_cache:
            already_existed = dir_exists_cache[dest_dir]
        else:
            already_existed = os.path.isdir(dest_dir)
            dir_exists_cache[dest_dir] = already_existed
        need_new = not already_existed
        if need_new:
            new_dirs_created.add(dest_dir)
            dir_exists_cache[dest_dir] = True  # 这次运行里第一个文件已经会把它建出来, 后面同目录的文件按"已有"算

        if not dry_run:
            os.makedirs(dest_dir, exist_ok=True)

        dest_path, action = resolve_dest_path(path, dest_dir, fn, placed_registry)

        if action == 'skip_duplicate':
            dup_count += 1
            status += S['dup_skip']
        elif not dry_run:
            shutil.copy2(path, dest_path)

        if not dry_run and action in ('copy', 'skip_duplicate') and f_size is not None:
            index[key] = {'size': f_size, 'mtime': f_mtime,
                           'dest_folder': os.path.relpath(dest_dir, target_root)}

        rows.append([path, status, os.path.relpath(dest_dir, target_root),
                     S['csv_new'] if need_new else S['csv_existing'],
                     S['csv_skip_dup'] if action == 'skip_duplicate' else S['csv_copy']])
        row_callback(path, status, os.path.relpath(dest_dir, target_root))

    if not dry_run:
        save_index(target_root, index)

    os.makedirs(target_root, exist_ok=True)
    log_path = os.path.join(target_root, f"整理日志_{datetime.now():%Y%m%d_%H%M%S}.csv")
    with open(log_path, 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.writer(f)
        w.writerow(S['csv_headers'])
        w.writerows(rows)

    ok_total = ok_filename_count + ok_metadata_count + ok_mtime_count
    mode_text = S['mode_preview'] if dry_run else S['mode_real']
    copy_verb = S['will_copy'] if dry_run else S['actually_copy']
    summary = S['summary'].format(
        mode=mode_text, total=total, incr=incr_skip_count, ok=ok_total, dup=dup_count,
        copy_verb=copy_verb, copied=ok_total - dup_count,
        ok_filename=ok_filename_count, ok_metadata=ok_metadata_count, ok_mtime=ok_mtime_count,
        metadata_folder=METADATA_FOLDER, mtime_folder=MTIME_FALLBACK_FOLDER,
        manual_folder=MANUAL_REVIEW_FOLDER,
        manual=manual_count, newf=len(new_dirs_created),
        check=incr_skip_count + ok_total + manual_count, log_path=log_path,
    )
    done_callback(summary, log_path)


# ------------------------- 图形界面 -------------------------

class App:
    def __init__(self, root):
        self.root = root
        self.lang = 'zh'
        self.row_paths = {}

        # ---- 顶部工具条(放在最顶端, 专属背景色, 保证第一眼就能看到): 三个按钮统一风格
        # (实心蓝底白字), 从左到右依次是 "使用说明" / "检查更新" 这两个次要功能按钮,
        # 最右边是语言切换按钮 ----
        lang_bar = tk.Frame(root, bg='#e8f0fe')
        lang_bar.pack(fill='x', side='top')

        topbar_btn_style = dict(
            font=('Segoe UI', 10, 'bold'), bg='#4a86e8', fg='white',
            activebackground='#3a6fc4', activeforeground='white',
            relief='flat', padx=14, pady=4, cursor='hand2')
        self.topbar_btn_style_normal = topbar_btn_style
        # 有新版本可更新时, "检查更新"按钮切换成这套显眼的橙红色样式, 鼓励用户点击更新
        self.topbar_btn_style_highlight = dict(
            font=('Segoe UI', 10, 'bold'), bg='#e64a19', fg='white',
            activebackground='#c2340d', activeforeground='white',
            relief='flat', padx=14, pady=4, cursor='hand2')

        self.lang_btn = tk.Button(lang_bar, text='', command=self.toggle_language, **topbar_btn_style)
        self.lang_btn.pack(side='right', padx=10, pady=6)

        # update_available: 是否已知存在比当前更新的版本(不管是不是被用户"跳过"过),
        # 按钮样式/文案要如实反映这个状态, 起到"鼓励更新"的作用
        self.update_available = False
        self.btn_check_update = tk.Button(lang_bar, command=lambda: self.check_update(silent=False), **topbar_btn_style)
        self.btn_check_update.pack(side='right', padx=(0, 8), pady=6)

        self.btn_help = tk.Button(lang_bar, command=self.show_help, **topbar_btn_style)
        self.btn_help.pack(side='right', padx=(0, 6), pady=6)

        top = tk.Frame(root)
        top.pack(fill='x', padx=10, pady=8)

        self.lbl_src = tk.Label(top)
        self.lbl_src.grid(row=0, column=0, sticky='w')
        self.src_var = tk.StringVar()
        tk.Entry(top, textvariable=self.src_var, width=60).grid(row=0, column=1, padx=5, sticky='w')
        self.btn_browse_src = tk.Button(top, command=self.choose_src)
        self.btn_browse_src.grid(row=0, column=2)

        self.lbl_dst = tk.Label(top)
        self.lbl_dst.grid(row=1, column=0, sticky='w', pady=5)
        self.dst_var = tk.StringVar(value="")
        tk.Entry(top, textvariable=self.dst_var, width=60).grid(row=1, column=1, padx=5, sticky='w', pady=5)
        self.btn_browse_dst = tk.Button(top, command=self.choose_dst)
        self.btn_browse_dst.grid(row=1, column=2)

        self.cfg = tk.LabelFrame(root)
        self.cfg.pack(fill='x', padx=10, pady=(0, 8))

        self.lbl_birth = tk.Label(self.cfg)
        self.lbl_birth.grid(row=0, column=0, sticky='w', padx=5, pady=5)
        self.birth_var = tk.StringVar(value=CONFIG.get('birth_date', '2024-06-15'))
        tk.Entry(self.cfg, textvariable=self.birth_var, width=14).grid(row=0, column=1, sticky='w')

        self.lbl_stage_prefix = tk.Label(self.cfg)
        self.lbl_stage_prefix.grid(row=0, column=2, sticky='e', padx=(20, 2), pady=5)
        self.stage1_var = tk.StringVar(value=str(CONFIG.get('monthly_stage_months', 12)))
        tk.Entry(self.cfg, textvariable=self.stage1_var, width=4, justify='center').grid(row=0, column=3, sticky='w')
        self.lbl_stage_mid = tk.Label(self.cfg)
        self.lbl_stage_mid.grid(row=0, column=4, sticky='w')

        self.lbl_interval_prefix = tk.Label(self.cfg)
        self.lbl_interval_prefix.grid(row=0, column=5, sticky='e', padx=(15, 2))
        self.interval_var = tk.StringVar(value=str(CONFIG.get('interval_after_months', 6)))
        tk.Entry(self.cfg, textvariable=self.interval_var, width=4, justify='center').grid(row=0, column=6, sticky='w')
        self.lbl_interval_suffix = tk.Label(self.cfg)
        self.lbl_interval_suffix.grid(row=0, column=7, sticky='w')

        self.btn_save_cfg = tk.Button(self.cfg, command=self.save_config)
        self.btn_save_cfg.grid(row=0, column=8, padx=15)
        self.btn_reload_rules = tk.Button(self.cfg, command=self.reload_rules)
        self.btn_reload_rules.grid(row=0, column=9, padx=5)

        self.lbl_dep = tk.Label(root, fg='#555')
        self.lbl_dep.pack(anchor='w', padx=12)

        btn_frm = tk.Frame(root)
        btn_frm.pack(pady=8)
        self.btn_preview = tk.Button(btn_frm, width=28, command=lambda: self.start(dry_run=True))
        self.btn_preview.pack(side='left', padx=8)
        self.btn_organize = tk.Button(btn_frm, width=28, command=lambda: self.start(dry_run=False))
        self.btn_organize.pack(side='left', padx=8)

        self.lbl_result = tk.Label(root)
        self.lbl_result.pack(anchor='w', padx=10, pady=(8, 0))

        self.columns_keys = ('col_filename', 'col_result', 'col_folder')
        self.tree = ttk.Treeview(root, columns=self.columns_keys, show='headings', height=18)
        for col, w in zip(self.columns_keys, (260, 420, 260)):
            self.tree.column(col, width=w, anchor='w')
        self.tree.pack(padx=10, pady=8, fill='both', expand=True)
        self.tree.bind('<Double-1>', self.show_preview)

        self.summary_box = scrolledtext.ScrolledText(root, height=7)
        self.summary_box.pack(padx=10, pady=(0, 6), fill='x')

        # ---- 底部作者/开源/防捆绑声明 ----
        self.footer_lbl = tk.Label(root, fg='#888', font=('Microsoft YaHei UI', 8), justify='left')
        self.footer_lbl.pack(anchor='w', padx=10, pady=(0, 8))

        self.apply_language()
        self._auto_fit_window()
        # 启动后延迟1秒再静默检查更新, 避免网络请求跟窗口初始化抢时间导致界面卡顿
        self.root.after(1000, lambda: self.check_update(silent=True))

    def _auto_fit_window(self):
        """按内容实际需要的尺寸打开窗口(留一点余量), 并大致居中, 避免固定尺寸在不同电脑上裁切内容"""
        self.root.update_idletasks()
        w = self.root.winfo_reqwidth() + 30
        h = self.root.winfo_reqheight() + 30
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        w = min(w, sw - 60)
        h = min(h, sh - 80)
        x = max((sw - w) // 2, 0)
        y = max((sh - h) // 3, 0)
        self.root.geometry(f"{w}x{h}+{x}+{y}")
        self.root.minsize(min(w, 900), min(h, 560))

    # ---------- 语言切换 ----------
    def S(self):
        return UI_STRINGS.get(self.lang, UI_STRINGS['zh'])

    def toggle_language(self):
        self.lang = 'en' if self.lang == 'zh' else 'zh'
        self.apply_language()

    def apply_language(self):
        global CURRENT_LANG
        CURRENT_LANG = self.lang
        S = self.S()

        self.root.title(S['window_title'])
        self.lang_btn.config(text=S['lang_toggle'])
        self.lbl_src.config(text=S['src_label'])
        self.lbl_dst.config(text=S['dst_label'])
        self.btn_browse_src.config(text=S['browse'])
        self.btn_browse_dst.config(text=S['browse'])
        self.cfg.config(text=S['cfg_frame_title'])
        self.lbl_birth.config(text=S['birth_label'])
        self.lbl_stage_prefix.config(text=S['stage_prefix'])
        self.lbl_stage_mid.config(text=S['stage_mid'])
        self.lbl_interval_prefix.config(text=S['interval_prefix'])
        self.lbl_interval_suffix.config(text=S['interval_suffix'])
        self.btn_save_cfg.config(text=S['save_config'])
        self.btn_reload_rules.config(text=S['reload_rules'])

        dep_on_off_pil = S['dep_on'] if PIL_AVAILABLE else S['dep_off_pillow']
        dep_on_off_hachoir = S['dep_on'] if HACHOIR_AVAILABLE else S['dep_off_hachoir']
        self.lbl_dep.config(text=f"{S['dep_exif']}{dep_on_off_pil}   |   {S['dep_video']}{dep_on_off_hachoir}")

        self.btn_preview.config(text=S['btn_preview'])
        self.btn_organize.config(text=S['btn_organize'])
        self.btn_help.config(text=S['btn_help'])
        self._refresh_update_button()
        self.lbl_result.config(text=S['result_label'])

        for key in self.columns_keys:
            self.tree.heading(key, text=S[key])

        self.footer_lbl.config(text=S['footer'])

    # ---------- 配置相关 ----------
    def save_config(self):
        global CONFIG
        S = self.S()
        try:
            datetime.strptime(self.birth_var.get().strip(), '%Y-%m-%d')
            s1 = int(self.stage1_var.get().strip())
            s2 = int(self.interval_var.get().strip())
        except ValueError:
            messagebox.showerror(S['error_title'], S['err_cfg_save'])
            return
        CONFIG['birth_date'] = self.birth_var.get().strip()
        CONFIG['monthly_stage_months'] = s1
        CONFIG['interval_after_months'] = s2
        with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
            json.dump(CONFIG, f, ensure_ascii=False, indent=2)
        messagebox.showinfo(S['saved_title'], S['saved_msg'].format(path=CONFIG_PATH))

    def reload_rules(self):
        global RULES
        S = self.S()
        RULES = load_json(RULES_PATH, DEFAULT_RULES)
        messagebox.showinfo(S['reloaded_title'], S['reloaded_msg'].format(n=len(RULES), path=RULES_PATH))

    # ---------- 使用说明 ----------
    def show_help(self):
        S = self.S()
        top = tk.Toplevel(self.root)
        top.title(S['help_title'])
        top.geometry("780x660")

        box = scrolledtext.ScrolledText(top, wrap='word', font=('Microsoft YaHei UI', 10))
        box.pack(fill='both', expand=True, padx=10, pady=10)
        box.insert('end', S['help_text'])
        box.config(state='disabled')

        btn_bar = tk.Frame(top)
        btn_bar.pack(fill='x', padx=10, pady=(0, 10))
        tk.Button(btn_bar, text=S['copy_prompt_btn'], command=self.copy_ai_prompt).pack(side='left')
        tk.Button(btn_bar, text=S['close_btn'], command=top.destroy).pack(side='right')

    # ---------- 检查更新 ----------
    def _refresh_update_button(self):
        """根据 self.update_available 刷新"检查更新"按钮的文案和颜色。
        有新版本时切换成橙红色+更醒目的文案, 起到"鼓励更新"的效果;
        没有新版本(或还没检查过)时用回默认的蓝色样式。
        语言切换时也会调用这个方法, 保证切换语言不会把高亮状态重置掉。"""
        S = self.S()
        if self.update_available:
            self.btn_check_update.config(text=S['btn_check_update_highlight'], **self.topbar_btn_style_highlight)
        else:
            self.btn_check_update.config(text=S['btn_check_update'], **self.topbar_btn_style_normal)

    def _center_toplevel(self, top):
        """把弹窗定位到主窗口正中间, 而不是 Tk 默认的屏幕左上角/随机位置。
        必须在弹窗里的内容都 pack/grid 完之后再调用, 这样 winfo_reqwidth/height
        才能拿到弹窗实际需要的尺寸。"""
        top.update_idletasks()
        w = top.winfo_reqwidth()
        h = top.winfo_reqheight()
        try:
            rx, ry = self.root.winfo_rootx(), self.root.winfo_rooty()
            rw, rh = self.root.winfo_width(), self.root.winfo_height()
            x = rx + max((rw - w) // 2, 0)
            y = ry + max((rh - h) // 2, 0)
        except Exception:
            sw, sh = top.winfo_screenwidth(), top.winfo_screenheight()
            x = max((sw - w) // 2, 0)
            y = max((sh - h) // 2, 0)
        top.geometry(f"+{x}+{y}")

    def check_update(self, silent=True):
        """silent=True(启动时自动检查): 查不到/没更新都不打扰用户, 只有发现新版本才弹窗。
        silent=False(用户手动点按钮): 无论查到什么结果都给个反馈, 包括"已是最新"和"查不到"。"""
        def worker():
            info = fetch_latest_version_info()
            self.root.after(0, lambda: self._on_update_checked(info, silent))
        threading.Thread(target=worker, daemon=True).start()

    def _on_update_checked(self, info, silent):
        S = self.S()
        if not info:
            if not silent:
                messagebox.showwarning(S['update_checking_title'], S['update_check_fail'], parent=self.root)
            return

        latest = str(info.get('version', ''))
        if _version_tuple(latest) <= _version_tuple(APP_VERSION):
            self.update_available = False
            self._refresh_update_button()
            if not silent:
                messagebox.showinfo(S['update_checking_title'], S['update_is_latest'].format(current=APP_VERSION), parent=self.root)
            return

        # 只要确认存在比当前更新的版本, 就让"检查更新"按钮亮起来提醒用户,
        # 这个跟下面的弹窗是否要打扰用户是两码事(哪怕用户跳过了这个版本, 按钮也照样提醒着)
        self.update_available = True
        self._refresh_update_button()

        # 用户之前手动"跳过"过这个版本号的话, 静默的自动检查(启动时那次)就不再打扰他了;
        # 但如果是用户自己手动点了"检查更新"按钮, 说明他就是想看看, 哪怕跳过了也照样弹一次。
        if silent and CONFIG.get('skipped_version', '') == latest:
            return

        self._show_update_dialog(info, latest)

    def _show_update_dialog(self, info, latest):
        """自定义弹窗(而不是简单的 是/否): 下载 / 跳过此版本 / 下次再说, 三选一, 由用户自己决定。
        弹在主窗口正中间(而不是屏幕左上角), 密码提示单独用一个醒目的色块框出来,
        避免像之前那样混在一大段文字里被用户忽略掉。"""
        S = self.S()
        changelog = info.get('changelog', '')
        url = info.get('download_url', '')
        pwd = info.get('download_password', '')

        top = tk.Toplevel(self.root)
        top.title(S['update_found_title'])
        top.resizable(False, False)
        top.transient(self.root)
        top.grab_set()

        tk.Label(top, text=S['update_found_title'], font=('Segoe UI', 13, 'bold'),
                 fg='#2a5db0', padx=20, pady=(18, 6)).pack()

        body = S['update_found_msg'].format(current=APP_VERSION, latest=latest, changelog=changelog)
        tk.Label(top, text=body, justify='left', wraplength=440, padx=20, pady=4).pack()

        if pwd:
            # 密码提示单独放一个显眼的浅黄色警示框, 而不是接在正文后面的小字里,
            # 这是之前最容易被用户看漏的地方
            pwd_frm = tk.Frame(top, bg='#fff3cd', highlightbackground='#ffca28',
                                highlightthickness=1, bd=0)
            pwd_frm.pack(fill='x', padx=20, pady=(10, 4))
            tk.Label(pwd_frm, text=S['update_found_pwd_note'].format(pwd=pwd),
                     justify='left', wraplength=400, bg='#fff3cd', fg='#7a4b00',
                     font=('Segoe UI', 10, 'bold'), padx=12, pady=10).pack()

        def do_download():
            if pwd:
                self.root.clipboard_clear()
                self.root.clipboard_append(pwd)
                self.root.update()  # 确保弹窗关闭、程序切走焦点后剪贴板内容依然保留
            top.destroy()
            # 密码复制是这个流程里最重要也最容易被忽略的一步, 单独用一个弹窗明确告诉用户
            # "已经复制好了", 而不是指望用户自己注意到上面那行小字
            if pwd:
                messagebox.showinfo(S['pwd_copied_title'], S['pwd_copied_msg'].format(pwd=pwd), parent=self.root)
            if url:
                try:
                    webbrowser.open(url)
                except Exception:
                    messagebox.showinfo(S['update_checking_title'], S['update_open_fail'].format(url=url), parent=self.root)

        def do_skip():
            global CONFIG
            CONFIG['skipped_version'] = latest
            try:
                with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
                    json.dump(CONFIG, f, ensure_ascii=False, indent=2)
            except Exception:
                pass
            top.destroy()

        def do_later():
            top.destroy()

        btn_bar = tk.Frame(top)
        btn_bar.pack(pady=(12, 18))
        tk.Button(btn_bar, text=S['btn_update_download'], width=16, font=('Segoe UI', 10, 'bold'),
                  bg='#e64a19', fg='white', activebackground='#c2340d', activeforeground='white',
                  relief='flat', cursor='hand2', command=do_download).pack(side='left', padx=6)
        tk.Button(btn_bar, text=S['btn_update_skip'], width=14, command=do_skip).pack(side='left', padx=6)
        tk.Button(btn_bar, text=S['btn_update_later'], width=14, command=do_later).pack(side='left', padx=6)

        top.protocol("WM_DELETE_WINDOW", do_later)
        self._center_toplevel(top)

    def copy_ai_prompt(self):
        S = self.S()
        self.root.clipboard_clear()
        self.root.clipboard_append(S['ai_prompt'])
        messagebox.showinfo(S['copied_title'], S['copied_msg'])

    # ---------- 文件选择 ----------
    def choose_src(self):
        p = filedialog.askdirectory()
        if p:
            self.src_var.set(p)

    def choose_dst(self):
        p = filedialog.askdirectory()
        if p:
            self.dst_var.set(p)

    # ---------- 缩略图预览 ----------
    def show_preview(self, event):
        S = self.S()
        item = self.tree.focus()
        if not item:
            return
        path = self.row_paths.get(item)
        if not path or not os.path.exists(path):
            return
        top = tk.Toplevel(self.root)
        top.title(os.path.basename(path))
        ext = os.path.splitext(path)[1].lower()
        if PIL_AVAILABLE and ext in IMAGE_EXTS:
            try:
                img = Image.open(path)
                img.thumbnail((520, 520))
                photo = ImageTk.PhotoImage(img)
                lbl = tk.Label(top, image=photo)
                lbl.image = photo
                lbl.pack(padx=10, pady=10)
            except Exception as e:
                tk.Label(top, text=S['cannot_preview_err'].format(err=e)).pack(padx=20, pady=20)
        else:
            tk.Label(top, text=S['cannot_preview_type'], justify='left').pack(padx=20, pady=20)
        tk.Label(top, text=path, wraplength=500, fg='#555').pack(padx=10, pady=(0, 10))

    # ---------- 运行 ----------
    def start(self, dry_run):
        S = self.S()
        src = self.src_var.get().strip()
        dst = self.dst_var.get().strip()
        if not src or not os.path.isdir(src):
            messagebox.showerror(S['error_title'], S['err_src'])
            return
        if not dst:
            messagebox.showerror(S['error_title'], S['err_dst'])
            return
        try:
            birth = datetime.strptime(self.birth_var.get().strip(), '%Y-%m-%d').date()
            stage1 = int(self.stage1_var.get().strip())
            interval = int(self.interval_var.get().strip())
        except ValueError:
            messagebox.showerror(S['error_title'], S['err_cfg'])
            return

        if not dry_run:
            if not messagebox.askyesno(S['confirm_title'], S['confirm_msg']):
                return

        for i in self.tree.get_children():
            self.tree.delete(i)
        self.row_paths = {}
        self.summary_box.delete('1.0', 'end')

        lang = self.lang

        def wrapped_row_callback(path, status, folder):
            fn = os.path.basename(path)
            iid = self.tree.insert('', 'end', values=(fn, status, folder))
            self.row_paths[iid] = path

        def worker():
            try:
                run_organize(src, dst, dry_run, birth, stage1, interval,
                              wrapped_row_callback, self.finish, lang=lang)
            except Exception as e:
                S2 = UI_STRINGS.get(lang, UI_STRINGS['zh'])
                self.summary_box.insert('end', S2['error_run_prefix'] + str(e) + '\n')
                messagebox.showerror(S2['error_run_title'], str(e))

        threading.Thread(target=worker, daemon=True).start()

    def finish(self, summary, log_path):
        S = self.S()
        self.summary_box.insert('end', summary + '\n')
        messagebox.showinfo(S['done_title'], summary)


def set_app_icon(root):
    """
    设置窗口图标, 找图标文件的优先级:
    1. exe/脚本 同目录下的 icon.ico / icon.png(外部文件, 优先级最高, 方便不重新打包也能换图标)
    2. 打包进exe内部的 icon.ico / icon.png(用 --add-data 打包进去的, 不依赖任何外部文件)
    两处都找不到就悄悄跳过, 不影响启动。
    """
    candidates = []
    for name in ('icon.ico', 'icon.png'):
        candidates.append(os.path.join(SCRIPT_DIR, name))
        candidates.append(resource_path(name))

    for p in candidates:
        if not os.path.exists(p):
            continue
        try:
            if PIL_AVAILABLE:
                img = Image.open(p)
                photo = ImageTk.PhotoImage(img)
                root.iconphoto(True, photo)
                root._icon_ref = photo  # 防止被垃圾回收导致图标消失
                return
            elif p.lower().endswith('.ico'):
                root.iconbitmap(p)
                return
        except Exception:
            continue


if __name__ == '__main__':
    root = tk.Tk()
    set_app_icon(root)
    App(root)
    root.mainloop()
