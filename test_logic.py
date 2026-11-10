# -*- coding: utf-8 -*-
"""纯逻辑自测：链接提取 / 清晰度整理 / 原图收集 / v2 内置ffmpeg与自动Cookie。"""
import sys, os, re
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ParseTool import (extract_url, build_rows, collect_image_urls, friendly_error,
                       find_ffmpeg, gen_s_v_web_id, gen_ms_token, write_cookies_file,
                       fetch_douyin_cookies, is_douyin_url, PLATFORMS_TEXT,
                       browser_key, clean_text, BROWSER_KEYS, edge_kill_command,
                       parse_page_json, find_aweme_item, douyin_item_to_rows,
                       load_cookies_from_file, cookie_header, douyin_id_from_url,
                       page_diagnostics, playwright_cookie_list, cookies_to_netscape,
                       profile_dir, profile_exists, profile_has_session,
                       extract_urls, pick_best_row)

fails = 0
def check(name, cond):
    global fails
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        fails += 1

# 1) 从抖音整段分享文案提链接
share = "0.20 10/07 FUY:/ T@L.WZ https://v.douyin.com/3Is4ZgnixNY/ 火山引擎 赠资料包 长按复制"
check("share-text douyin link", extract_url(share) == "https://v.douyin.com/3Is4ZgnixNY/")

# 2) B站链接 + 裸 BV 号
check("bilibili link", extract_url("看这个 https://www.bilibili.com/video/BV1GJ411x7h7 好") ==
"https://www.bilibili.com/video/BV1GJ411x7h7")
check("bare BV id", extract_url("BV1GJ411x7h7") == "https://www.bilibili.com/video/BV1GJ411x7h7")

# 3) 伪造 B站风格 info：DASH 分离轨道，多清晰度
fake = {
"title": "测试视频", "uploader": "UP主", "duration": 95,
"thumbnail": "https://i0.hdslb.com/cover_small.jpg",
"thumbnails": [
{"url": "https://i0.hdslb.com/cover_small.jpg", "width": 320, "height": 180},
{"url": "https://i0.hdslb.com/cover_big.jpg", "width": 1920, "height": 1080},
],
"formats": [
{"format_id": "v1080", "url": "https://cdn/v1080.m4s", "vcodec": "avc1", "acodec": "none",
"height": 1080, "fps": 30, "ext": "mp4", "tbr": 2500, "filesize_approx": 30_000_000},
{"format_id": "v1080b", "url": "https://cdn/v1080b.m4s", "vcodec": "hev1", "acodec": "none",
"height": 1080, "fps": 30, "ext": "mp4", "tbr": 1800, "filesize_approx": 20_000_000},
{"format_id": "v720", "url": "https://cdn/v720.m4s", "vcodec": "avc1", "acodec": "none",
"height": 720, "fps": 30, "ext": "mp4", "tbr": 1200, "filesize_approx": 12_000_000},
{"format_id": "v360", "url": "https://cdn/v360.mp4", "vcodec": "avc1", "acodec": "mp4a",
"height": 360, "fps": 30, "ext": "mp4", "tbr": 500, "filesize_approx": 5_000_000},
{"format_id": "a1", "url": "https://cdn/a.m4s", "vcodec": "none", "acodec": "mp4a",
"height": None, "fps": None, "ext": "m4a", "abr": 192, "filesize_approx": 2_000_000},
],
}
rows = build_rows(fake)
labels = [r["label"] for r in rows]
check("row: best first", rows[0]["kind"] == "best")
check("row: 1080 deduped (avc kept)",
sum(1 for r in rows if r["kind"] == "video" and "1080p" in r["label"]) == 1 and
any(r.get("format_id") == "v1080" for r in rows))
check("row: 720 present", any("720p" in l for l in labels))
check("row: 360 combined marked no merge note", any("360p" in l and "无声" not in l for l in labels))
check("row: 1080 notes silent track", any("1080p" in l and "无声轨道" in l for l in labels))
check("row: audio present", any(r["kind"] == "audio" for r in rows))
cover = [r for r in rows if r["kind"] == "cover"]
check("row: cover picks biggest", cover and cover[0]["url"].endswith("cover_big.jpg"))

# 4) 图文帖：entries 是图片直链
note = {"title": "图文", "formats": [], "entries": [
{"url": "https://p3.douyinpic.com/a.jpg"},
{"url": "https://p3.douyinpic.com/b.png"},
{"url": "https://p3.douyinpic.com/c.webp"},
]}
check("collect images", len(collect_image_urls(note)) == 3)
rows2 = build_rows(note)
check("row: images entry", any(r["kind"] == "images" and r.get("count") == 3 for r in rows2))

# 4.5) 通用直链：vcodec/acodec 缺失、formats 为空，info 本身就是媒体
direct = {"title": "flower", "url": "https://example.com/flower.mp4", "ext": "mp4"}
rows3 = build_rows(direct)
check("direct mp4 yields video rows", any(r["kind"] == "video" for r in rows3) and
      not any("无声轨道" in r["label"] for r in rows3))

# 4.6) v2: 内置 ffmpeg 查找
ff, src = find_ffmpeg()
check("find_ffmpeg finds one (bundled/system)", bool(ff) and os.path.isfile(ff))
print("     ffmpeg:", ff, "| 来源:", src)

# 4.7) v2: s_v_web_id / msToken 格式
sv = gen_s_v_web_id()
check("s_v_web_id format", bool(re.fullmatch(r"verify_[0-9a-z]+_[0-9A-Za-z_]{36}", sv)))
check("msToken length", len(gen_ms_token()) == 107)

# 4.8) v2: cookies 文件写出
p = write_cookies_file({"ttwid": "abc123", "s_v_web_id": sv}, path="/tmp/pt_test_cookies.txt")
content = open(p, encoding="utf-8").read()
check("cookies file content", ".douyin.com" in content and "ttwid\tabc123" in content)

# 4.9) v2: 抖音域名判断 + 平台清单
check("is_douyin_url", is_douyin_url("https://www.douyin.com/video/123") and
      is_douyin_url("https://v.douyin.com/xyz/") and
      not is_douyin_url("https://www.bilibili.com/video/BV1GJ411x7h7"))
check("platforms text covers CN+global", all(k in PLATFORMS_TEXT for k in
      ("抖音", "B站", "YouTube", "TikTok", "Instagram", "Netflix")))

# 4.10) v2: 实领 ttwid（联网，失败则跳过不算失败）
try:
    ck = fetch_douyin_cookies(timeout=15)
    check("fetch ttwid live", "ttwid" in ck and "s_v_web_id" in ck)
except Exception as e:
    print("SKIP fetch ttwid live（网络不可达）:", e)

# 4.11) v2.2: 浏览器键名必须与 yt-dlp 的 SUPPORTED_BROWSERS 一致（Edge 是 edge 不是 msedge）
try:
    from yt_dlp.cookies import SUPPORTED_BROWSERS
    check("browser keys valid in yt-dlp", all(k in SUPPORTED_BROWSERS for k in BROWSER_KEYS.values()))
except Exception as e:
    print("SKIP yt-dlp browser key check:", e)
check("edge key", browser_key("Edge 浏览器") == "edge")

# 4.12) v2.2: ANSI 颜色码清洗 + DPAPI 提示
check("clean_text strips ANSI", clean_text("\x1b[0;31mERROR:\x1b[0m failed") == "ERROR: failed")
check("dpapi tip points to Edge", "Edge" in friendly_error("Failed to decrypt with DPAPI"))

# 4.13) v2.3: Cookie 库被锁的提示 + 多报错综合提示（不再被最后一条 Chrome 报错带偏）
lock_tip = friendly_error("ERROR: Could not copy Chrome cookie database. See https://github.com/yt-dlp/yt-dlp/issues/7271")
check("copy-db tip mentions quit browser + cookies.txt",
      "彻底退出" in lock_tip and "cookies.txt" in lock_tip)
combined_tip = friendly_error("Could not copy Chrome cookie database | Failed to decrypt with DPAPI")
check("combined tips include both", "cookies.txt" in combined_tip and "Edge" in combined_tip)
check("dpapi tip gives cookies.txt + Firefox routes",
      "cookies.txt" in friendly_error("Failed to decrypt with DPAPI")
      and "Firefox" in friendly_error("Failed to decrypt with DPAPI"))

# 4.14) v2.4: 关闭 Edge 命令按平台生成
_kcmd = edge_kill_command()
check("edge kill command platform", (_kcmd is None) == (not sys.platform.startswith("win"))
      and (_kcmd is None or _kcmd[:2] == ["taskkill", "/F"]))

# 4.15) v3 抖音专用解析：页面 JSON 提取 + 条目转下载行（合成样本）
import json as _json, tempfile as _tf
_item = {
    "aweme_id": "7696175694895648042", "desc": "样本视频标题",
    "author": {"nickname": "样本作者"},
    "video": {
        "cover": {"url_list": ["https://p3.douyinpic.com/cover.jpeg"]},
        "bit_rate": [
            {"bit_rate": 2000000, "play_addr": {"url_list": ["https://v.douyinvod.com/br720.mp4"],
                                                "width": 1280, "height": 720, "data_size": 5000000}},
            {"bit_rate": 5000000, "play_addr": {"url_list": ["https://v.douyinvod.com/br1080.mp4"],
                                                "width": 1920, "height": 1080, "data_size": 12000000}},
        ],
    },
}
_router = {"loaderData": {"video_page": {"videoInfoRes": {"item_list": [_item]}}}}
_html = "<html><script>window._ROUTER_DATA = " + _json.dumps(_router) + "</script></html>"
_parsed = parse_page_json(_html)
check("router data parsed", bool(_parsed))
_found = find_aweme_item(_parsed) if _parsed else None
check("aweme item found", bool(_found) and _found.get("desc") == "样本视频标题")
_rows3, _meta3 = douyin_item_to_rows(_found) if _found else ([], {})
_kinds3 = [r["kind"] for r in _rows3]
check("douyin rows kinds", _kinds3 == ["best", "video", "video", "cover"])
check("douyin best is top quality direct", bool(_rows3) and _rows3[0].get("direct")
      and _rows3[0].get("url", "").endswith("br1080.mp4"))
check("douyin meta", _meta3.get("uploader") == "样本作者" and _meta3.get("id") == "7696175694895648042")
_note_item = {"aweme_id": "7300000000000000001", "desc": "图文",
              "images": [{"url_list": ["https://p3.douyinpic.com/a.jpeg"]},
                         {"url_list": ["https://p3.douyinpic.com/b.jpeg"]}]}
_nrows, _ = douyin_item_to_rows(_note_item)
check("douyin note images row", len(_nrows) == 1 and _nrows[0]["kind"] == "images"
      and _nrows[0]["count"] == 2)
check("douyin id from url", douyin_id_from_url("https://www.douyin.com/video/7696175694895648042") ==
      "7696175694895648042")
_ckp = write_cookies_file({"sessionid": "abc123", "ttwid": "tt"}, path=os.path.join(_tf.gettempdir(), "pt_ck_test.txt"))
_ckd = load_cookies_from_file(_ckp)
check("cookies.txt load + header", _ckd.get("sessionid") == "abc123"
      and "sessionid=abc123" in cookie_header(_ckd))

# 4.16) v3.1: UNIVERSAL 数据容器 + itemStruct（id 字段）识别 + 页面体检
_item2 = dict(_item)
_item2["id"] = _item2.pop("aweme_id")
_uni = {"__DEFAULT_SCOPE__": {"webapp.video-detail": {"itemInfo": {"itemStruct": _item2}}}}
_uhtml = ('<html><script id="__UNIVERSAL_DATA_FOR_REHYDRATION__" type="application/json">'
          + _json.dumps(_uni) + "</script></html>")
_uparsed = parse_page_json(_uhtml)
_ufound = find_aweme_item(_uparsed) if _uparsed else None
check("universal data parsed + itemStruct found", bool(_ufound) and _ufound.get("desc") == "样本视频标题")
_urows, _umeta = douyin_item_to_rows(_ufound) if _ufound else ([], {})
check("universal rows + id fallback", [r["kind"] for r in _urows] == ["best", "video", "video", "cover"]
      and _umeta.get("id") == "7696175694895648042")
_diag = page_diagnostics(_uhtml)
check("page diagnostics flags", "UNIVERSAL=有" in _diag and "疑似验证页=否" in _diag)

# 4.17) v4 浏览器模式：Playwright Cookie 格式转换
_pwl = playwright_cookie_list({"sessionid": "s1", "ttwid": "t1", "empty": ""})
check("playwright cookie list", len(_pwl) == 2 and _pwl[0]["domain"] == ".douyin.com"
      and _pwl[0]["path"] == "/" and {c["name"] for c in _pwl} == {"sessionid", "ttwid"})

# 4.18) v4.1: Cookie 回写（Playwright 格式 -> Netscape 文本 -> 再读回）
_ns = cookies_to_netscape([
    {"name": "sessionid", "value": "s1", "domain": ".douyin.com", "path": "/",
     "secure": True, "expires": 1893456000},
    {"name": "ttwid", "value": "t1", "domain": "www.douyin.com", "path": "/",
     "secure": False, "expires": -1},
])
_nsp = os.path.join(_tf.gettempdir(), "pt_ns_test.txt")
with open(_nsp, "w", encoding="utf-8") as _f:
    _f.write(_ns)
_back = load_cookies_from_file(_nsp)
check("netscape writeback roundtrip", _back.get("sessionid") == "s1" and _back.get("ttwid") == "t1"
      and ".douyin.com\tTRUE" in _ns and "1893456000" in _ns)

# 4.19) v5 档案模式：档案目录与存在性判断
_pd = profile_dir()
check("profile dir shape", _pd.endswith("EdgeProfile") and ("ParseTool" in _pd or ".parsetool" in _pd))
_td5 = _tf.mkdtemp(prefix="pt_prof_")
check("profile empty dir not exists", profile_exists(_td5) is False)
with open(os.path.join(_td5, "x"), "w") as _f:
    _f.write("1")
check("profile nonempty dir exists", profile_exists(_td5) is True)

# 4.20) v6: 品牌与 UI 清理（两个过时按钮文案消失、品牌名就位、自动登录函数存在）
_src6 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "ParseTool.py"),
             encoding="utf-8").read()
check("v6 brand present", "ParseBox 解析盒" in _src6 and "v6.1" in _src6)
check("v6 obsolete buttons gone", "关闭Edge并解析" not in _src6 and "抖音登录（档案）" not in _src6)
check("v6 autologin wired", "profile_has_session" in _src6 and "自动再弹出登录窗口" in _src6)

# 4.21) v6.1 批量：多链接提取（混合文案/多平台/BV/去重/上限）
_blob = ("9.99 复制打开抖音 https://v.douyin.com/abc123/ 看看\n"
         "B站这个 https://www.bilibili.com/video/BV1xx411c7mD 不错\n"
         "还有一个裸号 BV1yy411c7mE 和重复的 https://v.douyin.com/abc123/ \n"
         "油管 https://www.youtube.com/watch?v=xyz789 完")
_urls = extract_urls(_blob)
check("batch urls count", len(_urls) == 4)
check("batch urls dedupe order", _urls[0].startswith("https://v.douyin.com")
      and _urls[1].endswith("BV1xx411c7mD") and _urls[2].endswith("BV1yy411c7mE")
      and _urls[3].startswith("https://www.youtube.com"))
check("batch urls empty", extract_urls("") == [] and extract_urls("没有链接") == [])
check("batch urls cap", len(extract_urls(" ".join(
    f"https://example.com/v{i}" for i in range(40)))) == 30)

# 4.22) v6.1 批量：每条选最佳行
check("best row: kind best wins", pick_best_row(
    [{"kind": "video", "height": 2160}, {"kind": "best"}])["kind"] == "best")
check("best row: tallest video", pick_best_row(
    [{"kind": "video", "height": 720, "tbr": 800},
     {"kind": "video", "height": 1080, "tbr": 500}])["height"] == 1080)
check("best row: direct counts as video", pick_best_row(
    [{"kind": "cover"}, {"kind": "video", "direct": True, "height": 540}])["height"] == 540)
check("best row: audio fallback", pick_best_row(
    [{"kind": "cover"}, {"kind": "audio"}])["kind"] == "audio")
check("best row: empty", pick_best_row([]) is None)

import ParseTool

# 4.23) v6.1 批量：解析收集管道（假解析器驱动 _batch_parse_worker）
class _FakeRoot:
    def after(self, ms, fn=None):
        return None  # 不执行 UI 收尾，只验证数据管道


_app = ParseTool.App.__new__(ParseTool.App)
_app.log = lambda s: None
_app.root = _FakeRoot()
_app._batch_stop = False
_app.working_cookie_extra = {"cookiesfrombrowser": ("edge",)}
_app.page_cookie = "sessionid=x"


def _fake_parse(url, on_done=None, on_fail=None):
    if "bad" in url:
        on_fail("模拟失败", "模拟失败")
    elif "dy" in url:
        on_done({"title": "抖音作", "extractor_key": "Douyin档案",
                 "webpage_url": url, "id": "1"}, "档案模式",
                [{"kind": "video", "label": "v", "direct": True, "height": 720, "url": "http://x"}])
    else:
        on_done({"title": "B站作", "extractor_key": "Bilibili",
                 "webpage_url": url, "id": "2",
                 "formats": [{"format_id": "1", "vcodec": "avc1", "acodec": "none",
                              "height": 1080, "fps": 30, "tbr": 1000, "ext": "mp4",
                              "url": "http://x/v.mp4"}]},
                "无Cookie")


_app._parse_worker = _fake_parse
_app._batch_parse_worker(["https://dy.example/1", "https://bad.example/2",
                          "https://www.bilibili.com/video/BV1xx411c7mD"])
_res = _app.batch_results
check("batch pipe count", len(_res) == 3)
check("batch pipe douyin rows kept", _res[0]["rows"][0]["direct"] is True)
check("batch pipe failure recorded", "error" in _res[1])
check("batch pipe generic rows built", len(_res[2]["rows"]) >= 1
      and _res[2]["cookie_extra"] == {"cookiesfrombrowser": ("edge",)})

# 4.24) v6.1 下载分发 _dl_one（桩方法验证路由）
_app2 = ParseTool.App.__new__(ParseTool.App)
_app2.log = lambda s: None
_app2.info = {"webpage_url": "https://example.com/watch", "title": "t", "id": "9"}
_app2._download_direct = lambda row, outdir: "/tmp/a.mp4"
_app2._download_image = lambda url, outdir, suffix, referer="": "/tmp/i.jpg"
_app2._download_media = lambda row, outdir: "media-ok"
check("dl_one direct", _app2._dl_one({"direct": True, "url": "http://x/v.mp4"}, "/tmp") == "已下载：/tmp/a.mp4")
check("dl_one cover", _app2._dl_one({"kind": "cover", "url": "http://x/c.jpg"}, "/tmp") == "封面已保存：/tmp/i.jpg")
check("dl_one images", _app2._dl_one({"kind": "images", "urls": ["u1", "u2"], "count": 2}, "/tmp") == "图集下载完成：2/2 张")
check("dl_one media", _app2._dl_one({"kind": "best"}, "/tmp") == "media-ok")

# 5) 报错翻译
check("douyin cookie tip", "Cookie" in friendly_error("[Douyin] Fresh cookies are needed"))
check("412 tip", "412" in friendly_error("HTTP Error 412: Precondition Failed"))

print("\n全部通过" if fails == 0 else f"\n{fails} 项失败")
sys.exit(1 if fails else 0)
