
import argparse
import csv
import html
import json
import queue
import random
import re
import sys
import threading
import time
from pathlib import Path
from urllib.parse import unquote, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------

DEFAULT_API_KEY_FILE = r"C:\Users\HP\Downloads\scrape.py"
DEFAULT_COOKIES_FILE = "amazon_cookies.txt"
AJAX_REVIEWS_PATH = "/portal/customer-reviews/ajax/reviews/get/"

CHROME_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
             "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

# 站点别名 -> 域名后缀
SITES = {
    "us": "com", "usa": "com", "ca": "ca", "uk": "co.uk", "gb": "co.uk",
    "de": "de", "fr": "fr", "it": "it", "es": "es", "jp": "co.jp", "japan": "co.jp",
    "in": "in", "au": "com.au", "mx": "com.mx", "br": "com.br", "ae": "ae",
    "nl": "nl", "se": "se", "pl": "pl", "tr": "com.tr", "sg": "sg",
    "ie": "ie", "za": "co.za", "sa": "sa", "be": "com.be", "eg": "eg",
    "ng": "com.ng", "nl2": "nl",
}

# Cookie 中 lc-xxx 值 -> 站点后缀
MARKET_TLD = {
    "acbus": "com", "acbca": "ca", "acbuk": "co.uk", "acbin": "in", "acbde": "de",
    "acbfr": "fr", "acbit": "it", "acbes": "es", "acbja": "co.jp", "acbmx": "com.mx",
    "acbgl": "com.br", "acbae": "ae", "acbau": "com.au", "acbse": "se", "acbpl": "pl",
    "acbtr": "com.tr", "acbnl": "nl", "acbsg": "sg", "acbie": "ie", "acbza": "co.za",
    "acbsa": "sa", "acbec": "com.ec", "acbng": "com.ng", "acbeg": "eg",
}

# 货币 -> 站点(备用识别)
CURRENCY_TLD = {
    "CAD": "ca", "GBP": "co.uk", "JPY": "co.jp", "INR": "in", "AUD": "com.au",
    "BRL": "com.br", "MXN": "com.mx", "SGD": "sg", "AED": "ae", "TRY": "com.tr",
    "PLN": "pl", "SEK": "se", "EUR": "de",
}

# 站点 -> Accept-Language
SITE_LANG = {
    "com": "en-US", "ca": "en-CA", "co.uk": "en-GB", "de": "de-DE", "fr": "fr-FR",
    "it": "it-IT", "es": "es-ES", "co.jp": "ja-JP", "in": "en-IN", "com.au": "en-AU",
    "com.mx": "es-MX", "com.br": "pt-BR", "ae": "en-AE", "nl": "nl-NL", "se": "sv-SE",
    "pl": "pl-PL", "com.tr": "tr-TR", "sg": "en-SG", "ie": "en-IE", "co.za": "en-ZA",
    "sa": "en-SA", "com.be": "nl-BE", "eg": "ar-EG", "com.eg": "ar-EG", "com.ng": "en-NG",
}

GUI_MARKETPLACES = [
    ("自动（根据 Cookie）", "auto"),
    ("美国（amazon.com）", "com"),
    ("加拿大（amazon.ca）", "ca"),
    ("英国（amazon.co.uk）", "co.uk"),
    ("德国（amazon.de）", "de"),
    ("法国（amazon.fr）", "fr"),
    ("意大利（amazon.it）", "it"),
    ("西班牙（amazon.es）", "es"),
    ("日本（amazon.co.jp）", "co.jp"),
    ("印度（amazon.in）", "in"),
    ("澳大利亚（amazon.com.au）", "com.au"),
    ("墨西哥（amazon.com.mx）", "com.mx"),
    ("巴西（amazon.com.br）", "com.br"),
    ("阿联酋（amazon.ae）", "ae"),
    ("荷兰（amazon.nl）", "nl"),
    ("瑞典（amazon.se）", "se"),
    ("波兰（amazon.pl）", "pl"),
    ("土耳其（amazon.com.tr）", "com.tr"),
    ("新加坡（amazon.sg）", "sg"),
    ("爱尔兰（amazon.ie）", "ie"),
    ("南非（amazon.co.za）", "co.za"),
    ("沙特阿拉伯（amazon.sa）", "sa"),
    ("比利时（amazon.com.be）", "com.be"),
    ("埃及（amazon.eg）", "eg"),
]

STAR_FILTERS = ["all_stars", "five_star", "four_star", "three_star", "two_star", "one_star"]

CSV_COLUMNS = [
    "stars", "date", "username", "title", "review",
    "verified_purchase", "helpful_votes", "review_id", "review_url",
    "image_urls", "video_urls", "video_poster_urls", "video_present",
    "filter", "page",
]


class CookieError(RuntimeError):
    """Cookie 失效/评论被隐藏 -> 触发重新登录流程。"""


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------


def load_api_key(path: str) -> str:
    text = Path(path).read_text(encoding="utf-8", errors="ignore")
    m = re.search(r"""api_key['"]\s*[:=]\s*['"]([^'"]+)['"]""", text)
    if not m:
        raise SystemExit(f"错误: 在 {path} 中没有找到 api_key")
    return m.group(1).strip()


def read_cookie_line(path: Path) -> str:
    if not path.exists():
        return ""
    lines = [ln.strip() for ln in path.read_text(encoding="utf-8").splitlines()
             if ln.strip() and not ln.strip().startswith("#")]
    return " ".join(lines).strip().strip('"').strip("'")


def has_interactive_stdin() -> bool:
    """兼容 pythonw/Tkinter 等没有 stdin 的桌面运行环境。"""
    return bool(getattr(sys.stdin, "isatty", lambda: False)())


def save_cookie_line(path: Path, line: str):
    header = ("# 亚马逊登录 Cookie (amazon_reviews.py 自动维护, 单行 name=value; ...)\n"
              f"# 最后更新: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
    path.write_text(header + line.strip() + "\n", encoding="utf-8")


def cookie_dict(line: str) -> dict:
    d = {}
    for pair in line.split(";"):
        if "=" in pair:
            k, v = pair.split("=", 1)
            d[k.strip()] = v.strip()
    return d


def detect_tld(cookies: str) -> str:
    """根据 Cookie 推断站点。"""
    m = re.search(r"\blc-([a-z0-9]+)=", cookies)
    if m and m.group(1) in MARKET_TLD:
        return MARKET_TLD[m.group(1)]
    m = re.search(r"\bi18n-prefs=([A-Z]{3})", cookies)
    if m and m.group(1) in CURRENCY_TLD:
        return CURRENCY_TLD[m.group(1)]
    return "com"


def resolve_tld(args, cookies: str) -> str:
    if args.tld:
        return args.tld.lstrip(".").lower()
    if args.site and args.site.lower() != "auto":
        s = args.site.lower()
        return SITES.get(s, s)
    return detect_tld(cookies) if cookies else "com"


def choose_cookies_path(base_str: str, args) -> tuple:
    """按站点选择 Cookie 文件(不同地区的登录互不覆盖), 返回 (路径, 站点tld)。

    规则:
      * 明确指定了 --cookies-file -> 直接用它;
      * 否则优先 amazon_cookies.<tld>.txt (如 amazon_cookies.com.txt);
      * 兼容旧文件 amazon_cookies.txt: 当它识别出的站点就是目标站点时继续用它;
      * 都没有 -> 用 amazon_cookies.<tld>.txt (登录时新建)。
    """
    base = Path(base_str)
    if (base_str != DEFAULT_COOKIES_FILE
            and not getattr(args, "site_specific_cookies", False)):
        line = read_cookie_line(base)
        return base, resolve_tld(args, line)

    if args.tld:
        tld = args.tld.lstrip(".").lower()
    elif args.site and args.site.lower() != "auto":
        s = args.site.lower()
        tld = SITES.get(s, s)
    else:
        tld = detect_tld(read_cookie_line(base)) if base.exists() else "com"

    per_site = base.with_name(f"{base.stem}.{tld}{base.suffix}")   # amazon_cookies.ca.txt
    if per_site.exists():
        return per_site, tld
    if base.exists() and detect_tld(read_cookie_line(base)) == tld:
        return base, tld
    return per_site, tld


def normalize_asin(raw: str) -> str:
    raw = raw.strip()
    m = re.search(
        r"/(?:dp|gp/product|product-reviews|product)/([A-Z0-9]{10})(?=[/?#&\s]|$)",
        raw, re.I,
    )
    if m:
        return m.group(1).upper()
    raw = raw.upper()
    if re.fullmatch(r"[A-Z0-9]{10}", raw):
        return raw
    raise SystemExit(f"错误: 无法识别的 ASIN / 链接: {raw}")


def extract_asins(text) -> list:
    """从多行文本、ASIN 列表或商品/评论链接中解析并去重 ASIN。"""
    if isinstance(text, (list, tuple)):
        text = "\n".join(str(item) for item in text)
    text = str(text or "")
    found = []
    patterns = (
        r"/(?:dp|gp/product|product-reviews|product)/([A-Z0-9]{10})(?=[/?#&\s]|$)",
        r"(?<![A-Z0-9])([A-Z0-9]{10})(?![A-Z0-9])",
    )
    for pattern in patterns:
        for match in re.finditer(pattern, text, re.I):
            asin = match.group(1).upper()
            if asin not in found:
                found.append(asin)
    return found


def one_text(node, selectors) -> str:
    if node is None:
        return ""
    for sel in selectors:
        el = node.select_one(sel)
        if el:
            t = el.get_text(" ", strip=True)
            if t:
                return t
    return ""


def clean_title(title: str) -> str:
    if not title:
        return ""
    title = re.sub(
        r"^\s*[\d.,]+\s+(?:(?:out\s+of|von|sur|de|su|em)\s+5\s+)?"
        r"(?:stars?|stern(?:e|en)?|étoiles?|estrelas?|stelle?)\s*",
        "", title, flags=re.I,
    )
    return re.sub(r"\s+", " ", title).strip()


def clean_body(body: str) -> str:
    if not body:
        return ""
    body = re.sub(r"\s+Read more\s*$", "", body.strip(), flags=re.I)
    body = re.sub(r"\s+Read less\s*$", "", body, flags=re.I)
    return body.strip()


def parse_stars(text: str):
    text = (text or "").strip()
    m = re.search(
        r"([\d]+(?:[.,][\d]+)?)\s*(?:out\s+of|von|sur|de|su|em)\s*5\b",
        text, re.I,
    )
    if not m:
        # Amazon 会按站点本地化星级文案；星级元素通常以评分数字开头。
        m = re.search(r"^\s*([\d]+(?:[.,][\d]+)?)", text)
    if m:
        try:
            return float(m.group(1).replace(",", "."))
        except ValueError:
            return None
    return None


def _media_candidates(value) -> list:
    """从 HTML 属性或内嵌 JSON 字符串中取 URL 候选项。"""
    if not isinstance(value, str) or not value.strip():
        return []
    value = html.unescape(value).replace(r"\/", "/")
    pattern = r'''(?:https?:)?//[^\s"'<>\\]+|/(?:[^\s"'<>\\]+)'''
    found = re.findall(pattern, value)
    return found or ([value.strip()] if value.strip().startswith(("http://", "https://", "//", "/")) else [])


def _absolute_media_url(value: str, domain: str) -> str:
    value = html.unescape((value or "").strip()).rstrip(".,;:!?)]}>")
    if value.startswith("//"):
        value = "https:" + value
    elif value.startswith("/"):
        value = urljoin(f"https://{domain}/", value)
    if not value.startswith(("http://", "https://")):
        return ""
    parsed = urlparse(value)
    if not parsed.netloc:
        return ""
    return value


def _canonicalize_amazon_image_url(url: str) -> str:
    """移除 Amazon 图片 URL 的尺寸/格式变体后缀，保留原图扩展名和查询参数。"""
    if not url:
        return url
    parsed = urlparse(url)
    host = parsed.netloc.casefold()
    if ("amazon." not in host and "media-amazon." not in host) or "/images/" not in parsed.path:
        return url
    image_path = re.sub(
        r"\._[^/]+(?=\.(?:jpe?g|png|webp|avif|gif|bmp|tiff?)$)",
        "",
        parsed.path,
        flags=re.I,
    )
    if image_path == parsed.path:
        return url
    return parsed._replace(path=image_path).geturl()


def _is_direct_video_url(url: str) -> bool:
    parsed = urlparse(url)
    value = (parsed.path + "?" + parsed.query).lower()
    return bool(
        re.search(r"\.(?:mp4|m3u8|m3u|mpd|webm|m4v)(?:$|[?&])", value)
        or ("vse-vms-transcoding-artifact" in value
            and any(tag in value for tag in ("hls", "mp4", "video", "jobtemplate")))
    )


def _append_unique(target: list, values):
    for value in values:
        if value and value not in target:
            target.append(value)


def extract_review_media(node, domain: str) -> dict:
    """保留评论图片、视频直链和视频海报链接；不把海报伪装成视频链接。"""
    image_urls, video_urls, poster_urls = [], [], []
    image_containers = node.select(
        '[data-hook*="review-image"], [class*="review-image"], [id*="review-image"]'
    )
    image_nodes = []
    for container in image_containers:
        if container.name == "img":
            image_nodes.append(container)
        image_nodes.extend(container.select("img"))

    for img in image_nodes:
        candidates = []
        for attr in ("src", "data-src", "data-old-hires", "data-a-hires", "data-original", "srcset"):
            candidates.extend(_media_candidates(img.get(attr)))
        dynamic = img.get("data-a-dynamic-image")
        if dynamic:
            try:
                parsed = json.loads(html.unescape(dynamic))
                if isinstance(parsed, dict):
                    candidates.extend(parsed.keys())
            except (TypeError, ValueError):
                candidates.extend(_media_candidates(dynamic))
        for candidate in candidates:
            url = _canonicalize_amazon_image_url(_absolute_media_url(candidate, domain))
            if url and not _is_direct_video_url(url):
                _append_unique(image_urls, [url])

    for container in image_containers:
        for anchor in container.select("a[href]"):
            for candidate in _media_candidates(anchor.get("href")):
                url = _canonicalize_amazon_image_url(_absolute_media_url(candidate, domain))
                if url and ("/images/" in urlparse(url).path or "media-amazon" in url):
                    _append_unique(image_urls, [url])

    video_nodes = node.select(
        'video, source, [data-hook*="video"], [class*="video"], [id*="video"], '
        '[data-video-url], [data-a-video-url], [data-video-id], [data-video]'
    )
    video_present = bool(video_nodes)
    video_attrs = ("src", "data-src", "data-video-url", "data-a-video-url", "data-url", "href")
    for video_node in video_nodes:
        attr_values = []
        for attr in video_attrs:
            if video_node.get(attr):
                attr_values.append(video_node.get(attr))
        for attr_value in video_node.attrs.values():
            if isinstance(attr_value, list):
                attr_values.extend(attr_value)
            else:
                attr_values.append(attr_value)
        for attr_value in attr_values:
            for candidate in _media_candidates(str(attr_value)):
                url = _absolute_media_url(candidate, domain)
                if url and _is_direct_video_url(url):
                    _append_unique(video_urls, [url])
        for attr in ("poster", "data-poster", "data-video-poster"):
            for candidate in _media_candidates(video_node.get(attr)):
                url = _canonicalize_amazon_image_url(_absolute_media_url(candidate, domain))
                if url:
                    _append_unique(poster_urls, [url])
        sources = list(video_node.select(
            "source[src], video[src], iframe[src], img[src], img[data-src], a[href]"
        ))
        if video_node.name == "img":
            sources.append(video_node)
        for source in sources:
            for attr in ("src", "data-src", "poster"):
                for candidate in _media_candidates(source.get(attr)):
                    url = _absolute_media_url(candidate, domain)
                    if not url:
                        continue
                    if _is_direct_video_url(url):
                        _append_unique(video_urls, [url])
                    elif source.name == "img" or attr == "poster":
                        _append_unique(poster_urls, [_canonicalize_amazon_image_url(url)])

    return {
        "image_urls": image_urls,
        "video_urls": video_urls,
        "video_poster_urls": poster_urls,
        "video_present": video_present or bool(video_urls) or bool(poster_urls),
    }


def review_identity(rv: dict) -> str:
    """按 ID 去重；无 ID 时使用完整评论内容和元数据，避免短前缀误合并。"""
    rid = re.sub(r"^customer_review[-_]", "", str(rv.get("review_id") or ""), flags=re.I).strip()
    if rid:
        return "id:" + rid
    parts = [
        rv.get("title"), rv.get("review"), rv.get("date"), rv.get("stars"), rv.get("username")
    ]
    normalized = [re.sub(r"\s+", " ", str(part or "")).strip().casefold() for part in parts]
    return "fallback:" + "|".join(normalized)


# ---------------------------------------------------------------------------
# 客户端: 直连(默认) / ScraperAPI
# ---------------------------------------------------------------------------


class DirectClient:
    """直接从本机访问亚马逊(不消耗任何 API 额度)。"""

    can_render = False
    is_api = False

    def __init__(self, cookies: str, tld: str, delay: float = 1.0, max_retries: int = 3,
                 cookies_path: Path = None):
        self.tld = tld
        self.cookies_path = cookies_path or Path(DEFAULT_COOKIES_FILE)
        self.delay = delay
        self.max_retries = max_retries
        self.requests_made = 0
        self._last_call = 0.0
        self.session = requests.Session()
        lang = SITE_LANG.get(tld, "en-US")
        self.session.headers.update({
            "User-Agent": CHROME_UA,
            "Accept": ("text/html,application/xhtml+xml,application/xml;q=0.9,"
                       "image/avif,image/webp,image/apng,*/*;q=0.8"),
            "Accept-Language": lang,
            "Accept-Encoding": "gzip, deflate",
            "sec-ch-ua": '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
            "Upgrade-Insecure-Requests": "1",
        })
        if cookies:
            self.session.cookies = requests.cookies.cookiejar_from_dict(cookie_dict(cookies))

    def _throttle(self):
        wait = self.delay + random.uniform(0, 0.6) - (time.time() - self._last_call)
        if wait > 0:
            time.sleep(wait)
        self._last_call = time.time()

    def get(self, url: str, render: bool = False) -> str:
        last_err = ""
        for attempt in range(self.max_retries + 1):
            self._throttle()
            try:
                resp = self.session.get(url, timeout=60)
            except requests.RequestException as e:
                last_err = f"网络错误: {e}"
                time.sleep(2 ** attempt)
                continue
            self.requests_made += 1

            if resp.status_code == 200 and resp.text:
                return resp.text
            if resp.status_code in (429, 500, 502, 503):
                last_err = f"HTTP {resp.status_code}"
                time.sleep(3 * (attempt + 1))
                continue
            if resp.status_code == 404:
                raise RuntimeError(f"页面 404(ASIN 或站点可能不对): {url}")
            if resp.status_code in (403, 405):
                raise RuntimeError(
                    f"直连被亚马逊拦截(HTTP {resp.status_code})。"
                    "可稍后重试, 或加 --via api 改用 ScraperAPI 爬取。"
                )
            last_err = f"HTTP {resp.status_code}: {resp.text[:150]}"
            time.sleep(2 ** attempt)
        raise RuntimeError(f"重试 {self.max_retries} 次后仍失败 -> {last_err}")

    def post_form(self, url: str, data: dict, headers: dict = None) -> str:
        hdrs = {
            "X-Requested-With": "XMLHttpRequest",
            "Accept": "*/*",
            "Accept-Language": SITE_LANG.get(self.tld, "en-US"),
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "sec-fetch-site": "same-origin",
            "sec-fetch-mode": "cors",
            "sec-fetch-dest": "empty",
        }
        if headers:
            hdrs.update(headers)
        last_err = ""
        for attempt in range(self.max_retries + 1):
            self._throttle()
            try:
                resp = self.session.post(url, data=data, headers=hdrs, timeout=60)
            except requests.RequestException as e:
                last_err = f"网络错误: {e}"
                time.sleep(2 ** attempt)
                continue
            self.requests_made += 1
            if resp.status_code == 200 and resp.text:
                return resp.text
            if resp.status_code in (429, 500, 502, 503):
                last_err = f"HTTP {resp.status_code}"
                time.sleep(3 * (attempt + 1))
                continue
            last_err = f"HTTP {resp.status_code}: {resp.text[:150]}"
            time.sleep(2 ** attempt)
        raise RuntimeError(f"AJAX 翻页失败 -> {last_err}")

    def persist_cookies(self):
        """把本次会话刷新后的 Cookie 回存(自动续期, 延缓过期)。"""
        seen = {}
        for c in self.session.cookies:
            dom = c.domain or ""
            if not dom or "amazon." in dom:
                seen[c.name] = c.value
        if seen:
            line = "; ".join(f"{k}={v}" for k, v in seen.items())
            save_cookie_line(self.cookies_path, line)


class ApiClient:
    """通过 ScraperAPI 爬取(代理 + 可渲染, 消耗额度)。"""

    can_render = True
    is_api = True
    STRUCTURED_PRODUCT = "https://api.scraperapi.com/structured/amazon/product/v1"

    def __init__(self, api_key: str, cookies: str = "", delay: float = 1.0,
                 max_retries: int = 3, session_number: int = 1, cookies_path: Path = None):
        self.api_key = api_key
        self.cookies = cookies
        self.cookies_path = cookies_path or Path(DEFAULT_COOKIES_FILE)
        self.delay = delay
        self.max_retries = max_retries
        self.session_number = session_number
        self.requests_made = 0
        self._last_call = 0.0

    def _throttle(self):
        wait = self.delay - (time.time() - self._last_call)
        if wait > 0:
            time.sleep(wait)
        self._last_call = time.time()

    def _request(self, method: str, url: str, *, render: bool = False,
                 data: dict = None, headers: dict = None) -> str:
        params = {"api_key": self.api_key, "url": url, "session_number": self.session_number}
        hdrs = {}
        if self.cookies:
            params["keep_headers"] = "true"
            hdrs["Cookie"] = self.cookies
        if render:
            params["render"] = "true"
        if headers:
            hdrs.update(headers)

        last_err = ""
        for attempt in range(self.max_retries + 1):
            self._throttle()
            try:
                if method == "POST":
                    resp = requests.post("https://api.scraperapi.com/",
                                         params=params, data=data, headers=hdrs, timeout=180)
                else:
                    resp = requests.get("https://api.scraperapi.com/",
                                        params=params, headers=hdrs, timeout=180)
            except requests.RequestException as e:
                last_err = f"网络错误: {e}"
                time.sleep(2 ** attempt)
                continue
            self.requests_made += 1

            if resp.status_code == 200 and resp.text:
                return resp.text
            if resp.status_code in (429, 500, 502, 503) or (resp.status_code == 200 and not resp.text):
                last_err = f"HTTP {resp.status_code} (空响应)"
                time.sleep(3 * (attempt + 1))
                continue
            if resp.status_code == 404:
                raise RuntimeError(f"目标页面 404: {url}")
            last_err = f"HTTP {resp.status_code}: {resp.text[:200]}"
            time.sleep(2 ** attempt)
        raise RuntimeError(f"重试 {self.max_retries} 次后仍失败 -> {last_err}")

    def get(self, url: str, render: bool = False) -> str:
        return self._request("GET", url, render=render)

    def post_form(self, url: str, data: dict, headers: dict = None) -> str:
        return self._request("POST", url, data=data, headers=headers)

    def persist_cookies(self):
        pass   # API 模式 Cookie 不经过本机, 无法刷新


# ---------------------------------------------------------------------------
# 页面状态 & 解析
# ---------------------------------------------------------------------------


class PageStatus:
    OK = "ok"
    SIGNIN = "signin"
    CAPTCHA = "captcha"
    BLOCKED = "blocked"
    UNEXPECTED = "unexpected"


def detect_status(html: str) -> str:
    if "Robot Check" in html[:20000] or 'id="captchacharacters"' in html:
        return PageStatus.CAPTCHA
    title = re.search(r"<title>(.*?)</title>", html[:8000], re.S)
    title = title.group(1) if title else ""
    if re.search(r'name="ap_email"|id="ap_email"', html) or "Amazon Sign-In" in title:
        return PageStatus.SIGNIN
    if "Sign in</a> to see customer reviews" in html or "Sign in to see customer reviews" in html:
        return PageStatus.BLOCKED
    return PageStatus.OK


def build_review_row(node, domain: str) -> dict:
    rid = (node.get("id") or "").strip()
    rid = re.sub(r"^customer_review[-_]", "", rid, flags=re.I)
    stars = parse_stars(one_text(node, [
        '[data-hook="review-star-rating"] .a-icon-alt',
        '[data-hook="cmps-review-star-rating"] .a-icon-alt',
        '[data-hook="review-star-rating"]',
        '[data-hook="cmps-review-star-rating"]',
        'i[data-hook*="star-rating"]',
    ]))
    link = node.select_one('a[data-hook="review-title"][href]')
    href = (link.get("href") or "").strip() if link else ""
    if href.startswith("/"):
        href = f"https://{domain}{href}"

    helpful_text = one_text(node, [
        '[data-hook="helpful-vote-button"]',
        '[data-hook="helpful-vote-statement"]',
        '[data-hook="helpful-vote"]',
    ])
    m = re.search(r"(\d+)", helpful_text or "")
    if m:
        helpful = int(m.group(1))
    elif "one person" in (helpful_text or "").lower():
        helpful = 1
    else:
        helpful = None

    media = extract_review_media(node, domain)
    return {
        "review_id": rid,
        "stars": stars,
        "date": one_text(node, ['[data-hook="review-date"]']),
        "username": one_text(node, [".a-profile-name"]),
        "title": clean_title(one_text(node, ['[data-hook="review-title"]'])),
        "review": clean_body(one_text(node, ['[data-hook="review-body"]'])),
        "verified_purchase": node.select_one('[data-hook="avp-badge"]') is not None,
        "helpful_votes": helpful,
        "review_url": href or (f"https://{domain}/gp/customer-reviews/{rid}" if rid else ""),
        **media,
    }


def _extract_show_more(soup) -> dict:
    btn = soup.select_one('[data-hook="show-more-button"]')
    if btn is None:
        return None
    raw = (btn.get("data-reviews-state-param") or "").replace("&quot;", '"').replace("&amp;", "&")
    try:
        state = json.loads(raw) or {}
    except ValueError:
        return None
    if not state.get("nextPageToken"):
        return None
    return state


def parse_summary(soup, html: str) -> dict:
    """从评论页解析商品名/评分/星级占比。"""
    title = soup.title.get_text(strip=True) if soup.title else ""
    name = re.sub(rf"^Amazon\.[a-z.]+:\s*(Customer reviews:\s*)?", "", title)

    avg = None
    el = soup.select_one('[data-hook="average-star-rating"] .a-icon-alt')
    if el:
        avg = parse_stars(el.get_text())

    total = None
    el = soup.select_one('[data-hook="total-review-count"]')
    if el:
        m = re.search(r"([\d,]+)", el.get_text())
        if m:
            total = int(m.group(1).replace(",", ""))

    pcts = {}
    for a in soup.select("a[aria-label]"):
        m = re.match(r"\s*(\d+)% of reviews have (\d) stars", a.get("aria-label") or "")
        if m:
            pcts[f"{m.group(2)}_star"] = int(m.group(1))

    public_count = None
    el = soup.select_one('[data-hook="cr-filter-info-review-rating-count"]')
    if el:
        count_text = el.get_text(" ", strip=True)
        # 例如 “1-10 of 2,345 reviews”：取 reviews 前的总数，不能取范围起点 1。
        review_counts = re.findall(
            r"([\d][\d,.\s]*)\s*(?:customer\s+)?(?:reviews?|rezensionen|avis|recensioni|"
            r"reseñas|críticas|avaliações|valiações|comentários)",
            count_text, re.I,
        )
        if review_counts:
            digits = re.sub(r"\D", "", review_counts[-1])
            if digits:
                public_count = int(digits)
        else:
            m = re.search(r"\b(?:of|von|sur|de)\s+([\d,.]+)", count_text, re.I)
            if m:
                digits = re.sub(r"\D", "", m.group(1))
                if digits:
                    public_count = int(digits)

    return {
        "product_name": name or None,
        "average_rating": avg,
        "total_ratings": total,
        "star_percentages": pcts or None,
        "public_review_count": public_count,
    }


def parse_reviews_page(html: str, domain: str) -> dict:
    status = detect_status(html)
    if status != PageStatus.OK:
        return {"status": status, "reviews": [], "next_state": None, "csrf": "", "summary": {}}

    soup = BeautifulSoup(html, "lxml")
    rows = []
    for node in soup.select('[data-hook="review"]'):
        row = build_review_row(node, domain)
        if row["review"] or row["title"]:
            rows.append(row)

    csrf = ""
    m = re.search(r'"reviewsCsrfToken"\s*:\s*"([^"]+)"', html)
    if m:
        csrf = unquote(m.group(1))

    summary = parse_summary(soup, html)
    if not rows:
        text = soup.get_text(" ", strip=True).casefold()
        has_review_surface = bool(
            soup.select_one("#cm_cr-review_list")
            or soup.select_one('[data-hook="cr-filter-info-review-rating-count"]')
            or soup.select_one('[data-hook="average-star-rating"]')
            or "customer reviews" in (soup.title.get_text(" ", strip=True).casefold() if soup.title else "")
            or any(marker in text for marker in ("no customer reviews", "no reviews yet", "there are no reviews"))
        )
        if not has_review_surface:
            status = PageStatus.UNEXPECTED

    return {
        "status": status,
        "reviews": rows,
        "next_state": _extract_show_more(soup),
        "csrf": csrf,
        "summary": summary,
    }


def parse_ajax_body(body: str, domain: str) -> dict:
    """解析 AJAX 翻页响应(多段 JSON, 以 &&& 分隔)。"""
    rows, token = [], None
    recognized = False
    fragments = [part for part in re.split(r"\s*&&&\s*", body or "") if part.strip()]

    def strings_in(value):
        if isinstance(value, str):
            yield value
        elif isinstance(value, dict):
            for key, child in value.items():
                if key == "nextPageToken" and child:
                    yield "__NEXT_PAGE_TOKEN__" + str(child)
                yield from strings_in(child)
        elif isinstance(value, list):
            for child in value:
                yield from strings_in(child)

    for fragment in fragments:
        fragment = fragment.strip()
        payload = None
        if fragment.startswith(("[", "{")):
            try:
                payload = json.loads(fragment)
                recognized = True
            except ValueError:
                payload = None

        candidates = list(strings_in(payload)) if payload is not None else [fragment]
        for html_part in candidates:
            if html_part.startswith("__NEXT_PAGE_TOKEN__"):
                token = html_part[len("__NEXT_PAGE_TOKEN__"):]
                continue
            if not isinstance(html_part, str):
                continue
            if "data-hook" not in html_part and "show-more-button" not in html_part:
                continue
            recognized = True
            fso = BeautifulSoup(html_part, "lxml")
            for node in fso.select('[data-hook="review"]'):
                row = build_review_row(node, domain)
                if row["review"] or row["title"]:
                    rows.append(row)
            state = _extract_show_more(fso)
            if state:
                token = state["nextPageToken"]

    status = detect_status(body or "")
    return {
        "rows": rows,
        "token": token,
        "recognized": recognized,
        "status": status,
    }


# ---------------------------------------------------------------------------
# 抓取主流程
# ---------------------------------------------------------------------------


def build_url(asin: str, tld: str, page: int, star_filter: str, review_type: str) -> str:
    return (
        f"https://www.amazon.{tld}/product-reviews/{asin}/"
        f"?ie=UTF8&pageNumber={page}"
        f"&filterByStar={star_filter}&reviewerType={review_type}&sortBy=recent"
    )


def scrape_filter(client, asin: str, tld: str, star_filter: str, args, log,
                  max_reviews: int = None) -> tuple:
    """抓取一个星级筛选的评论，返回评论、首屏摘要和完整性状态。"""
    domain = f"www.amazon.{tld}"
    ajax_url = f"https://{domain}{AJAX_REVIEWS_PATH}"
    rows, seen = [], set()
    page_no = 1
    coverage = {
        "pages_fetched": 0,
        "complete": False,
        "stop_reason": "unknown",
        "error": None,
    }

    def add(new_rows) -> int:
        added = 0
        for rv in new_rows:
            key = review_identity(rv)
            if key in seen:
                continue
            if max_reviews is not None and len(rows) >= max_reviews:
                break
            seen.add(key)
            rv.update({"filter": star_filter, "page": page_no})
            rows.append(rv)
            added += 1
        return added

    # ---- 第 1 页 ----
    page1_url = build_url(asin, tld, 1, star_filter, args.review_type)
    html = client.get(page1_url)
    result = parse_reviews_page(html, domain)
    coverage["pages_fetched"] = 1
    status = result["status"]

    if status == PageStatus.SIGNIN:
        raise CookieError(
            "亚马逊要求登录 -> Cookie 已失效。\n"
            "将打开浏览器窗口, 登录一次即可自动更新 Cookie。"
        )
    if status == PageStatus.CAPTCHA:
        raise RuntimeError(
            "触发了亚马逊人机验证(Captcha)。请稍后重试; "
            "直连模式下可试 --via api, 或先 --login 重新登录。"
        )
    if status == PageStatus.UNEXPECTED:
        raise RuntimeError("评论页响应结构无法识别，停止以免把页面错误当作零评论。")
    if status == PageStatus.BLOCKED:
        if client.can_render:
            log(f"  [{star_filter}] 第 1 页评论被隐藏, 尝试浏览器渲染模式...")
            html = client.get(page1_url, render=True)
            result = parse_reviews_page(html, domain)
            if result["status"] == PageStatus.SIGNIN:
                raise CookieError("渲染后仍需登录 -> Cookie 已失效。")
            if result["status"] == PageStatus.CAPTCHA:
                raise RuntimeError("渲染时触发人机验证, 请稍后重试。")
            if result["status"] == PageStatus.BLOCKED:
                raise CookieError("评论被隐藏 -> Cookie 未生效。")
            if result["status"] == PageStatus.UNEXPECTED:
                raise RuntimeError("渲染后的评论页结构无法识别。")
        else:
            raise CookieError("评论被隐藏 -> Cookie 未生效(直连模式)。")

    added = add(result["reviews"])
    log(f"  [{star_filter}] 第 1 页: {len(result['reviews'])} 条 (新增 {added})")

    token = result["next_state"]["nextPageToken"] if result["next_state"] else None
    csrf = result["csrf"]
    stagnant = 0
    if not token:
        coverage.update({"complete": True, "stop_reason": "no_next_page"})
        return rows, result, coverage
    if max_reviews is not None and len(rows) >= max_reviews:
        coverage["stop_reason"] = "max_reviews"
        return rows, result, coverage
    if args.max_pages <= 1:
        coverage["stop_reason"] = "max_pages"
        return rows, result, coverage

    # ---- 后续页(AJAX 游标翻页) ----
    while token and page_no < args.max_pages:
        page_no += 1
        data = {
            "asin": asin,
            "pageNumber": str(page_no),
            "nextPageToken": token,
            "filterByStar": star_filter,
            "reviewerType": args.review_type,
            # 与评论页默认的最近评论顺序保持一致，避免游标和排序条件冲突。
            "sortBy": "recent",
            "formatType": "",
            "filterByKeyword": "",
            "filterByLanguage": "",
            "mediaType": "all_contents",
            "csrf": csrf,
        }
        headers = {
            "Referer": page1_url,
            "Origin": f"https://{domain}",
            "anti-csrftoken-a2z": csrf,
        }
        try:
            body = client.post_form(ajax_url, data=data, headers=headers)
        except RuntimeError as e:
            log(f"  [{star_filter}] 第 {page_no} 页 AJAX 失败: {e}")
            coverage.update({"stop_reason": "ajax_error", "error": str(e)})
            break

        ajax_status = detect_status(body)
        if ajax_status == PageStatus.SIGNIN:
            raise CookieError("AJAX 翻页要求登录 -> Cookie 已失效。")
        if ajax_status == PageStatus.CAPTCHA:
            coverage.update({"stop_reason": "captcha", "error": "AJAX 翻页触发人机验证"})
            log(f"  [{star_filter}] AJAX 翻页触发人机验证，停止抓取")
            break

        res = parse_ajax_body(body, domain)
        coverage["pages_fetched"] += 1
        if not res["recognized"]:
            coverage.update({"stop_reason": "invalid_response", "error": "AJAX 响应无法识别"})
            log(f"  [{star_filter}] 第 {page_no} 页 AJAX 响应无法识别，停止抓取")
            break
        added = add(res["rows"])
        log(f"  [{star_filter}] 第 {page_no} 页(AJAX): {len(res['rows'])} 条 "
            f"(新增 {added}, 累计 {len(rows)})")

        if added == 0:
            stagnant += 1
            if stagnant >= 2:
                coverage["stop_reason"] = "no_new_reviews"
                log(f"  [{star_filter}] 连续两页没有新评论，停止")
                break
        else:
            stagnant = 0

        if not res["token"]:
            coverage.update({"complete": True, "stop_reason": "no_next_page"})
            log(f"  [{star_filter}] 已到最后一页")
            break
        if res["token"] == token:
            coverage["stop_reason"] = "repeated_cursor"
            log(f"  [{star_filter}] 翻页游标重复，停止抓取")
            break

        if max_reviews is not None and len(rows) >= max_reviews:
            coverage["stop_reason"] = "max_reviews"
            break
        token = res["token"]

    if coverage["stop_reason"] == "unknown" and token and page_no >= args.max_pages:
        coverage["stop_reason"] = "max_pages"

    return rows, result, coverage


def run_scrape(asin: str, args, client, tld: str, log) -> dict:
    domain = f"www.amazon.{tld}"
    filters = args.filters or (STAR_FILTERS if args.deep else ["all_stars"])
    log(f"抓取策略: 筛选={filters}, 每组最多 {args.max_pages} 页, 域名={domain}, "
        f"模式={'ScraperAPI' if client.is_api else '直连'}")

    all_rows = {}
    filter_stats = []
    summary = {}
    public_count = None
    not_attempted_filters = []

    for index, star_filter in enumerate(filters):
        log(f"筛选: {star_filter}")
        remaining = args.max_reviews - len(all_rows) if args.max_reviews is not None else None
        if remaining is not None and remaining <= 0:
            not_attempted_filters = filters[index:]
            break
        try:
            rows, page1, coverage = scrape_filter(
                client, asin, tld, star_filter, args, log, max_reviews=remaining
            )
        except CookieError:
            raise
        except RuntimeError as e:
            log(f"  [{star_filter}] 失败: {e}")
            rows, page1 = [], None
            coverage = {
                "pages_fetched": 0,
                "complete": False,
                "stop_reason": "request_error",
                "error": str(e),
            }

        if not summary and page1:
            summary = {k: v for k, v in (page1.get("summary") or {}).items() if v}
            public_count = (page1.get("summary") or {}).get("public_review_count")

        added = 0
        for rv in rows:
            key = review_identity(rv)
            if key not in all_rows:
                all_rows[key] = rv
                added += 1
        filter_stats.append({
            "filter": star_filter,
            "fetched": len(rows),
            "new": added,
            **coverage,
        })
        log(f"  -> 新增 {added} 条, 去重后累计 {len(all_rows)} 条 "
            f"(已用 {client.requests_made} 次请求)")
        if args.max_reviews is not None and len(all_rows) >= args.max_reviews:
            not_attempted_filters = filters[index + 1:]
            break

    if summary.get("product_name"):
        log(f"商品: {summary['product_name']}")
    if summary.get("average_rating") is not None:
        log(f"评分: {summary['average_rating']} / 5, 共 {summary.get('total_ratings')} 个评分")

    pagination_complete = (
        bool(filter_stats)
        and not not_attempted_filters
        and all(item.get("complete") for item in filter_stats)
    )
    incomplete_reasons = [
        f"{item['filter']}: {item.get('stop_reason')}"
        for item in filter_stats
        if not item.get("complete") and item.get("stop_reason") != "max_reviews"
    ]
    if not_attempted_filters:
        incomplete_reasons.append("未尝试筛选: " + ", ".join(not_attempted_filters))
    if any(item.get("stop_reason") == "max_reviews" for item in filter_stats):
        incomplete_reasons.append(f"达到 --max-reviews 上限 {args.max_reviews}")
    comparable_count = (
        public_count is not None
        and args.review_type == "all_reviews"
        and "all_stars" in filters
    )
    count_matches = len(all_rows) == public_count if comparable_count else None
    if pagination_complete and count_matches is False:
        incomplete_reasons.append("抓取条数与页面显示的公开评论数不一致")
    complete = pagination_complete and count_matches is not False

    return {
        "asin": asin,
        "domain": domain,
        "mode": "api" if client.is_api else "direct",
        "scraped_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "requests_used": client.requests_made,
        "filters": filter_stats,
        "filters_requested": filters,
        "filters_not_attempted": not_attempted_filters,
        "pagination_complete": pagination_complete,
        "count_matches_public_review_count": count_matches,
        "collection_complete": complete,
        "collection_status": "complete" if complete else "partial",
        "collection_notes": incomplete_reasons,
        "public_review_count": public_count,
        **summary,
        "review_count": len(all_rows),
        "reviews": list(all_rows.values()),
    }


def scrape_all(asin: str, args, log) -> dict:
    logins_used = 0

    while True:
        cookies_path, tld = choose_cookies_path(args.cookies_file, args)
        cookies = "" if args.anonymous else read_cookie_line(cookies_path)

        if not args.anonymous and ("=" not in cookies):
            # 该站点还没有 Cookie -> 触发登录流程
            if logins_used >= 1:
                raise RuntimeError(
                    f"没有可用 Cookie ({cookies_path.resolve()}) 且登录未成功。"
                )
            log(f"站点 amazon.{tld} 还没有登录 Cookie ({cookies_path.name}), "
                "需要先登录一次")
            cookies_path = _do_login(tld, cookies_path, args, log)
            logins_used += 1
            continue

        if args.via == "api":
            api_key = load_api_key(args.api_key_file)
            client = ApiClient(api_key, cookies=cookies, delay=args.delay,
                               session_number=args.session, cookies_path=cookies_path)
        else:
            client = DirectClient(cookies, tld, delay=args.delay, cookies_path=cookies_path)
        if cookies:
            log(f"Cookie: {len(cookies)} 字符 | 站点: amazon.{tld} | "
                f"模式: {'ScraperAPI' if client.is_api else '直连(不耗额度)'}")

        try:
            result = run_scrape(asin, args, client, tld, log)
        except CookieError as e:
            if args.anonymous:
                raise RuntimeError(str(e))
            if logins_used >= 1:
                raise RuntimeError(f"{e}\n(已尝试重新登录一次仍失败, 请检查账号状态)")
            log("")
            log(f"!! {e}")
            cookies_path = _do_login(tld, cookies_path, args, log)
            logins_used += 1
            continue

        client.persist_cookies()
        return result


def _do_login(tld: str, cookies_path: Path, args, log) -> Path:
    """弹出浏览器窗口登录(交互时先询问, 非交互时直接打开), 返回保存后的 Cookie 路径。"""
    if has_interactive_stdin():
        try:
            input("按回车打开浏览器登录 (Ctrl+C 取消) ...")
        except KeyboardInterrupt:
            raise SystemExit("\n已取消。可稍后单独运行: python amazon_reviews.py --login")
        except EOFError:
            log("无法读取键盘输入, 直接打开登录窗口 ...")
    else:
        log("自动打开浏览器登录窗口 ...")
    try:
        interactive_login(tld, cookies_path, log, wait_seconds=args.login_wait)
    except RuntimeError as e:
        raise RuntimeError(str(e))
    return cookies_path


# ---------------------------------------------------------------------------
# 浏览器登录 (Playwright): 登录一次, 自动保存 Cookie
# ---------------------------------------------------------------------------


def interactive_login(tld: str, cookies_path: Path, log, start_url: str = None,
                      wait_seconds: int = 900):
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise SystemExit(
            "需要 playwright 才能打开登录窗口:\n"
            "    pip install playwright && playwright install chromium"
        )

    url = start_url or f"https://www.amazon.{tld}/gp/sign-in.html"
    locale = SITE_LANG.get(tld, "en-US").replace("-", "_")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        ctx = browser.new_context(user_agent=CHROME_UA, locale=locale)
        # 带上旧 Cookie 预热, 减少二次验证
        old = cookie_dict(read_cookie_line(cookies_path))
        if old:
            try:
                ctx.add_cookies([
                    {"name": k, "value": v, "domain": f".amazon.{tld}", "path": "/"}
                    for k, v in old.items()
                ])
            except Exception:
                pass
        page = ctx.new_page()
        def _login_surface_state() -> dict:
            try:
                return page.evaluate(
                    r"""() => {
                        const body = document.body;
                        const text = body ? (body.innerText || '').trim() : '';
                        return {
                            bodyLength: text.length,
                            hasLoginForm: !!document.querySelector(
                                '#ap_email, input[name="email"], #ap_password, form[name="signIn"]'
                            ),
                            hasAccountNav: !!document.querySelector('#nav-link-accountList'),
                            hasCaptcha: !!document.querySelector('#captchacharacters'),
                        };
                    }"""
                ) or {}
            except Exception:
                return {}

        page_loaded = False
        for attempt in range(2):
            response = None
            try:
                response = page.goto(url, wait_until="domcontentloaded", timeout=45000)
            except Exception as e:
                if not browser.is_connected() or page.is_closed():
                    raise RuntimeError("登录浏览器窗口已关闭，登录已取消。") from e
                log(f"打开登录页失败（第 {attempt + 1} 次）: {e}")

            try:
                page.wait_for_function(
                    """() => {
                        const body = document.body;
                        return !!body && (
                            (body.innerText || '').trim().length > 0 ||
                            !!document.querySelector(
                                '#ap_email, input[name="email"], #ap_password, '
                                'form[name="signIn"], #nav-link-accountList, #captchacharacters'
                            )
                        );
                    }""",
                    timeout=15000,
                )
            except Exception:
                pass

            if not browser.is_connected() or page.is_closed():
                raise RuntimeError("登录浏览器窗口已关闭，登录已取消。")
            state = _login_surface_state()
            try:
                page_status = detect_status(page.content())
            except Exception as e:
                if not browser.is_connected() or page.is_closed():
                    raise RuntimeError("登录浏览器窗口已关闭，登录已取消。") from e
                raise RuntimeError(f"无法读取 Amazon 登录页：{e}") from e
            if state.get("hasCaptcha") or page_status == PageStatus.CAPTCHA:
                browser.close()
                raise RuntimeError("Amazon 登录页出现 Robot Check/CAPTCHA，已停止等待；请稍后重试。")

            if response is not None:
                log(f"登录页 HTTP 状态: {response.status}")
            page_loaded = bool(
                state.get("bodyLength", 0)
                or state.get("hasLoginForm")
                or state.get("hasAccountNav")
            )
            if page_loaded and (response is None or response.status < 400):
                break
            if attempt == 0:
                log("登录页仍为空白或返回错误，正在重新打开一次…")

        if not page_loaded or (response is not None and response.status >= 400):
            status_text = f"HTTP {response.status}" if response is not None else "无 HTTP 响应"
            browser.close()
            raise RuntimeError(
                f"Amazon 登录页未能正常加载（{status_text}）：{page.url}\n"
                "请检查网络或代理后重试；本次不会继续空等登录。"
            )

        log("=" * 64)
        log("登录页已加载，请在浏览器窗口中登录亚马逊账号。")

        # 记录注入的旧登录态, 用于判断"是否产生了新的登录动作"
        try:
            initial = {c["name"]: c["value"] for c in ctx.cookies()}
        except Exception as e:
            raise RuntimeError(f"登录浏览器已关闭或连接中断，登录已取消：{e}") from e

        def _new_login(cks) -> bool:
            """出现了新的 / 被更新的登录态 Cookie(at-/sess-at-/sst-)。"""
            for c in cks:
                n = c.get("name", "")
                if n.startswith(("at-", "sess-at-", "sst-")) and initial.get(n) != c.get("value"):
                    return True
            return False

        def _has_login(cks) -> bool:
            return any(c.get("name", "").startswith(("at-", "sess-at-", "sst-")) for c in cks)

        def _page_logged_in() -> bool:
            """通过页面导航栏判断是否已登录(Hello, xxx / 非 Sign in)。"""
            try:
                return bool(page.evaluate(
                    r"""() => {
                        const el = document.querySelector('#nav-link-accountList');
                        if (!el) return false;
                        const t = (el.textContent || '').trim();
                        return t.length > 0 && !/(sign\s*in|log\s*in|login|登录|登入|ログイン|anmelden|iniciar sesión|connexion)/i.test(t);
                    }"""
                ))
            except Exception:
                return False

        log("登录完成会自动识别，无需按回车；关闭浏览器窗口会取消登录。")
        log(f"自动等待登录完成，最长 {wait_seconds} 秒…")
        log("=" * 64)
        waited = 0
        while waited < wait_seconds:
            time.sleep(1)
            waited += 1
            if not browser.is_connected() or page.is_closed():
                raise RuntimeError("登录浏览器窗口已关闭，登录已取消。")
            try:
                cks = ctx.cookies()
            except Exception as e:
                raise RuntimeError(f"登录浏览器已关闭或连接中断，登录已取消：{e}") from e
            if _new_login(cks) or _page_logged_in():
                log(f"检测到登录成功 (用时 {waited} 秒)")
                break
            if waited % 30 == 0:
                log(f"  ... 已等待 {waited} 秒, 请继续在浏览器中登录")
        else:
            if not browser.is_connected() or page.is_closed():
                raise RuntimeError("登录浏览器窗口已关闭，登录已取消。")
            try:
                cookies_at_timeout = ctx.cookies()
            except Exception as e:
                raise RuntimeError(f"登录浏览器已关闭或连接中断，登录已取消：{e}") from e
            if not _has_login(cookies_at_timeout):
                browser.close()
                raise RuntimeError(
                    f"等待 {wait_seconds} 秒仍未检测到登录态, 登录未完成。"
                    "请重试 --login, 或加长 --login-wait"
                )
            log("未检测到新的登录动作, 但已有登录态 Cookie, 将直接保存。")

        # Cookie 可能在页面加载后才写入, 轮询最多等 20 秒
        cookies, waited = [], 0.0
        while waited < 20:
            if not browser.is_connected() or page.is_closed():
                raise RuntimeError("登录浏览器在 Cookie 保存前关闭，登录已取消。")
            try:
                cookies = ctx.cookies()
            except Exception as e:
                raise RuntimeError(f"读取 Cookie 时浏览器已关闭或连接中断：{e}") from e
            if any("amazon." in (c.get("domain") or "") for c in cookies):
                break
            time.sleep(1)
            waited += 1
        browser.close()

    line = "; ".join(f'{c["name"]}={c["value"]}' for c in cookies
                     if "amazon." in (c.get("domain") or ""))
    if not line:
        raise RuntimeError("没有读取到亚马逊 Cookie, 登录可能未完成, 请重试 --login")

    logged_in = any(c["name"].startswith(("at-", "sst-")) for c in cookies)
    save_cookie_line(cookies_path, line)
    log(f"Cookie 已保存到 {cookies_path.resolve()} ({len(line)} 字符)"
        + ("" if logged_in else "  [警告: 未检测到登录态标记, 可能未登录成功]"))
    return logged_in


# ---------------------------------------------------------------------------
# 输出: CSV / JSON / Excel
# ---------------------------------------------------------------------------


def save_csv(rows: list, path: Path):
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows({
            key: _spreadsheet_value(row.get(key)) for key in CSV_COLUMNS
        } for row in rows)


def _spreadsheet_value(value, list_separator=" | "):
    if isinstance(value, (list, tuple)):
        value = list_separator.join(str(item) for item in value if item is not None)
    if isinstance(value, str) and re.match(r"^\s*[=+\-@]", value):
        return "'" + value
    return value


def save_json(data: dict, path: Path):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def save_xlsx(data: dict, path: Path):
    """把 JSON 数据解析成 Excel: '评论' 表 + '概览' 表。"""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    reviews = data.get("reviews") or []
    wb = Workbook()

    # ---- 评论表 ----
    ws = wb.active
    ws.title = "评论"
    ws.append(CSV_COLUMNS)
    head_fill = PatternFill("solid", fgColor="1F4E78")
    for cell in ws[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = head_fill
        cell.alignment = Alignment(vertical="center")
    media_columns = {"image_urls", "video_urls", "video_poster_urls"}
    for rv in reviews:
        ws.append([
            _spreadsheet_value(rv.get(c), list_separator="\n" if c in media_columns else " | ")
            for c in CSV_COLUMNS
        ])

    widths = {}
    for row in ws.iter_rows(min_row=2):
        for cell in row:
            col = cell.column
            val = str(cell.value) if cell.value is not None else ""
            widths[col] = max(widths.get(col, 10), min(len(val) + 2, 60))
            if CSV_COLUMNS[cell.column - 1] in (
                "review", "title", "date", "image_urls", "video_urls", "video_poster_urls"
            ):
                cell.alignment = Alignment(wrap_text=True, vertical="top")
    for col in range(1, len(CSV_COLUMNS) + 1):
        letter = get_column_letter(col)
        name = CSV_COLUMNS[col - 1]
        if name in media_columns:
            ws.column_dimensions[letter].width = 42
            continue
        default_w = 50 if name == "review" else (
            28 if name in ("title", "date", "review_url") else
            14
        )
        ws.column_dimensions[letter].width = max(widths.get(col, 10), min(default_w, 60))
    ws.freeze_panes = "A2"
    if reviews:
        ws.auto_filter.ref = ws.dimensions

    # ---- 概览表 ----
    ws2 = wb.create_sheet("概览")
    ws2.append(["项目", "值"])
    for cell in ws2[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = head_fill

    public_review_count = data.get("public_review_count")
    review_scope = (
        f"{data.get('review_count')}/{public_review_count}"
        if public_review_count is not None else ""
    )

    # 星级分布
    dist = {s: 0 for s in range(1, 6)}
    for rv in reviews:
        try:
            dist[int(float(rv.get("stars") or 0))] += 1
        except (TypeError, ValueError):
            pass

    kv = [
        ("ASIN", data.get("asin")),
        ("站点", data.get("domain")),
        ("抓取模式", {"direct": "直连", "api": "ScraperAPI"}.get(data.get("mode"), data.get("mode"))),
        ("商品名称", data.get("product_name")),
        ("平均评分", data.get("average_rating")),
        ("评分数", data.get("total_ratings")),
        ("公开评论数", data.get("public_review_count")),
        ("本次抓取评论数", data.get("review_count")),
        ("抓取评论/页面显示评论", review_scope),
        ("抓取完整性", (
            "完整" if data.get("collection_complete") is True else
            "部分或受限" if data.get("collection_complete") is False else "未记录"
        )),
        ("未完整原因", "；".join(data.get("collection_notes") or [])),
        ("抓取时间", data.get("scraped_at")),
        ("消耗请求数", data.get("requests_used")),
        ("", ""),
        ("星级分布", ""),
    ]
    for s in range(5, 0, -1):
        kv.append((f"  {s} 星", f"{dist[s]} 条"))
    pcts = data.get("star_percentages") or {}
    if pcts:
        kv.append(("", ""))
        kv.append(("官方星级占比", ""))
        for s in range(5, 0, -1):
            v = pcts.get(f"{s}_star")
            if v is not None:
                kv.append((f"  {s} 星", f"{v}%"))
    if data.get("filters"):
        kv.append(("", ""))
        kv.append(("筛选统计", ""))
        for f in data["filters"]:
            kv.append((f"  {f.get('filter')}", f"抓取 {f.get('fetched')} 条, 新增 {f.get('new')} 条"))

    for k, v in kv:
        ws2.append([_spreadsheet_value(k), _spreadsheet_value(v)])
    ws2.column_dimensions["A"].width = 22
    ws2.column_dimensions["B"].width = 80
    for row in ws2.iter_rows(min_row=2):
        row[1].alignment = Alignment(wrap_text=True, vertical="top")

    wb.save(path)


def run_collection(asin: str, args, log):
    """抓取并保存一组评论，供命令行和桌面界面共用。"""
    if args.login:
        cookies_path, tld = choose_cookies_path(args.cookies_file, args)
        log(f"目标站点: amazon.{tld} -> Cookie 文件 {cookies_path.name}")
        interactive_login(tld, cookies_path, log, wait_seconds=args.login_wait)

    result = scrape_all(asin, args, log)
    outdir = Path(args.outdir) / asin
    outdir.mkdir(parents=True, exist_ok=True)
    csv_path = outdir / f"{asin}_reviews.csv"
    json_path = outdir / f"{asin}_reviews.json"
    xlsx_path = outdir / f"{asin}_reviews.xlsx"

    save_csv(result["reviews"], csv_path)
    save_json(result, json_path)
    save_xlsx(result, xlsx_path)

    log("")
    log("=" * 64)
    log(f"完成! 共 {result['review_count']} 条评论, "
        f"模式={'ScraperAPI' if result['mode'] == 'api' else '直连(0 额度)'}")
    log(f"消耗 {result['requests_used']} 次请求"
        + (" (ScraperAPI 额度)" if result["mode"] == "api" else " (不耗 API 额度)"))
    if result.get("public_review_count") is not None:
        log(f"页面显示公开评论数 {result['public_review_count']}, "
            f"本次收集 {result['review_count']} 条")
    if not result["reviews"]:
        log("没有采集到评论行；请结合抓取状态和筛选错误判断，不能仅据此认定没有评论。")
    log(f"抓取完整性: {'完整' if result.get('collection_complete') else '部分或受限'}")
    for note in result.get("collection_notes") or []:
        log(f"  - {note}")
    log(f"CSV : {csv_path.resolve()}")
    log(f"JSON: {json_path.resolve()}")
    log(f"Excel: {xlsx_path.resolve()}")
    log("=" * 64)
    return result, (csv_path, json_path, xlsx_path)


def run_batch_collection(asins, args, log):
    """逐个抓取多个 ASIN；单个商品失败时记录结果并继续下一项。"""
    unique_asins = []
    for asin in asins:
        asin = normalize_asin(asin)
        if asin not in unique_asins:
            unique_asins.append(asin)
    if not unique_asins:
        raise ValueError("没有找到有效 ASIN。")

    if args.login:
        cookies_path, tld = choose_cookies_path(args.cookies_file, args)
        log(f"先登录站点 amazon.{tld}，本批次后续 ASIN 会复用 Cookie。")
        interactive_login(tld, cookies_path, log, wait_seconds=args.login_wait)
        args.login = False

    outcomes = []
    stop_fragments = (
        "登录", "Cookie", "验证码", "CAPTCHA", "Robot Check", "被亚马逊拦截", "HTTP 429",
    )
    for index, asin in enumerate(unique_asins, start=1):
        log("")
        log(f"批量任务 {index}/{len(unique_asins)}：{asin}")
        try:
            result, paths = run_collection(asin, args, log)
            outcomes.append({
                "asin": asin,
                "review_count": result["review_count"],
                "collection_complete": result.get("collection_complete"),
                "paths": [str(path) for path in paths],
            })
        except Exception as exc:
            message = str(exc)
            outcomes.append({"asin": asin, "error": message})
            log(f"[{asin}] 抓取失败：{message}")
            if any(fragment.casefold() in message.casefold() for fragment in stop_fragments):
                remaining = unique_asins[index:]
                if remaining:
                    log("登录或站点访问状态异常，停止批次；尚未处理：" + ", ".join(remaining))
                break

    successes = sum("error" not in item for item in outcomes)
    failures = sum("error" in item for item in outcomes)
    summary = {
        "total": len(unique_asins),
        "successes": successes,
        "failures": failures,
        "skipped": len(unique_asins) - len(outcomes),
        "outcomes": outcomes,
    }
    log(
        f"批次结束：成功 {summary['successes']}/{summary['total']}，"
        f"失败 {summary['failures']}，跳过 {summary['skipped']}。"
    )
    return summary


def launch_gui(initial_args):
    """启动 ASIN 与站点选择窗口；抓取运行在后台线程中。"""
    try:
        import tkinter as tk
        from tkinter import filedialog, messagebox, ttk
        from tkinter.scrolledtext import ScrolledText
    except ImportError as exc:
        raise SystemExit(f"当前 Python 缺少 Tkinter，无法启动桌面界面: {exc}")

    script_dir = Path(__file__).resolve().parent
    if initial_args.cookies_file == DEFAULT_COOKIES_FILE:
        default_cookie_file = str(script_dir / DEFAULT_COOKIES_FILE)
    else:
        default_cookie_file = initial_args.cookies_file
    if initial_args.outdir == ".":
        default_outdir = str(script_dir / "output")
    else:
        output_path = Path(initial_args.outdir)
        default_outdir = str(output_path if output_path.is_absolute() else script_dir / output_path)

    root = tk.Tk()
    root.title("Amazon 评论抓取工具")
    root.geometry("880x680")
    root.minsize(760, 560)

    frame = ttk.Frame(root, padding=18)
    frame.pack(fill="both", expand=True)
    frame.columnconfigure(1, weight=1)
    frame.rowconfigure(5, weight=1)

    initial_asins = initial_args.asin or []
    if isinstance(initial_asins, str):
        initial_asin_text = initial_asins
    else:
        initial_asin_text = "\n".join(str(item) for item in initial_asins)
    outdir_var = tk.StringVar(value=default_outdir)
    marketplace_labels = [label for label, _ in GUI_MARKETPLACES]
    marketplace_to_site = {label: site for label, site in GUI_MARKETPLACES}
    initial_site = initial_args.tld or SITES.get(initial_args.site, initial_args.site)
    selected_label = next(
        (label for label, site in GUI_MARKETPLACES if site == initial_site),
        GUI_MARKETPLACES[0][0],
    )
    site_var = tk.StringVar(value=selected_label)
    status_var = tk.StringVar(value="就绪。选择站点并输入 ASIN 后开始。")
    events = queue.Queue()
    busy = {"value": False}

    ttk.Label(frame, text="ASIN / 商品链接 / 文本").grid(
        row=0, column=0, sticky="w", padx=(0, 12), pady=7
    )
    asin_input = tk.Text(frame, height=3, wrap="word")
    asin_input.insert("1.0", initial_asin_text)
    asin_input.grid(row=0, column=1, columnspan=2, sticky="ew", pady=7)

    ttk.Label(frame, text="Amazon 站点").grid(row=1, column=0, sticky="w", padx=(0, 12), pady=7)
    site_combo = ttk.Combobox(
        frame, textvariable=site_var, values=marketplace_labels, state="readonly", width=32
    )
    site_combo.grid(row=1, column=1, sticky="w", pady=7)

    ttk.Label(frame, text="输出根目录（按 ASIN 分文件夹）").grid(
        row=2, column=0, sticky="w", padx=(0, 12), pady=7
    )
    outdir_entry = ttk.Entry(frame, textvariable=outdir_var)
    outdir_entry.grid(row=2, column=1, sticky="ew", pady=7)

    def browse_output():
        chosen = filedialog.askdirectory(
            title="选择评论文件的保存目录",
            initialdir=outdir_var.get() or str(script_dir),
        )
        if chosen:
            outdir_var.set(chosen)

    browse_button = ttk.Button(frame, text="浏览…", command=browse_output)
    browse_button.grid(row=2, column=2, sticky="ew", padx=(8, 0), pady=7)

    ttk.Label(
        frame,
        text="可粘贴多行 ASIN 或商品链接；程序会自动提取并去重。缺少 Cookie 时会打开浏览器登录。",
        foreground="#555555",
    ).grid(row=3, column=0, columnspan=3, sticky="w", pady=(3, 12))

    button_frame = ttk.Frame(frame)
    button_frame.grid(row=4, column=0, columnspan=3, sticky="ew", pady=(0, 10))
    status_label = ttk.Label(button_frame, textvariable=status_var)
    status_label.pack(side="right", padx=(12, 0))

    log_frame = ttk.LabelFrame(frame, text="运行日志", padding=8)
    log_frame.grid(row=5, column=0, columnspan=3, sticky="nsew")
    log_frame.rowconfigure(0, weight=1)
    log_frame.columnconfigure(0, weight=1)
    log_view = ScrolledText(log_frame, wrap="word", height=18, state="disabled")
    log_view.grid(row=0, column=0, sticky="nsew")

    def append_log(message):
        log_view.configure(state="normal")
        log_view.insert("end", str(message) + "\n")
        log_view.see("end")
        log_view.configure(state="disabled")

    def selected_site():
        return marketplace_to_site.get(site_var.get(), "auto")

    def make_job_args():
        args = argparse.Namespace(**vars(initial_args))
        args.gui = False
        args.asin = asin_input.get("1.0", "end-1c").strip()
        args.site = selected_site()
        args.tld = None
        args.cookies_file = default_cookie_file
        args.site_specific_cookies = initial_args.cookies_file == DEFAULT_COOKIES_FILE
        args.outdir = outdir_var.get().strip() or default_outdir
        return args

    def set_busy(value):
        busy["value"] = value
        start_button.configure(state="disabled" if value else "normal")
        login_button.configure(state="disabled" if value else "normal")
        asin_input.configure(state="disabled" if value else "normal")
        site_combo.configure(state="disabled" if value else "readonly")
        outdir_entry.configure(state="disabled" if value else "normal")
        browse_button.configure(state="disabled" if value else "normal")

    def start_scrape():
        if busy["value"]:
            return
        asins = extract_asins(asin_input.get("1.0", "end-1c"))
        if not asins:
            messagebox.showerror(
                "未找到 ASIN",
                "请输入 ASIN、商品链接，或粘贴包含 10 位 ASIN 的文本。",
                parent=root,
            )
            return
        job_args = make_job_args()
        job_args.asin = asins
        status_var.set(f"正在批量抓取 {len(asins)} 个 ASIN…")
        append_log(f"开始处理 {len(asins)} 个唯一 ASIN，站点：{site_var.get()}")
        set_busy(True)

        def worker():
            try:
                summary = run_batch_collection(
                    asins, job_args, lambda message: events.put(("log", message))
                )
                events.put(("batch_done", summary))
            except SystemExit as exc:
                events.put(("error", str(exc)))
            except Exception as exc:
                events.put(("error", str(exc)))

        threading.Thread(target=worker, daemon=True).start()

    def login_selected_site():
        if busy["value"]:
            return
        job_args = make_job_args()
        try:
            cookies_path, tld = choose_cookies_path(job_args.cookies_file, job_args)
        except Exception as exc:
            messagebox.showerror("Cookie 配置错误", str(exc), parent=root)
            return
        status_var.set(f"正在打开 Amazon.{tld} 登录窗口…")
        append_log(f"准备登录 amazon.{tld}，Cookie 文件：{cookies_path}")
        set_busy(True)

        def worker():
            try:
                interactive_login(
                    tld, cookies_path,
                    lambda message: events.put(("log", message)),
                    wait_seconds=job_args.login_wait,
                )
                events.put(("login_done", cookies_path))
            except SystemExit as exc:
                events.put(("error", str(exc)))
            except Exception as exc:
                events.put(("error", str(exc)))

        threading.Thread(target=worker, daemon=True).start()

    start_button = ttk.Button(button_frame, text="开始抓取", command=start_scrape)
    start_button.pack(side="left")
    login_button = ttk.Button(button_frame, text="登录 / 更新 Cookie", command=login_selected_site)
    login_button.pack(side="left", padx=(8, 0))

    def poll_events():
        while True:
            try:
                kind, payload = events.get_nowait()
            except queue.Empty:
                break
            if kind == "log":
                append_log(payload)
            elif kind == "batch_done":
                summary = payload
                status_var.set(
                    f"批量结束：成功 {summary['successes']}，失败 {summary['failures']}，"
                    f"跳过 {summary['skipped']}"
                )
                set_busy(False)
            elif kind == "login_done":
                status_var.set("Cookie 已保存，可以开始抓取。")
                append_log(f"Cookie 已保存：{payload}")
                set_busy(False)
            elif kind == "error":
                status_var.set("任务失败，请查看错误信息。")
                append_log("错误：" + payload)
                messagebox.showerror("任务失败", payload, parent=root)
                set_busy(False)
        root.after(120, poll_events)

    def close_window():
        if busy["value"]:
            messagebox.showwarning("任务处理中", "请等待当前任务结束后再关闭窗口。", parent=root)
            return
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", close_window)
    root.after(120, poll_events)
    asin_input.focus_set()
    root.mainloop()


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------


def parse_args():
    def positive_int(value):
        try:
            parsed = int(value)
        except ValueError:
            raise argparse.ArgumentTypeError("请输入正整数")
        if parsed < 1:
            raise argparse.ArgumentTypeError("必须大于 0")
        return parsed

    def nonnegative_float(value):
        try:
            parsed = float(value)
        except ValueError:
            raise argparse.ArgumentTypeError("请输入非负数字")
        if parsed < 0:
            raise argparse.ArgumentTypeError("不能小于 0")
        return parsed

    p = argparse.ArgumentParser(
        description="亚马逊全部评论爬虫(直连 / ScraperAPI 可选, 输出 CSV+JSON+Excel)")
    p.add_argument(
        "asin", nargs="*",
        help="一个或多个 ASIN、商品链接或含 ASIN 的文本，例如 B000MS727E B000MS727F",
    )
    p.add_argument("--gui", action="store_true", help="打开桌面界面输入 ASIN 并选择站点")
    p.add_argument("--login", action="store_true",
                   help="打开浏览器窗口登录亚马逊并保存 Cookie (可单独执行)")
    p.add_argument("--login-wait", type=positive_int, default=900,
                   help="非交互环境下等待登录完成的最长秒数 (默认 900)")
    p.add_argument("--from-json", metavar="FILE",
                   help="把已有的 JSON 文件解析成 Excel 后退出")
    p.add_argument("--site", default="auto",
                   help="站点: auto(默认, 按 Cookie 识别) / ca us uk de fr jp in au ...")
    p.add_argument("--tld", default=None,
                   help="直接指定域名后缀(优先于 --site), 如 com / co.uk / com.mx")
    p.add_argument("--via", choices=["direct", "api"], default="direct",
                   help="direct=直接爬取不耗额度(默认); api=走 ScraperAPI")
    p.add_argument("--api-key-file", default=DEFAULT_API_KEY_FILE,
                   help=f"ScraperAPI 的 api_key 文件 (--via api 时用, 默认 {DEFAULT_API_KEY_FILE})")
    p.add_argument("--cookies-file", default=DEFAULT_COOKIES_FILE,
                   help=f"Cookie 文件 (默认 {DEFAULT_COOKIES_FILE})")
    p.add_argument("--anonymous", action="store_true", help="不带 Cookie 匿名抓(基本拿不到)")
    p.add_argument("--max-pages", type=positive_int, default=50,
                   help="每种筛选最多翻页数 (默认 50)")
    p.add_argument("--max-reviews", type=positive_int, default=None,
                   help="最多收集多少条评论(成本控制)")
    p.add_argument("--delay", type=nonnegative_float, default=1.0,
                   help="两次请求最小间隔秒数 (默认 1)")
    p.add_argument("--session", type=int, default=1, help="ScraperAPI 粘性会话号 (--via api)")
    p.add_argument("--review-type", default="all_reviews",
                   choices=["all_reviews", "avp_only_reviews"],
                   help="all_reviews=全部, avp_only_reviews=仅已验证购买")
    p.add_argument("--filters", nargs="+", choices=STAR_FILTERS, default=None,
                   help="指定星级筛选, 例如 all_stars five_star")
    p.add_argument("--deep", action="store_true",
                   help="深度模式: 额外按 1~5 星分别翻页(请求数约 x6, 覆盖更全)")
    p.add_argument("--outdir", default=".", help="输出根目录；每个 ASIN 保存到独立子文件夹")
    return p.parse_args()


def main():
    args = parse_args()

    if args.gui or (not args.asin and not args.from_json and not args.login):
        launch_gui(args)
        return

    def log(msg: str):
        print(msg, flush=True)

    # ---- JSON 转 Excel ----
    if args.from_json:
        src = Path(args.from_json)
        if not src.exists():
            raise SystemExit(f"错误: 找不到 {src}")
        data = json.loads(src.read_text(encoding="utf-8"))
        out = src.with_suffix(".xlsx")
        save_xlsx(data, out)
        print(f"已生成 Excel: {out.resolve()} "
              f"({len(data.get('reviews') or [])} 条评论)")
        return

    # ---- 单独登录 ----
    if args.login and not args.asin:
        cookies_path, tld = choose_cookies_path(args.cookies_file, args)
        log(f"目标站点: amazon.{tld} -> Cookie 文件 {cookies_path.name}")
        try:
            interactive_login(tld, cookies_path, log, wait_seconds=args.login_wait)
        except RuntimeError as e:
            raise SystemExit(f"\n登录失败: {e}")
        return

    asins = extract_asins(args.asin)
    if not asins:
        raise SystemExit(
            "没有找到有效 ASIN。可传入一个或多个 ASIN、商品链接或含 ASIN 的文本。"
        )

    try:
        summary = run_batch_collection(asins, args, log)
    except RuntimeError as e:
        raise SystemExit(f"\n抓取失败: {e}")
    return 1 if summary["failures"] or summary["skipped"] else 0


if __name__ == "__main__":
    sys.exit(main())
