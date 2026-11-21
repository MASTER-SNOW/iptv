
import json
import re
import time
import requests
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse
from concurrent.futures import ThreadPoolExecutor

BASE_URL = "https://live.viks.tv/"
OUTPUT = "viks_report.json"

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


if __name__ == "__main__":
    main()
      
