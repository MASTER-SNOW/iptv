
import json
import re
import time
import requests
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse
from concurrent.futures import ThreadPoolExecutor

BASE_URL = "https://live.viks.tv/"
OUTPUT = "viks_report.json"
MANUAL_OUT = "manual.m3u"  # check.py бере цей файл і додає канали в playlist.m3u
M3U8_RE = re.compile(r"https?://[^\s'\"<>\\]+?\.m3u8[^\s'\"<>\\]*")

HEADERS = {
    "User-Agent": "Mozilla/5.0"
}

class LinkParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []

    def handle_starttag(self, tag, attrs):
        if tag != "a":
            return

        attrs = dict(attrs)
        href = attrs.get("href", "")
        name = attrs.get("title", "")

        if href:
            self.links.append((href, name))


def get_catalog():
    print("Завантажую каталог Viks TV...")

    r = requests.get(
        BASE_URL,
        headers=HEADERS,
        timeout=25
    )
    r.raise_for_status()

    parser = LinkParser()
    parser.feed(r.text)

    channels = {}
    for href, name in parser.links:
        url = urljoin(BASE_URL, href)
        parsed = urlparse(url)

        if parsed.netloc != "live.viks.tv":
            continue

        if url.rstrip("/") == BASE_URL.rstrip("/"):
            continue

        if not url.startswith(BASE_URL):
            continue

        title = name.strip()

        if not title:
            title = parsed.path.strip("/").split("/")[-1]

        if parsed.path and parsed.path != "/":
            channels[url] = title

    return channels


def check_page(item):
    url, name = item

    result = {
        "name": name,
        "page": url,
        "status": "error",
        "hls_player": False,
        "checked": int(time.time())
    }

    try:
        r = requests.get(
            url,
            headers=HEADERS,
            timeout=20
        )

        result["http_status"] = r.status_code

        if r.status_code != 200:
            return result

        html = r.text.lower()

        # Визначаємо наявність ознак HLS-плеєра,
        # не копіюючи адреси чужих відеопотоків.
        patterns = [
            r"application/x-mpegurl",
            r"application/vnd.apple.mpegurl",
            r"hls\.js",
            r"mpegurl",
            r"\.m3u8(?:\?|['\"]|$)"
        ]

        result["hls_player"] = any(
            re.search(pattern, html)
            for pattern in patterns
        )

        result["status"] = "working"

    except Exception as e:
        result["error"] = str(e)

    return result


def find_stream(item):
    name, page = item
    try:
        r = requests.get(page, headers=HEADERS, timeout=20)
        r.raise_for_status()
    except Exception:
        return None
    urls = M3U8_RE.findall(r.text)
    return (name, urls[0]) if urls else None


def write_streams(pages):
    with ThreadPoolExecutor(max_workers=15) as pool:
        found = [x for x in pool.map(find_stream, pages) if x]
    with open(MANUAL_OUT, "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for name, url in found:
            f.write(f'#EXTINF:-1 group-title="Viks",{name}\n{url}\n')
    print("Потоків знайдено:", len(found))


def main():
    channels = get_catalog()

    print("Знайдено сторінок:", len(channels))

    with ThreadPoolExecutor(max_workers=15) as pool:
        results = list(
            pool.map(check_page, channels.items())
        )

    working = sum(
        x["status"] == "working"
        for x in results
    )

    hls = sum(
        x["hls_player"]
        for x in results
    )

    report = {
        "site": BASE_URL,
        "total": len(results),
        "working": working,
        "hls_pages": hls,
        "channels": results
    }

    with open(OUTPUT, "w", encoding="utf-8") as f:
        json.dump(
            report,
            f,
            ensure_ascii=False,
            indent=2
        )

    print("Сторінок:", len(results))
    print("Доступних:", working)
    print("З HLS-плеєром:", hls)
    print("Звіт:", OUTPUT)

    write_streams([(x["name"], x["page"]) for x in results if x["status"] == "working"])


if __name__ == "__main__":
    main()
      
      


