import re
import time
import requests
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse
from concurrent.futures import ThreadPoolExecutor

BASE_URL = "https://telik.live/"
HOST = "telik.live"
MANUAL_OUT = "manual.m3u"  # check.py бере цей файл і додає канали в playlist.m3u
M3U8_RE = re.compile(r"https?://[^\s'\"<>\\]+?\.m3u8[^\s'\"<>\\]*")
IFRAME_RE = re.compile(r"<iframe[^>]+src=['\"]([^'\"]+)['\"]", re.I)

HEADERS = {"User-Agent": "Mozilla/5.0"}

# службові сторінки сайту, які не є каналами
SKIP_WORDS = ("login", "register", "contact", "dmca", "about", "policy",
              "terms", "feedback", "sitemap", "faq", "#", "javascript:")


class LinkParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []
        self._href = None
        self._title = ""
        self._text = ""

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            a = dict(attrs)
            self._href = a.get("href", "")
            self._title = a.get("title", "")
            self._text = ""

    def handle_data(self, data):
        if self._href is not None:
            self._text += data

    def handle_endtag(self, tag):
        if tag == "a" and self._href:
            name = (self._title or self._text).strip()
            self.links.append((self._href, " ".join(name.split())))
        if tag == "a":
            self._href = None


def get_catalog():
    print("Завантажую каталог Telik...")
    r = requests.get(BASE_URL, headers=HEADERS, timeout=25)
    r.raise_for_status()

    parser = LinkParser()
    parser.feed(r.text)

    channels = {}
    for href, name in parser.links:
        if any(w in href.lower() for w in SKIP_WORDS):
            continue
        url = urljoin(BASE_URL, href)
        parsed = urlparse(url)
        if parsed.netloc != HOST:
            continue
        if parsed.path in ("", "/"):
            continue
        if not name:
            name = parsed.path.strip("/").split("/")[-1]
        channels.setdefault(url.split("#")[0], name)
    return channels


def grab(url, referer=None):
    headers = dict(HEADERS)
    if referer:
        headers["Referer"] = referer
    r = requests.get(url, headers=headers, timeout=20)
    r.raise_for_status()
    return r.text.replace("\\/", "/")


def find_stream(item):
    page, name = item
    try:
        html = grab(page)
        urls = M3U8_RE.findall(html)
        if not urls:
            # плеєр часто лежить в iframe — заходимо всередину
            for src in IFRAME_RE.findall(html)[:3]:
                src = urljoin(page, src)
                try:
                    urls = M3U8_RE.findall(grab(src, referer=page))
                except Exception:
                    continue
                if urls:
                    break
        return (name, urls[0]) if urls else None
    except Exception:
        return None


def main():
    channels = get_catalog()
    print("Знайдено сторінок:", len(channels))

    with ThreadPoolExecutor(max_workers=15) as pool:
        found = [x for x in pool.map(find_stream, channels.items()) if x]

    with open(MANUAL_OUT, "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for name, url in found:
            f.write(f'#EXTINF:-1 group-title="Telik",{name}\n{url}\n')

    print("Потоків знайдено:", len(found))
    print("Готово:", MANUAL_OUT, int(time.time()))


if __name__ == "__main__":
    main()
