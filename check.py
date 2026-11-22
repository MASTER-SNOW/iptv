import os
import re
import json
import time
import requests
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote

BASE = os.path.dirname(os.path.abspath(__file__))

OUTPUT = os.path.join(BASE, "playlist.m3u")
REGISTRY = os.path.join(BASE, "sources.json")
HISTORY = os.path.join(BASE, "history.json")
MANUAL = os.path.join(BASE, "manual.m3u")

HEADERS = {"User-Agent": "Mozilla/5.0 IPTV-Collector/2.0"}

INITIAL_SOURCES = [
    "https://iptv-org.github.io/iptv/countries/ua.m3u",
    "https://iptv-org.github.io/iptv/countries/ru.m3u",
    "https://iptv-org.github.io/iptv/countries/by.m3u",
    "https://iptv-org.github.io/iptv/languages/ukr.m3u",
    "https://raw.githubusercontent.com/Free-TV/IPTV/master/playlist.m3u8",
]

SEARCH_QUERIES = [
    "iptv ukraine m3u",
    "iptv belarus m3u",
    "iptv russia m3u",
]

KEYWORDS = ("ua", "ukr", "ukraine","russia","Yosso" "ru", "rus", "by", "bel", "belarus")

MAX_REPOS_PER_QUERY = 10
MAX_FILES_PER_REPO = 5
MAX_WORKERS = 30

REMOVE = []


def read_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_text(url):
    return requests.get(url, headers=HEADERS, timeout=30).text


def parse_m3u(text):
    items, info = [], None
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("#EXTINF"):
            info = line
        elif line.startswith("http") and info:
            items.append((info, line))
            info = None
    return items


def is_target_channel(info):
    allowed = {"UA", "UKR", "UKRAINE", "RU", "RUS", "RUSSIA", "BY", "BLR", "BEL", "BELARUS","Erotic","XXX","18+ ","Adult",}
    match = re.search(r'tvg-country="([^"]+)"', info, re.IGNORECASE)
    if match:
        countries = re.split(r"[,;|]", match.group(1))
        return any(c.strip().upper() in allowed for c in countries)
    return True


def discover_github():
    found = []
    print("Шукаю нові GitHub-репозиторії...")
    for query in SEARCH_QUERIES:
        print("Пошук:", query)
        try:
            r = requests.get(
                "https://api.github.com/search/repositories",
                params={"q": query, "sort": "updated", "per_page": 20},
                headers=HEADERS,
                timeout=20,
            )
            if r.status_code != 200:
                print("GitHub API:", r.status_code)
                continue
            for repo in r.json().get("items", [])[:MAX_REPOS_PER_QUERY]:
                if repo.get("archived"):
                    continue
                name = repo["full_name"]
                branch = repo.get("default_branch", "master")
                try:
                    tr = requests.get(
                        f"https://api.github.com/repos/{name}/git/trees/{quote(branch, safe='/')}",
                        params={"recursive": "1"},
                        headers=HEADERS,
                        timeout=25,
                    )
                    if tr.status_code != 200:
                        continue
                    count = 0
                    for f in tr.json().get("tree", []):
                        path = f.get("path", "")
                        low = path.lower()
                        if not low.endswith((".m3u", ".m3u8")):
                            continue
                        if not any(k in low for k in KEYWORDS):
                            continue
                        found.append({
                            "url": f"https://raw.githubusercontent.com/{name}/{quote(branch, safe='/')}/{quote(path, safe='/')}",
                            "name": name + "/" + path,
                        })
                        count += 1
                        if count >= MAX_FILES_PER_REPO:
                            break
                except Exception:
                    continue
        except Exception as e:
            print("Помилка GitHub:", e)
    print("Нових джерел знайдено:", len(found))
    return found


def update_registry(new_sources):
    old = read_json(REGISTRY, [])
    known = {x.get("url") for x in old if isinstance(x, dict)}
    added = 0
    for item in new_sources:
        if item["url"] not in known:
            old.append(item)
            known.add(item["url"])
            added += 1
    save_json(REGISTRY, old)
    print("Додано нових джерел:", added)
    return old


def get_sources():
    sources = [{"url": u, "name": "Основне джерело"} for u in INITIAL_SOURCES]
    for item in read_json(REGISTRY, []):
        if isinstance(item, dict) and item.get("url", "").startswith("http"):
            sources.append(item)
    seen, result = set(), []
    for s in sources:
        if s["url"] not in seen:
            seen.add(s["url"])
            result.append(s)
    return result


def load_source(source):
    try:
        return parse_m3u(load_text(source["url"]))
    except Exception as e:
        print("Не вдалося прочитати:", source["name"], e)
        return []


def alive(item):
    info, url, source = item
    try:
        with requests.get(url, headers=HEADERS, stream=True, timeout=(6, 10)) as r:
            if not 200 <= r.status_code < 300:
                return item, False
            if "text/html" in r.headers.get("Content-Type", "").lower():
                return item, False
            chunk = next(r.iter_content(chunk_size=512), b"")
            return item, bool(chunk)
    except Exception:
        return item, False


def build():
    start = time.time()

    new = discover_github()
    update_registry(new)
    sources = get_sources()
    print("Усього джерел:", len(sources))

    candidates, seen_urls = [], set()

    for source in sources:
        print("Читаю:", source["name"])
        for info, url in load_source(source):
            if not is_target_channel(info):
                continue
            if url in seen_urls:
                continue
            seen_urls.add(url)
            candidates.append((info, url, source["name"]))

    if os.path.exists(MANUAL):
        with open(MANUAL, encoding="utf-8") as f:
            for info, url in parse_m3u(f.read()):
                if url not in seen_urls:
                    seen_urls.add(url)
                    candidates.append((info, url, "manual.m3u"))

    print("Зібрано посилань:", len(candidates))

    with ThreadPoolExecutor(MAX_WORKERS) as ex:
        results = list(ex.map(alive, candidates))

    history = read_json(HISTORY, {})
    now = datetime.now(timezone.utc).isoformat()
    working = []
    for item, ok in results:
        info, url, source = item
        history[url] = {"source": source, "last_check": now, "status": "working" if ok else "dead"}
        if ok:
            working.append(item)

    working = [item for item in working if not any(x.lower() in item[0].lower() for x in REMOVE)]

    with open(OUTPUT, "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for info, url, source in working:
            f.write(info + "\n" + url + "\n")

    save_json(HISTORY, history)

    elapsed = round(time.time() - start, 1)
    lines = [
        "### Звіт оновлення",
        f"- Джерел: {len(sources)}",
        f"- Знайдено посилань: {len(candidates)}",
        f"- Робочих: {len(working)}",
        f"- Час: {elapsed} с",
    ]
    text = "\n".join(lines) + "\n"
    print(text)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write(text)


if __name__ == "__main__":
    build()
