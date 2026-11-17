import requests
import os
from concurrent.futures import ThreadPoolExecutor

SOURCES = [
    "https://iptv-org.github.io/iptv/countries/ua.m3u",
    "https://iptv-org.github.io/iptv/countries/ru.m3u",
    "https://iptv-org.github.io/iptv/countries/by.m3u",
    "https://iptv-org.github.io/iptv/languages/ukr.m3u",
]

FOREIGN = [
    "https://raw.githubusercontent.com/Free-TV/IPTV/master/playlist.m3u8",
]

def load(url):
    text = requests.get(url, timeout=30).text
    items, info = [], None
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("#EXTINF"):
            info = line
        elif line.startswith("http") and info:
            items.append((info, line))
            info = None
    return items

def tvg_id(info):
    if 'tvg-id="' in info:
        return info.split('tvg-id="')[1].split('"')[0]
    return None

def alive(url):
    try:
        r = requests.get(url, stream=True, timeout=8)
        ok = r.status_code == 200
        r.close()
        return ok
    except Exception:
        return False

def build(sources, out):
    all_items = []
    for s in sources:
        all_items.extend(load(s))

    groups, order = {}, []
    for info, url in all_items:
        key = tvg_id(info) or url
        if key not in groups:
            groups[key] = []
            order.append(key)
        if url not in [u for _, u in groups[key]]:
            groups[key].append((info, url))

    # перевіряємо всі кандидати паралельно
    candidates = [(key, info, url) for key in order for info, url in groups[key]]
    with ThreadPoolExecutor(30) as ex:
        results = list(ex.map(lambda c: alive(c[2]), candidates))

    # беремо перший робочий кандидат для кожного каналу
    ok_by_key = {}
    for (key, info, url), ok in zip(candidates, results):
        if ok and key not in ok_by_key:
            ok_by_key[key] = (info, url)

    with open(out, "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for key in order:
            if key in ok_by_key:
                info, url = ok_by_key[key]
                f.write(info + "\n" + url + "\n")
    return order

build(SOURCES, "playlist.m3u")
build(FOREIGN, "foreign.m3u")

if os.path.exists("manual.m3u"):
    with open("manual.m3u", encoding="utf-8") as m:
        with open("playlist.m3u", "a", encoding="utf-8") as f:
            for line in m:
                if not line.startswith("#EXTM3U"):
                    f.write(line)
