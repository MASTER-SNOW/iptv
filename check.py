import requests 
import os
from concurrent.futures import ThreadPoolExecutor

SOURCES = [
    "https://iptv-org.github.io/iptv/countries/ua.m3u",
    "https://iptv-org.github.io/iptv/countries/ru.m3u",
    "https://iptv-org.github.io/iptv/countries/by.m3u",
    playlist_ukraine.m3u8 і dearbulut ua.m3u.
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

def alive(item):
    try:
        r = requests.get(item[1], stream=True, timeout=8)
        ok = r.status_code == 200
        r.close()
        return ok
    except Exception:
        return False

items, seen = [], set()
for s in SOURCES:
    for info, url in load(s):
        if url not in seen:
            seen.add(url)
            items.append((info, url))

with ThreadPoolExecutor(30) as ex:
    results = list(ex.map(alive, items))

with open("playlist.m3u", "w", encoding="utf-8") as f:
    f.write("#EXTM3U\n")
    for (info, url), ok in zip(items, results):
        if ok:
            f.write(info + "\n" + url + "\n")
if os.path.exists("manual.m3u"):
    with open("manual.m3u", encoding="utf-8") as m:
        with open("playlist.m3u", "a", encoding="utf-8") as f:
            for line in m:
                if not line.startswith("#EXTM3U"):
                    f.write(line)
ADULT = ["https://iptv-org.github.io/iptv/categories/xxx.m3u"]
with open("playlist.m3u", "a", encoding="utf-8") as f:
    for s in ADULT:
        for info, url in load(s):
            if url not in seen:
                seen.add(url)
                f.write(info + "\n" + url + "\n")
import requests
import os
from concurrent.futures import ThreadPoolExecutor

SOURCES = [
    "https://iptv-org.github.io/iptv/countries/ua.m3u",
    "https://iptv-org.github.io/iptv/countries/ru.m3u",
    "https://iptv-org.github.io/iptv/countries/by.m3u",
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

def alive(item):
    try:
        r = requests.get(item[1], stream=True, timeout=8)
        ok = r.status_code == 200
        r.close()
        return ok
    except Exception:
        return False

def build(sources, out):
    items, seen = [], set()
    for s in sources:
        for info, url in load(s):
            if url not in seen:
                seen.add(url)
                items.append((info, url))
    with ThreadPoolExecutor(30) as ex:
        results = list(ex.map(alive, items))
    with open(out, "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for (info, url), ok in zip(items, results):
            if ok:
                f.write(info + "\n" + url + "\n")
    return items

build(SOURCES, "playlist.m3u")
build(FOREIGN, "foreign.m3u")

if os.path.exists("manual.m3u"):
    with open("manual.m3u", encoding="utf-8") as m:
        with open("playlist.m3u", "a", encoding="utf-8") as f:
            for line in m:
                if not line.startswith("#EXTM3U"):
                    f.write(line)
