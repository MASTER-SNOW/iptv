
import os
import json
import time
import requests
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote

# =====================================
# НАЛАШТУВАННЯ
# =====================================

BASE = os.path.dirname(os.path.abspath(__file__))

OUTPUT = os.path.join(BASE, "playlist.m3u")
REGISTRY = os.path.join(BASE, "sources.json")
HISTORY = os.path.join(BASE, "history.json")
MANUAL = os.path.join(BASE, "manual.m3u")

HEADERS = {
    "User-Agent": "Mozilla/5.0 IPTV-Collector/2.0"
}

# Якщо є GitHub token, можна вставити його сюди.
# Не публікуй цей токен і не передавай його іншим.
GITHUB_TOKEN = ""

if GITHUB_TOKEN:
    HEADERS["Authorization"] = "Bearer " + GITHUB_TOKEN

# Початкові джерела
INITIAL_SOURCES = [
    "https://iptv-org.github.io/iptv/countries/ua.m3u",
    "https://iptv-org.github.io/iptv/countries/ru.m3u",
    "https://iptv-org.github.io/iptv/countries/by.m3u",
    "https://iptv-org.github.io/iptv/languages/ukr.m3u",
    "https://iptv-org.github.io/iptv/languages/rus.m3u",
    "https://iptv-org.github.io/iptv/languages/bel.m3u",
    "https://raw.githubusercontent.com/Free-TV/IPTV/master/playlist.m3u8"
]

# Запити для пошуку нових GitHub-репозиторіїв
SEARCH_QUERIES = [
    "iptv m3u",
    "iptv playlist",
    "m3u8 tv channels",
    "iptv Ukraine",
    "iptv Russia",
    "iptv Belarus"
]

MAX_REPOS_PER_QUERY = 8
MAX_FILES_PER_REPO = 5
MAX_WORKERS = 20


# =====================================
# РОБОТА З ФАЙЛАМИ
# =====================================

def read_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def get_sources():
    saved = read_json(REGISTRY, [])
    sources = []

    for url in INITIAL_SOURCES:
        sources.append({
            "url": url,
            "name": "Основне джерело"
        })

    for item in saved:
        if isinstance(item, dict):
            sources.append(item)

    result = []
    seen = set()

    for item in sources:
        url = item.get("url", "")
        if url.startswith("http") and url not in seen:
            seen.add(url)
            result.append(item)

    return result


# =====================================
# ПОШУК НОВИХ ДЖЕРЕЛ GITHUB
# =====================================

def discover_github():
    print("\nШукаю нові GitHub-репозиторії...")

    found = []

    for query in SEARCH_QUERIES:
        print("Пошук:", query)

        try:
            response = requests.get(
                "https://api.github.com/search/repositories",
                params={
                    "q": query,
                    "sort": "updated",
                    "order": "desc",
                    "per_page": 20
                },
                headers=HEADERS,
                timeout=20
            )

            if response.status_code != 200:
                print("GitHub API:", response.status_code)
                continue

            repos = response.json().get("items", [])

            for repo in repos[:MAX_REPOS_PER_QUERY]:

                if repo.get("archived"):
                    continue

                name = repo["full_name"]
                branch = repo.get("default_branch", "master")

                try:
                    tree_url = (
                        f"https://api.github.com/repos/"
                        f"{name}/git/trees/{quote(branch, safe='/')}"
                    )

                    tree_response = requests.get(
                        tree_url,
                        params={"recursive": "1"},
                        headers=HEADERS,
                        timeout=25
                    )

                    if tree_response.status_code != 200:
                        continue

                    files = tree_response.json().get("tree", [])

                    count = 0

                    for file in files:

                        path = file.get("path", "")
                        low = path.lower()

                        if not low.endswith((".m3u", ".m3u8")):
                            continue

                        if file.get("size", 0) > 5_000_000:
                            continue

                        if file.get("size", 0) < 10:
                            continue

                        raw_url = (
                            f"https://raw.githubusercontent.com/"
                            f"{name}/{quote(branch, safe='/')}/"
                            f"{quote(path, safe='/')}"
                        )

                        found.append({
                            "url": raw_url,
                            "name": name + "/" + path
                        })

                        count += 1

                        if count >= MAX_FILES_PER_REPO:
                            break

                except Exception as e:
                    print("Помилка репозиторію:", e)

        except Exception as e:
            print("Помилка GitHub:", e)

    print("Нових джерел знайдено:", len(found))

    return found


# =====================================
# ЗБЕРЕЖЕННЯ НОВИХ ДЖЕРЕЛ
# =====================================

def update_registry(new_sources):

    old = read_json(REGISTRY, [])
    known = {
        x.get("url")
        for x in old
        if isinstance(x, dict)
    }

    added = 0

    for item in new_sources:
        if item["url"] not in known:
            old.append(item)
            known.add(item["url"])
            added += 1

    save_json(REGISTRY, old)

    print("Додано нових джерел:", added)

    return get_sources()


# =====================================
# ЗАВАНТАЖЕННЯ ВМІСТУ ПЛЕЙЛИСТІВ
# =====================================

def parse_m3u(text):

    items = []
    info = None

    for line in text.splitlines():

        line = line.strip()

        if line.startswith("#EXTINF"):
            info = line

        elif line.startswith("http") and info:
            items.append((info, line))
            info = None

    return items


def load_source(source):

    url = source["url"]

    try:
        response = requests.get(
            url,
            headers=HEADERS,
            timeout=30
        )

        response.raise_for_status()

        return parse_m3u(response.text)

    except Exception as e:
        print("Не вдалося прочитати:", source["name"])
        return []


# =====================================
# ПЕРЕВІРКА ПРЯМИХ ПОСИЛАНЬ
# =====================================

def alive(item):

    info, url, source = item

    try:
        with requests.get(
            url,
            headers=HEADERS,
            stream=True,
            timeout=(6, 10),
            allow_redirects=True
        ) as r:

            if not 200 <= r.status_code < 300:
                return item, False

            content_type = r.headers.get(
                "Content-Type", ""
            ).lower()

            if "text/html" in content_type:
                return item, False

            # Перевіряємо, чи сервер справді віддає дані
            chunk = next(
                r.iter_content(chunk_size=512),
                b""
            )

            if not chunk:
                return item, False

            return item, True

    except Exception:
        return item, False


# =====================================
# ПОЗНАЧЕННЯ ДЖЕРЕЛА
# =====================================

def add_source(info, source):

    comma = info.find(",")

    if comma == -1:
        return info

    source = source.replace('"', "'")

    return (
        info[:comma]
        + ' source="' + source + '"'
        + info[comma:]
    )


# =====================================
# ГОЛОВНА ФУНКЦІЯ
# =====================================

def build():

    start = time.time()

    print("\n=== IPTV COLLECTOR ===")

    # 1. Пошук нових джерел
    new = discover_github()

    # 2. Зберігаємо знайдені джерела
    sources = update_registry(new)

    print("\nУсього джерел:", len(sources))

    # 3. Збираємо прямі URL
    candidates = []
    seen_urls = set()

    for source in sources:

        print("Читаю:", source["name"])

        items = load_source(source)

        for info, url in items:

    # Залишаємо тільки Україну, Росію та Білорусь
    if not is_target_channel(info, source):
        continue

    # Видаляємо тільки однакові URL
    for info, url in manual_items:

    if not is_target_channel(info, {"url": ""}):
        continue

    if url not in seen_urls:

            seen_urls.add(url)

            candidates.append((
                info,
                url,
                source["name"]
            ))

    # 4. Додаємо власні ручні посилання
    if os.path.exists(MANUAL):

        manual_items = load_source({
            "url": "file://" + MANUAL,
            "name": "manual.m3u"
        }) if False else []

        try:
            with open(MANUAL, encoding="utf-8") as f:
                manual_items = parse_m3u(f.read())
        except Exception:
            manual_items = []

        for info, url in manual_items:

            if url not in seen_urls:
                seen_urls.add(url)

                candidates.append((
                    info,
                    url,
                    "manual.m3u"
                ))

    print("\nЗібрано різних посилань:", len(candidates))

    # 5. Перевірка всіх посилань
    print("\nПеревіряю потоки...")

    with ThreadPoolExecutor(MAX_WORKERS) as executor:
        results = list(
            executor.map(alive, candidates)
        )

    working = []
    dead = []

    history = read_json(HISTORY, {})

    now = datetime.now(timezone.utc).isoformat()

    for item, ok in results:

        info, url, source = item

        history[url] = {
            "source": source,
            "last_check": now,
            "status": "working" if ok else "dead"
        }

        if ok:
            working.append(item)
        else:
            dead.append(url)

    # 6. Створюємо один загальний плейлист
    with open(OUTPUT, "w", encoding="utf-8") as f:

        f.write("#EXTM3U\n")

        for info, url, source in working:

            info = add_source(info, source)

            f.write(info + "\n")
            f.write(url + "\n")

    # 7. Зберігаємо історію
    save_json(HISTORY, history)

    elapsed = round(time.time() - start, 1)

    print("\n======================")
    print("ПОШУК ЗАВЕРШЕНО")
    print("======================")
    print("Джерел:", len(sources))
    print("Знайдено URL:", len(candidates))
    print("Робочих:", len(working))
    print("Неробочих:", len(dead))
    print("Час:", elapsed, "секунд")
    print("Плейлист:", OUTPUT)
    print("======================")


# =====================================
# ЗАПУСК ТІЛЬКИ ВРУЧНУ
# =====================================

if __name__ == "__main__":
    print("Починаю пошук IPTV...")
    build()

