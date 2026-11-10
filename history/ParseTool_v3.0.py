# -*- coding: utf-8 -*-
"""
链接解析工具 · 本地版 v3.0（模仿 ParseDock 的三步流程）
粘贴链接/整段分享文案 -> 解析 -> 选清晰度 -> 下载原视频 / 原图 / 音频

支持：抖音、B站、小红书、快手、微博、西瓜、YouTube、TikTok、Instagram、X 等
     国内外主流平台（完整清单见软件内“支持平台”按钮 / PLATFORMS.md）
依赖：yt-dlp、imageio-ffmpeg（自带 ffmpeg 二进制，免单独安装）
界面：tkinter（Python 自带）
v2 新增：内置 ffmpeg 自动合并音视频；抖音自动领访客 Cookie（失败自动改读浏览器）
"""

import os
import re
import sys
import json
import time
import random
import shutil
import tempfile
import threading
import subprocess
import http.cookiejar
import urllib.request
import urllib.parse

try:
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox
    TK_OK = True
except Exception:  # 无界面环境下只保留纯逻辑函数（方便测试）
    TK_OK = False

# ---------------------------------------------------------------- 纯逻辑部分

KNOWN_DOMAINS = (
    "douyin.com", "iesdouyin.com", "bilibili.com", "b23.tv",
    "xiaohongshu.com", "xhslink.com", "kuaishou.com", "weibo.com",
    "weibo.cn", "youtube.com", "youtu.be", "tiktok.com",
    "ixigua.com", "youku.com", "iqiyi.com", "qq.com", "acfun.cn",
)

URL_RE = re.compile(r"https?://[^\s，。；、）】\]\"'<>]+")
BV_RE = re.compile(r"\bBV[0-9A-Za-z]{10}\b")


def extract_url(text):
    """从整段分享文案里提取第一个链接；没有 http 链接时尝试识别 BV 号。"""
    if not text:
        return ""
    text = text.strip()
    links = URL_RE.findall(text)
    links = [u.rstrip(".,") for u in links]
    if links:
        for u in links:  # 优先已知平台域名
            try:
                host = urllib.parse.urlparse(u).netloc.lower()
            except Exception:
                continue
            if any(d in host for d in KNOWN_DOMAINS):
                return u
        return links[0]
    m = BV_RE.search(text)
    if m:
        return "https://www.bilibili.com/video/" + m.group(0)
    if re.match(r"^(www\.)?[a-z0-9.-]+\.[a-z]{2,}/\S+$", text, re.I):
        return "https://" + text
    return ""


def fmt_size(n):
    if not n:
        return "大小未知"
    n = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def clarity_name(h):
    if not h:
        return "未知"
    tag = ""
    if h >= 2000:
        tag = "（4K）"
    elif h >= 1400:
        tag = "（2K）"
    return f"{int(h)}p{tag}"


def build_rows(info):
    """把 yt-dlp 的 info dict 整理成可下载条目列表（纯函数，可测试）。

    每条 row: dict(kind, label, format_id, has_audio, url, ext, count)
    kind: best / video / audio / cover / images
    """
    rows = []
    if not info:
        return rows
    entries = info.get("entries") or []
    main = info
    if entries and isinstance(entries[0], dict) and entries[0].get("formats"):
        main = entries[0]

    formats = main.get("formats") or []
    if not formats and main.get("url"):
        # 单文件直链（通用解析）：info 本身就是唯一格式
        formats = [dict(main, format_id=main.get("format_id") or "0")]

    # ---- 视频：同一清晰度（高+fps）只保留质量最好的一条
    # 注意：有些来源不写 vcodec（为 None），不能当作“无视频”跳过；
    # 只有明确写 none 且无高度/视频扩展名时才不是视频。
    VIDEO_EXTS = ("mp4", "mkv", "webm", "mov", "flv", "ts", "m4v", "avi")
    best_by_key = {}
    for f in formats:
        if not f.get("url"):
            continue
        vc = f.get("vcodec")
        if vc == "none":
            continue
        if vc is None and not (f.get("height") or f.get("width")
                               or (f.get("ext") or "").lower() in VIDEO_EXTS):
            continue
        h = f.get("height") or 0
        fps = round(f.get("fps") or 0)
        key = (h, fps, f.get("ext"))
        score = (f.get("tbr") or 0, f.get("filesize") or f.get("filesize_approx") or 0)
        if key not in best_by_key or score > best_by_key[key][0]:
            best_by_key[key] = (score, f)

    vids = [v[1] for v in best_by_key.values()]
    vids.sort(key=lambda f: (f.get("height") or 0, f.get("fps") or 0,
                             f.get("tbr") or 0), reverse=True)

    if vids:
        top = vids[0]
        rows.append({
            "kind": "best",
            "label": f"视频 · 最高画质自动（{clarity_name(top.get('height'))}，自动合并音视频）",
            "format_id": None, "has_audio": True, "url": None,
        })
        for f in vids:
            # acodec 明确为 none 才是无声轨道；缺失（None）按自带音频处理，不误报
            has_audio = f.get("acodec") != "none"
            size = f.get("filesize") or f.get("filesize_approx")
            fps = round(f.get("fps") or 0)
            label = (f"视频 · {clarity_name(f.get('height')) if f.get('height') else '原始尺寸'}"
                     + (f" · {fps}fps" if fps else "")
                     + f" · {f.get('ext', '')}"
                     + f" · {fmt_size(size)}"
                     + ("" if has_audio else " · 无声轨道（下载时自动合并音频）"))
            rows.append({
                "kind": "video", "label": label,
                "format_id": f.get("format_id"), "has_audio": has_audio,
                "url": None,
            })

    # ---- 音频：保留最好的一条（acodec 缺失时用音频扩展名兜底）
    AUDIO_EXTS = ("mp3", "m4a", "aac", "opus", "ogg", "wav", "flac")
    auds = [f for f in formats
            if f.get("url") and (not f.get("vcodec") or f.get("vcodec") == "none")
            and ((f.get("acodec") and f.get("acodec") != "none")
                 or (f.get("ext") or "").lower() in AUDIO_EXTS)]
    if auds:
        auds.sort(key=lambda f: (f.get("abr") or 0), reverse=True)
        a = auds[0]
        size = a.get("filesize") or a.get("filesize_approx")
        rows.append({
            "kind": "audio",
            "label": f"音频 · 仅音频 {a.get('ext', '')} · {fmt_size(size)}",
            "format_id": a.get("format_id"), "has_audio": True, "url": None,
        })

    # ---- 封面原图：取 thumbnails 里最大的一张
    thumbs = main.get("thumbnails") or info.get("thumbnails") or []
    cover = None
    if main.get("thumbnail"):
        cover = {"url": main["thumbnail"], "width": main.get("thumbnail_width"),
                 "height": main.get("thumbnail_height")}
    for t in thumbs:
        if t.get("url") and (cover is None or
                             (t.get("width") or 0) * (t.get("height") or 0) >=
                             (cover.get("width") or 0) * (cover.get("height") or 0)):
            cover = t
    if cover and cover.get("url"):
        wh = ""
        if cover.get("width") and cover.get("height"):
            wh = f" {cover['width']}x{cover['height']}"
        rows.append({
            "kind": "cover", "label": f"图片 · 封面原图{wh}",
            "format_id": None, "has_audio": False, "url": cover["url"],
        })

    # ---- 图集原图（图文帖）：entries 里是图片直链时收集
    img_urls = collect_image_urls(info)
    if len(img_urls) > 1 or (img_urls and not vids):
        rows.append({
            "kind": "images", "label": f"图片 · 图集原图 共 {len(img_urls)} 张",
            "format_id": None, "has_audio": False, "url": None,
            "urls": img_urls, "count": len(img_urls),
        })
    return rows


IMG_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".avif", ".bmp")


def _is_img_url(u):
    if not u:
        return False
    path = urllib.parse.urlparse(u).path.lower()
    return path.endswith(IMG_EXTS)


def collect_image_urls(info):
    urls, seen = [], set()

    def add(u):
        if u and u not in seen and _is_img_url(u):
            seen.add(u)
            urls.append(u)

    for e in info.get("entries") or []:
        if not isinstance(e, dict):
            continue
        add(e.get("url"))
        for f in e.get("formats") or []:
            vc, ac = f.get("vcodec"), f.get("acodec")
            if (not vc or vc == "none") and (not ac or ac == "none"):
                add(f.get("url"))
    for f in info.get("formats") or []:
        vc, ac = f.get("vcodec"), f.get("acodec")
        if (not vc or vc == "none") and (not ac or ac == "none"):
            add(f.get("url"))
    return urls


ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def clean_text(s):
    """去掉 yt-dlp 报错里的 ANSI 颜色码，避免日志里出现乱码符号。"""
    return ANSI_RE.sub("", str(s)).strip()


# yt-dlp 的 cookiesfrombrowser 只认这些键名（Edge 是 "edge"，不是 "msedge"）
BROWSER_KEYS = {"Edge 浏览器": "edge", "Chrome 浏览器": "chrome", "Firefox 浏览器": "firefox"}


def browser_key(ui_name):
    return BROWSER_KEYS.get(ui_name, "")


def edge_kill_command():
    """强制结束 Edge 全部进程的命令（仅 Windows）；其他系统返回 None。

    Edge 运行时会锁死 Cookie 数据库导致 yt-dlp 复制失败（issue 7271），
    解析抖音前先彻底关掉 Edge 是最直接的解法。
    """
    if sys.platform.startswith("win"):
        return ["taskkill", "/F", "/IM", "msedge.exe"]
    return None


def friendly_error(msg):
    """把 yt-dlp 常见报错翻译成操作提示（可传多条报错拼接，一并给提示）。"""
    msg = clean_text(msg)
    low = msg.lower()
    tips = []
    if "could not copy" in low or "database is locked" in low or "7271" in msg:
        tips.append("浏览器正在运行、把 Cookie 数据库锁住了，软件复制不出来：彻底退出该浏览器再试。"
                    "但注意 Chrome/新版 Edge 即使退出，Cookie 值也会因“应用绑定加密”解不开——抖音等平台"
                    "请直接在 Edge 装扩展 Get cookies.txt LOCALLY，登录状态下打开 douyin.com 导出 "
                    "cookies.txt，再在本软件把 Cookie 来源选成“cookies.txt 文件”，与浏览器开没开无关。")
    if "dpapi" in low or "decrypt" in low:
        tips.append("Chrome 和新版 Edge 都用了“应用绑定加密”：Cookie 库能读出来，但里面的值只有浏览器"
                    "本体能解，任何外部程序（包括本软件）都解不开，管理员权限也不行（yt-dlp issue 10927）。"
                    "解法：在 Edge 里装扩展 Get cookies.txt LOCALLY，登录状态下打开 douyin.com 点它导出 "
                    "cookies.txt，再在本软件把 Cookie 来源选成“cookies.txt 文件”；或装 Firefox 登录一次，"
                    "Cookie 来源选“Firefox 浏览器”（Firefox 没用这种加密）。")
    if "cookie" in low or "fresh cookies" in low:
        tips.append("该平台需要有效的浏览器 Cookie：优先用 cookies.txt 文件（扩展从浏览器内部导出）"
                    "或 Firefox 的 Cookie；Chrome/新版 Edge 的 Cookie 因应用绑定加密，外部程序读不出值。")
    if "412" in low or "precondition" in low:
        tips.append("B站触发了风控（412）：选上浏览器 Cookie 后重试，或稍后再试。")
    if "login" in low or "premium" in low or "member" in low:
        tips.append("该清晰度/内容需要登录或会员：导入已登录账号的 Cookie 才能解析，本软件不绕过付费权限。")
    if "unsupported url" in low:
        tips.append("这个链接 yt-dlp 暂不支持，换作品的分享链接再试（不要用主页/专题链接）。")
    if "timed out" in low or "timeout" in low:
        tips.append("网络超时：检查网络后重试。")
    return "；".join(tips)


# ---------------------------------------------------------- v2: 内置 ffmpeg

def find_ffmpeg():
    """三级查找 ffmpeg，返回 (路径, 来源)；找不到返回 (None, "")。

    1) 程序同目录 / 打包资源里的 ffmpeg(.exe)  2) imageio-ffmpeg 自带二进制
    3) 系统 PATH 里安装的 ffmpeg
    """
    names = ["ffmpeg.exe", "ffmpeg"] if sys.platform.startswith("win") else ["ffmpeg"]
    bases = []
    try:
        bases.append(os.path.dirname(os.path.abspath(__file__)))
    except Exception:
        pass
    if getattr(sys, "frozen", False):
        bases.append(os.path.dirname(os.path.abspath(sys.executable)))
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        bases.append(meipass)
    for base in bases:
        for n in names:
            p = os.path.join(base, n)
            if os.path.isfile(p):
                return p, "程序同目录"
    try:
        import imageio_ffmpeg
        p = imageio_ffmpeg.get_ffmpeg_exe()
        if p and os.path.isfile(p):
            return p, "内置（imageio-ffmpeg）"
    except Exception:
        pass
    p = shutil.which("ffmpeg")
    if p:
        return p, "系统安装"
    return None, ""


# ------------------------------------------------- v2: 抖音自动领访客 Cookie

UA_STR = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
          "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
TTWID_REGISTER_URL = "https://ttwid.bytedance.com/ttwid/union/register/"
_B36 = "0123456789abcdefghijklmnopqrstuvwxyz"


def _to_base36(n):
    s = ""
    while n:
        s = _B36[n % 36] + s
        n //= 36
    return s or "0"


def gen_s_v_web_id():
    """按抖音网页 JS 的规则本地生成 s_v_web_id（verify_fp），无需服务器下发。"""
    chars = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
    arr = [None] * 36
    for idx in (8, 13, 18, 23):
        arr[idx] = "_"
    arr[14] = "4"
    for i in range(36):
        if arr[i] is None:
            arr[i] = chars[random.randint(0, 61)]
    return "verify_" + _to_base36(int(time.time() * 1000)) + "_" + "".join(arr)


def gen_ms_token(length=107):
    chars = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789=_-"
    return "".join(random.choice(chars) for _ in range(length))


def fetch_douyin_cookies(timeout=15):
    """向抖音官方 ttwid 接口领一个访客 Cookie，再补上本地生成的两个参数。

    返回 dict，如 {"ttwid": ..., "s_v_web_id": ..., "msToken": ...}
    ttwid 由服务器正式下发；后两者按网页规则本地生成（网站养 Cookie 池的同款做法）。
    """
    cj = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    body = json.dumps({"region": "cn", "aid": 1768, "needFid": False,
                       "service": "www.douyin.com"}).encode("utf-8")
    req = urllib.request.Request(TTWID_REGISTER_URL, data=body, headers={
        "User-Agent": UA_STR, "Content-Type": "application/json"})
    opener.open(req, timeout=timeout).read()
    cookies = {c.name: c.value for c in cj}
    if "ttwid" not in cookies:
        raise RuntimeError("ttwid 接口没有返回 Cookie")
    cookies.setdefault("s_v_web_id", gen_s_v_web_id())
    cookies.setdefault("msToken", gen_ms_token())
    return cookies


def write_cookies_file(cookies, domain=".douyin.com", path=None):
    """把 cookie dict 写成 Netscape 格式文件，返回路径（供 yt-dlp 的 cookiefile 用）。"""
    exp = int(time.time()) + 30 * 24 * 3600
    lines = ["# Netscape HTTP Cookie File"]
    for k, v in cookies.items():
        lines.append(f"{domain}\tTRUE\t/\tFALSE\t{exp}\t{k}\t{v}")
    if path is None:
        path = os.path.join(tempfile.gettempdir(), "parsetool_douyin_cookies.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return path


def is_douyin_url(url):
    try:
        host = urllib.parse.urlparse(url).netloc.lower()
    except Exception:
        return False
    return "douyin" in host


# ------------------------------------------------- v3: 抖音专用解析（不走 yt-dlp 提取器）
# yt-dlp 的抖音提取器被 a_bogus 签名墙挡住（上游 #9667），有效 Cookie 也常报
# "Fresh cookies needed"。这里改走 ParseDock 类站点的做法：带 Cookie 请求
# 视频页 HTML，解析页面内嵌的 _ROUTER_DATA / RENDER_DATA 拿播放地址再直链下载。

DOUYIN_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
             "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0")


def load_cookies_from_file(path):
    """读 Netscape cookies.txt -> {name: value}（同名以后者为准，够用即可）。"""
    out = {}
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.rstrip("\n")
                if not line or line.startswith("#"):
                    continue
                parts = line.split("\t")
                if len(parts) >= 7:
                    out[parts[5]] = parts[6]
    except OSError:
        pass
    return out


def cookie_header(cookies):
    return "; ".join(f"{k}={v}" for k, v in (cookies or {}).items() if v)


def douyin_id_from_url(url, opener=None):
    """提取抖音作品 ID：先按路径正则，短链则跟随跳转后从最终地址/页面里提取。"""
    m = re.search(r"/(?:video|note|share/video)/(\d{10,})", url or "")
    if m:
        return m.group(1)
    if opener is None:
        opener = urllib.request.build_opener()
    req = urllib.request.Request(url, headers={"User-Agent": DOUYIN_UA})
    try:
        with opener.open(req, timeout=20) as r:
            final = r.geturl() or ""
            body = r.read(300000).decode("utf-8", "replace")
    except Exception:
        return ""
    m = re.search(r"/(?:video|note)/(\d{10,})", final) or \
        re.search(r'"(?:aweme_id|itemId|item_id)":"?(\d{10,})', body)
    return m.group(1) if m else ""


def _raw_json_at(text, start):
    try:
        obj, _ = json.JSONDecoder().raw_decode(text[start:])
        return obj
    except Exception:
        return None


def parse_page_json(html):
    """从抖音页面 HTML 里提取 _ROUTER_DATA 或 RENDER_DATA 的 JSON 对象。"""
    if not html:
        return None
    idx = html.find("_ROUTER_DATA")
    if idx >= 0:
        brace = html.find("{", idx)
        if brace >= 0:
            obj = _raw_json_at(html, brace)
            if obj:
                return obj
    m = re.search(r'id="RENDER_DATA"[^>]*>(.*?)</script>', html, re.S)
    if m:
        try:
            return json.loads(urllib.parse.unquote(m.group(1)))
        except Exception:
            return None
    return None


def find_aweme_item(obj):
    """在页面 JSON 里递归找作品条目（带 item_list / aweme_detail 的那个）。"""
    if isinstance(obj, dict):
        il = obj.get("item_list")
        if isinstance(il, list) and il and isinstance(il[0], dict):
            return il[0]
        ad = obj.get("aweme_detail")
        if isinstance(ad, dict) and ad:
            return ad
        for v in obj.values():
            r = find_aweme_item(v)
            if r:
                return r
    elif isinstance(obj, list):
        for v in obj:
            r = find_aweme_item(v)
            if r:
                return r
    return None


def _first_url(url_list):
    return (url_list or [None])[0]


def douyin_item_to_rows(item):
    """作品条目 -> (rows, meta)。rows 带 direct 标记，走直链下载通道。"""
    meta = {"title": item.get("desc") or "抖音作品",
            "uploader": ((item.get("author") or {}).get("nickname") or ""),
            "id": str(item.get("aweme_id") or "")}
    rows = []
    video = item.get("video") or {}
    vids = []
    for br in video.get("bit_rate") or []:
        pa = br.get("play_addr") or {}
        u = _first_url(pa.get("url_list"))
        if not u:
            continue
        vids.append({"url": u.replace("playwm", "play"),
                     "height": pa.get("height") or 0, "width": pa.get("width") or 0,
                     "bit_rate": br.get("bit_rate") or 0,
                     "size": pa.get("data_size") or pa.get("size")})
    if not vids:
        pa = video.get("play_addr") or {}
        u = _first_url(pa.get("url_list"))
        if u:
            vids.append({"url": u.replace("playwm", "play"),
                         "height": pa.get("height") or 0, "width": pa.get("width") or 0,
                         "bit_rate": 0, "size": pa.get("data_size")})
    vids.sort(key=lambda v: (v["height"], v["bit_rate"]), reverse=True)
    if vids:
        top = vids[0]
        rows.append({"kind": "best", "direct": True, "url": top["url"],
                     "label": f"视频 · 最高画质自动（{clarity_name(top['height'])}，原视频）",
                     "format_id": None, "has_audio": True})
        for v in vids:
            wh = f" {v['width']}x{v['height']}" if v["width"] and v["height"] else ""
            rows.append({"kind": "video", "direct": True, "url": v["url"],
                         "label": (f"视频 · {clarity_name(v['height']) if v['height'] else '原始尺寸'}{wh}"
                                   f" · mp4 · {fmt_size(v['size'])}"),
                         "format_id": None, "has_audio": True})
    cover = _first_url((video.get("cover") or {}).get("url_list")) or \
        _first_url((video.get("origin_cover") or {}).get("url_list"))
    if cover:
        rows.append({"kind": "cover", "label": "图片 · 封面原图",
                     "format_id": None, "has_audio": False, "url": cover})
    img_urls = []
    for im in item.get("images") or []:
        u = _first_url((im or {}).get("url_list"))
        if u:
            img_urls.append(u)
    if img_urls:
        rows.append({"kind": "images", "label": f"图片 · 图集原图 共 {len(img_urls)} 张",
                     "format_id": None, "has_audio": False, "url": None,
                     "urls": img_urls, "count": len(img_urls)})
    return rows, meta


def resolve_douyin(url, cookie_str=""):
    """抖音专用解析：返回 (rows, meta, 来源说明)。失败抛 RuntimeError（含原因）。"""
    opener = urllib.request.build_opener()
    vid = douyin_id_from_url(url, opener)
    if not vid:
        raise RuntimeError("没能从链接里提取作品 ID（短链跳转失败或链接格式不对）")
    headers = {"User-Agent": DOUYIN_UA, "Referer": "https://www.douyin.com/",
               "Accept": "text/html,application/xhtml+xml"}
    if cookie_str:
        headers["Cookie"] = cookie_str
    pages = [(f"https://www.douyin.com/video/{vid}", "视频页"),
             (f"https://www.iesdouyin.com/share/video/{vid}/", "分享页")]
    last = ""
    for page, src in pages:
        req = urllib.request.Request(page, headers=headers)
        try:
            with opener.open(req, timeout=25) as r:
                html = r.read().decode("utf-8", "replace")
        except Exception as e:  # noqa: BLE001
            last = clean_text(e)
            continue
        data = parse_page_json(html)
        item = find_aweme_item(data) if data else None
        if item:
            rows, meta = douyin_item_to_rows(item)
            if rows:
                meta["id"] = meta.get("id") or vid
                return rows, meta, src
            last = "作品数据里没有可下载的地址"
        else:
            last = "页面里没找到作品数据（可能被风控，或 Cookie 不是登录状态/已过期）"
    raise RuntimeError("抖音专用解析失败：" + (last or "未知原因") +
                       "。请确认 cookies.txt 是在 douyin.com 登录状态下导出的"
                       "（用记事本打开能搜到 sessionid），重新导出后再试。")


# ---------------------------------------------------------- v2: 支持平台清单

PLATFORMS_TEXT = """支持平台清单（引擎 yt-dlp，支持 1000+ 站点，此处只列主流）
状态：✔ 可用 ｜ ◐ 部分可用/有条件 ｜ ✘ 不支持

【国内】
✔ 抖音（v3 起走专用解析：抓视频页数据拿原视频/多清晰度/封面/图集，需 cookies.txt 登录导出）
✔ B站（多清晰度/音频/封面；1080p+ 需登录 Cookie，4K 需大会员）
✔ 快手　✔ 微博　✔ 西瓜视频　✔ 今日头条　✔ AcFun
◐ 小红书（视频/图片笔记，部分需 Cookie）
◐ 知乎（视频）　◐ 斗鱼/虎牙（回放录像）
◐ 优酷/腾讯视频/爱奇艺/芒果TV（部分可解析，会员与 DRM 内容不行）
✔ 网易云音乐/喜马拉雅（音频，付费内容除外）
✘ 微信视频号（无公开解析接口）　✘ QQ音乐等（DRM）

【国外】
✔ YouTube（视频/Shorts/直播回放/音频/封面；偶尔需 Cookie 过机器人校验）
✔ TikTok（视频/图片帖，可能需要 Cookie）
✔ X（Twitter）视频/图片　✔ Reddit　✔ Vimeo　✔ Dailymotion
✔ Twitch（点播/录像）　✔ Pinterest（视频/原图）　✔ Tumblr　✔ VK
✔ Niconico　✔ SoundCloud（音频）
◐ Instagram（帖子/Reels，多数需登录 Cookie）
◐ Facebook（公开视频为主）　◐ Patreon（需 Cookie＋本人权限）
✘ Netflix / Disney+ / Hulu / Prime Video / Spotify（DRM，一律不支持）

原则：只解析你有权访问的内容；登录/会员内容用你自己账号的 Cookie，不绕过付费与 DRM。
某个站突然失效多半是平台改版，先升级引擎：pip install -U yt-dlp
完整版见随软件附带的 PLATFORMS.md。"""


# ---------------------------------------------------------------- 界面部分

class App:
    def __init__(self, root):
        self.root = root
        root.title("链接解析工具 · 本地版 v3.0")
        root.geometry("760x600")
        root.minsize(680, 520)

        self.info = None
        self.rows = []
        self.busy = False
        self.working_cookie_extra = {}
        self.ffmpeg_path, self.ffmpeg_src = find_ffmpeg()

        pad = {"padx": 8, "pady": 4}
        top = ttk.Frame(root)
        top.pack(fill="x", **pad)
        ttk.Label(top, text="链接").pack(side="left")
        self.url_var = tk.StringVar()
        self.url_entry = ttk.Entry(top, textvariable=self.url_var)
        self.url_entry.pack(side="left", fill="x", expand=True, padx=4)
        ttk.Button(top, text="粘贴", command=self.paste).pack(side="left")
        self.parse_btn = ttk.Button(top, text="解析", command=self.parse)
        self.parse_btn.pack(side="left", padx=(4, 0))
        ttk.Button(top, text="支持平台", command=self.show_platforms).pack(side="left", padx=(4, 0))
        ttk.Button(top, text="关闭Edge并解析", command=self.kill_edge_and_parse).pack(side="left", padx=(4, 0))

        opt = ttk.Frame(root)
        opt.pack(fill="x", **pad)
        ttk.Label(opt, text="Cookie 来源").pack(side="left")
        self.cookie_var = tk.StringVar(value="自动（推荐）")
        self.cookie_box = ttk.Combobox(
            opt, textvariable=self.cookie_var, width=16, state="readonly",
            values=["自动（推荐）", "不使用", "Edge 浏览器", "Chrome 浏览器", "Firefox 浏览器", "cookies.txt 文件"])
        self.cookie_box.pack(side="left", padx=4)
        self.cookie_file = ""
        ttk.Button(opt, text="选 cookies.txt", command=self.pick_cookie_file).pack(side="left")
        self.ffmpeg_label = ttk.Label(opt, text="")
        self.ffmpeg_label.pack(side="right")
        self.refresh_ffmpeg_label()

        opt2 = ttk.Frame(root)
        opt2.pack(fill="x", **pad)
        ttk.Label(opt2, text="保存到").pack(side="left")
        default_dir = os.path.join(os.path.expanduser("~"), "Downloads", "解析下载")
        self.dir_var = tk.StringVar(value=default_dir)
        ttk.Entry(opt2, textvariable=self.dir_var).pack(side="left", fill="x", expand=True, padx=4)
        ttk.Button(opt2, text="选择", command=self.pick_dir).pack(side="left")
        ttk.Button(opt2, text="打开文件夹", command=self.open_dir).pack(side="left", padx=(4, 0))

        self.info_var = tk.StringVar(value="粘贴抖音 / B站等链接（可直接粘贴整段分享文案），点“解析”。")
        ttk.Label(root, textvariable=self.info_var, wraplength=730,
                  foreground="#444").pack(fill="x", **pad)

        mid = ttk.Frame(root)
        mid.pack(fill="both", expand=True, **pad)
        self.list = tk.Listbox(mid, font=("Microsoft YaHei UI", 10), activestyle="dotbox")
        self.list.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(mid, orient="vertical", command=self.list.yview)
        sb.pack(side="right", fill="y")
        self.list.config(yscrollcommand=sb.set)
        self.list.bind("<Double-Button-1>", lambda e: self.download())

        bar = ttk.Frame(root)
        bar.pack(fill="x", **pad)
        self.dl_btn = ttk.Button(bar, text="下载所选", command=self.download)
        self.dl_btn.pack(side="left")
        self.progress = ttk.Progressbar(bar, mode="determinate", maximum=100)
        self.progress.pack(side="left", fill="x", expand=True, padx=8)
        self.status_var = tk.StringVar(value="")
        ttk.Label(bar, textvariable=self.status_var).pack(side="left")

        self.log_box = tk.Text(root, height=8, font=("Consolas", 9), state="disabled")
        self.log_box.pack(fill="x", **pad)

    # ---------- 小工具
    def log(self, text):
        def _():
            self.log_box.config(state="normal")
            self.log_box.insert("end", str(text) + "\n")
            self.log_box.see("end")
            self.log_box.config(state="disabled")
        self.root.after(0, _)

    def refresh_ffmpeg_label(self):
        self.ffmpeg_path, self.ffmpeg_src = find_ffmpeg()
        if self.ffmpeg_path:
            self.ffmpeg_label.config(
                text=f"ffmpeg：{self.ffmpeg_src} ✔（免装，可自动合并）", foreground="#2a7")
        else:
            self.ffmpeg_label.config(
                text="ffmpeg：未找到（合并音视频/转MP3受限，请重装依赖）", foreground="#c60")

    def show_platforms(self):
        win = tk.Toplevel(self.root)
        win.title("支持平台")
        win.geometry("640x480")
        txt = tk.Text(win, font=("Microsoft YaHei UI", 10), wrap="word")
        txt.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(win, orient="vertical", command=txt.yview)
        sb.pack(side="right", fill="y")
        txt.config(yscrollcommand=sb.set)
        txt.insert("1.0", PLATFORMS_TEXT)
        txt.config(state="disabled")

    def paste(self):
        try:
            text = self.root.clipboard_get()
        except Exception:
            text = ""
        if text:
            self.url_var.set(text.strip())

    def pick_dir(self):
        d = filedialog.askdirectory(initialdir=self.dir_var.get() or os.path.expanduser("~"))
        if d:
            self.dir_var.set(d)

    def pick_cookie_file(self):
        f = filedialog.askopenfilename(filetypes=[("cookies.txt", "*.txt"), ("全部文件", "*.*")])
        if f:
            self.cookie_file = f
            self.cookie_var.set("cookies.txt 文件")

    def open_dir(self):
        d = self.dir_var.get()
        os.makedirs(d, exist_ok=True)
        if sys.platform.startswith("win"):
            os.startfile(d)  # noqa: S606
        else:
            import subprocess
            subprocess.Popen(["xdg-open", d])

    def cookie_opts(self):
        sel = self.cookie_var.get()
        for ui_name, key in BROWSER_KEYS.items():
            if sel.startswith(ui_name.split(" ")[0]):
                return {"cookiesfrombrowser": (key,)}
        if sel.startswith("cookies") and self.cookie_file:
            return {"cookiefile": self.cookie_file}
        return {}

    def set_busy(self, busy, status=""):
        self.busy = busy
        self.status_var.set(status)
        state = "disabled" if busy else "normal"
        self.parse_btn.config(state=state)
        self.dl_btn.config(state=state)

    def kill_edge_and_parse(self):
        cmd = edge_kill_command()
        if not cmd:
            messagebox.showinfo("提示", "这个按钮只在 Windows 上有用。")
            return
        if self.busy:
            return
        if not messagebox.askyesno(
                "关闭 Edge 并解析",
                "将强制关闭 Edge 的所有窗口和后台进程（未保存的网页/表单内容会丢失），\n"
                "关完立刻开始解析。Edge 的登录状态保存在硬盘上，不会因此退出登录。继续吗？"):
            return
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            out = clean_text((r.stdout or "") + (r.stderr or ""))
            self.log("已执行关闭 Edge：" + (out[:140] if out else "完成"))
        except Exception as e:  # noqa: BLE001
            self.log(f"关闭 Edge 失败：{clean_text(e)}")
        self.root.after(900, self.parse)

    # ---------- 解析
    def parse(self):
        if self.busy:
            return
        raw = self.url_var.get()
        url = extract_url(raw)
        if not url:
            messagebox.showwarning("提示", "没识别出链接，请粘贴作品链接或整段分享文案。")
            return
        if url != raw.strip():
            self.log(f"识别到链接：{url}")
        self.set_busy(True, "解析中…")
        self.list.delete(0, "end")
        threading.Thread(target=self._parse_worker, args=(url,), daemon=True).start()

    def _extract_once(self, url, extra):
        import yt_dlp
        opts = {
            "quiet": True, "no_warnings": True, "skip_download": True,
            "socket_timeout": 30, "retries": 3, "windowsfilenames": True,
            **extra,
        }
        if self.ffmpeg_path:
            opts["ffmpeg_location"] = self.ffmpeg_path
        with yt_dlp.YoutubeDL(opts) as ydl:
            return ydl.extract_info(url, download=False)

    def _douyin_cookie_str(self):
        """为抖音专用解析准备 Cookie 头：优先用户选的 cookies.txt，其次自领访客。"""
        if getattr(self, "cookie_file", ""):
            ck = load_cookies_from_file(self.cookie_file)
            if ck:
                self.log(f"抖音专用解析：已带入 cookies.txt 的 Cookie（{len(ck)} 条）。")
                return cookie_header(ck)
            self.log("抖音专用解析：选中的 cookies.txt 读不到内容，改用访客 Cookie 试。")
        try:
            return cookie_header(fetch_douyin_cookies())
        except Exception:  # noqa: BLE001
            return ""

    def _parse_worker(self, url):
        if is_douyin_url(url):
            try:
                self.log("抖音链接：走专用解析（抓视频页数据，不用 yt-dlp 提取器）。")
                cookie_str = self._douyin_cookie_str()
                self.page_cookie = cookie_str
                rows, meta, src = resolve_douyin(url, cookie_str)
                info = {"title": meta.get("title"), "uploader": meta.get("uploader"),
                        "id": meta.get("id"), "webpage_url": url,
                        "extractor_key": "Douyin专用"}
                self.working_cookie_extra = {}
                self.root.after(0, lambda: self._parse_done_ui(info, f"抖音专用·{src}", rows))
                return
            except Exception as e:  # noqa: BLE001
                self.log(f"{clean_text(e)[:150]}")
                self.log("专用解析没成，改走 yt-dlp 常规方式再试一次。")
        mode = self.cookie_var.get()
        attempts = []
        if mode.startswith("自动"):
            if is_douyin_url(url):
                try:
                    ck = fetch_douyin_cookies()
                    attempts.append(("自领访客Cookie", {"cookiefile": write_cookies_file(ck)}))
                    self.log("已自领抖音访客 Cookie（" + "、".join(sorted(ck.keys())) + "），先试它。")
                except Exception as e:  # noqa: BLE001
                    self.log(f"自领抖音 Cookie 失败（{clean_text(e)}），自动改走后面的方式。")
                # 抖音无 Cookie 必然失败，不浪费一次，直接试浏览器
                attempts.append(("Edge浏览器Cookie", {"cookiesfrombrowser": ("edge",)}))
                attempts.append(("Chrome浏览器Cookie", {"cookiesfrombrowser": ("chrome",)}))
            else:
                attempts.append(("无Cookie", {}))
                attempts.append(("Edge浏览器Cookie", {"cookiesfrombrowser": ("edge",)}))
                attempts.append(("Chrome浏览器Cookie", {"cookiesfrombrowser": ("chrome",)}))
        else:
            attempts.append((mode, self.cookie_opts()))

        attempt_errors = []
        for name, extra in attempts:
            try:
                info = self._extract_once(url, extra)
            except Exception as e:  # noqa: BLE001
                cleaned = clean_text(e)
                attempt_errors.append((name, cleaned))
                if len(attempts) > 1:
                    self.log(f"[{name}] 没成功，换下一种：{cleaned[:110]}")
                low = cleaned.lower()
                if "Edge" in name and ("could not copy" in low or "locked" in low):
                    self.log("↑ 这是 Edge 还在运行、锁住了 Cookie 库。用界面上的“关闭Edge并解析”按钮，"
                             "或彻底退出 Edge（任务管理器结束全部 msedge.exe）后重试。")
                continue
            self.working_cookie_extra = extra
            self.root.after(0, lambda info=info, name=name: self._parse_done_ui(info, name))
            return
        # 全失败：抖音场景优先展示 Edge 的报错（最相关），提示则综合所有报错一起给
        display = attempt_errors[-1][1] if attempt_errors else "未知错误"
        for name, m in attempt_errors:
            if "Edge" in name:
                display = m
                break
        combined = " | ".join(m for _, m in attempt_errors)
        self.root.after(0, lambda: self._parse_fail_ui(display, combined))

    def _parse_done_ui(self, info, via, rows=None):
        self.info = info
        self.rows = rows if rows is not None else build_rows(info)
        title = info.get("title") or "（无标题）"
        up = info.get("uploader") or info.get("channel") or ""
        dur = info.get("duration")
        dur_s = f" · 时长 {int(dur // 60)}:{int(dur % 60):02d}" if dur else ""
        self.info_var.set(f"{title}\n作者：{up}{dur_s} · 来源：{info.get('extractor_key', '')}")
        self.list.delete(0, "end")
        for r in self.rows:
            self.list.insert("end", r["label"])
        if not self.rows:
            self.list.insert("end", "（没解析到可下载的媒体，可能是图文帖或需要登录）")
        if self.rows:
            self.list.selection_set(0)
        self.set_busy(False, "解析完成")
        self.log(f"解析完成（方式：{via}）：共 {len(self.rows)} 个可下载条目。双击条目也可下载。")

    def _parse_fail_ui(self, err, combined=None):
        msg = clean_text(err)
        tip = friendly_error(combined or msg)
        self.set_busy(False, "解析失败")
        self.log("解析失败：" + msg)
        if tip:
            self.log("提示：" + tip)

    # ---------- 下载
    def download(self):
        if self.busy or not self.info:
            return
        sel = self.list.curselection()
        if not sel or sel[0] >= len(self.rows):
            messagebox.showwarning("提示", "先在列表里选一个要下载的条目。")
            return
        row = self.rows[sel[0]]
        outdir = self.dir_var.get()
        os.makedirs(outdir, exist_ok=True)
        self.set_busy(True, "下载中…")
        self.progress.config(value=0)
        threading.Thread(target=self._dl_worker, args=(row, outdir), daemon=True).start()

    def _progress_hook(self, d):
        if d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            got = d.get("downloaded_bytes") or 0
            pct = (got * 100 / total) if total else 0
            speed = d.get("speed") or 0
            txt = f"下载中 {pct:.0f}% · {fmt_size(speed)}/s"
            self.root.after(0, lambda: (self.progress.config(value=pct),
                                        self.status_var.set(txt)))
        elif d.get("status") == "finished":
            self.root.after(0, lambda: self.status_var.set("下载完成，处理中…"))

    def _dl_worker(self, row, outdir):
        webpage = (self.info or {}).get("webpage_url") or ""
        try:
            if row.get("direct"):
                path = self._download_direct(row, outdir)
                result = f"已下载：{path}"
            elif row["kind"] in ("cover",):
                path = self._download_image(row["url"], outdir, "封面", webpage)
                result = f"封面已保存：{path}"
            elif row["kind"] in ("images",):
                ok = 0
                for i, u in enumerate(row.get("urls") or [], 1):
                    try:
                        self._download_image(u, outdir, f"图{i}", webpage)
                        ok += 1
                    except Exception as e:  # noqa: BLE001
                        self.log(f"第 {i} 张下载失败：{e}")
                result = f"图集下载完成：{ok}/{row.get('count', ok)} 张"
            else:
                result = self._download_media(row, outdir)
        except Exception as e:  # noqa: BLE001
            msg = clean_text(e)
            tip = friendly_error(msg)
            self.root.after(0, lambda: (
                self.set_busy(False, "下载失败"),
                self.log("下载失败：" + msg),
                self.log("提示：" + tip) if tip else None,
            ))
            return
        self.root.after(0, lambda: (
            self.progress.config(value=100),
            self.set_busy(False, "完成"),
            self.log(result),
        ))

    def _download_direct(self, row, outdir):
        """直链流式下载（抖音专用解析的播放地址）：带 UA/Referer/Cookie，复用进度条。"""
        url = row["url"]
        ext = os.path.splitext(urllib.parse.urlparse(url).path)[1].lower()
        if ext not in (".mp4", ".mov", ".webm", ".mkv"):
            ext = ".mp4"
        vid = (self.info or {}).get("id") or "douyin"
        path = os.path.join(outdir, f"{self._safe_title()} [{vid}]{ext}")
        headers = {"User-Agent": DOUYIN_UA, "Referer": "https://www.douyin.com/"}
        ck = getattr(self, "page_cookie", "")
        if ck:
            headers["Cookie"] = ck
        req = urllib.request.Request(url, headers=headers)
        tmp = path + ".part"
        t0 = time.time()
        got = 0
        with urllib.request.urlopen(req, timeout=30) as r, open(tmp, "wb") as f:
            total = int(r.headers.get("Content-Length") or 0)
            while True:
                chunk = r.read(262144)
                if not chunk:
                    break
                f.write(chunk)
                got += len(chunk)
                elapsed = max(time.time() - t0, 0.1)
                self._progress_hook({"status": "downloading",
                                     "downloaded_bytes": got, "total_bytes": total,
                                     "speed": got / elapsed})
        os.replace(tmp, path)
        self._progress_hook({"status": "finished"})
        return path

    def _download_media(self, row, outdir):
        import yt_dlp
        url = (self.info or {}).get("webpage_url")
        opts = {
            "quiet": True, "no_warnings": True, "socket_timeout": 30,
            "retries": 3, "windowsfilenames": True,
            "outtmpl": os.path.join(outdir, "%(title).80B [%(id)s].%(ext)s"),
            "progress_hooks": [self._progress_hook],
            **getattr(self, "working_cookie_extra", {}),
        }
        has_ffmpeg = bool(getattr(self, "ffmpeg_path", None))
        if has_ffmpeg:
            opts["ffmpeg_location"] = self.ffmpeg_path
        if row["kind"] == "best":
            opts["format"] = "bestvideo+bestaudio/best"
            if has_ffmpeg:
                opts["merge_output_format"] = "mp4"
        elif row["kind"] == "video":
            fid = row["format_id"]
            if row.get("has_audio"):
                opts["format"] = fid
            else:
                opts["format"] = f"{fid}+bestaudio[ext=m4a]/{fid}+bestaudio/{fid}"
                if has_ffmpeg:
                    opts["merge_output_format"] = "mp4"
                else:
                    self.log("未找到 ffmpeg：无声轨道将只下视频部分（重装依赖可恢复内置合并）。")
        elif row["kind"] == "audio":
            opts["format"] = row["format_id"] or "bestaudio"
            if has_ffmpeg:
                opts["postprocessors"] = [{
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3", "preferredquality": "0",
                }]
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([url])
        return "已下载到：" + outdir

    def _safe_title(self):
        t = (self.info or {}).get("title") or "media"
        t = re.sub(r'[\\/:*?"<>|]', "_", t).strip()
        return t[:40] or "media"

    def _download_image(self, url, outdir, suffix, referer=""):
        ext = os.path.splitext(urllib.parse.urlparse(url).path)[1].lower()
        if ext not in IMG_EXTS:
            ext = ".jpg"
        name = f"{self._safe_title()}_{suffix}{ext}"
        path = os.path.join(outdir, name)
        img_headers = {
            "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) "
                           "Chrome/126.0 Safari/537.36"),
            "Referer": referer or "https://www.douyin.com/",
        }
        ck = getattr(self, "page_cookie", "")
        if ck:
            img_headers["Cookie"] = ck
        req = urllib.request.Request(url, headers=img_headers)
        with urllib.request.urlopen(req, timeout=30) as r, open(path, "wb") as f:
            shutil.copyfileobj(r, f)
        return path


def main():
    if not TK_OK:
        print("当前环境没有 tkinter，无法打开界面。Windows 官方 Python 自带 tkinter。")
        return
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
