import os
import re
import json
import time
import requests
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import quote

BASE = os.path.dirname(os.path.abspath(__file__))

OUTPUT = os.path.join(BASE, "playlist.m3u")
REGISTRY = os.path.join(BASE, "sources.json")
HISTORY = os.path.join(BASE, "history.json")
MANUAL = os.path.join(BASE, "manual.m3u")

HEADERS = {
    "User-Agent": "Mozilla/5.0 IPTV-Collector/3.0",
    "Accept": "application/vnd.github+json",
}

TOKEN = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
if TOKEN:
    HEADERS["Authorization"] = f"Bearer {TOKEN}"

INITIAL_SOURCES = [
    "https://iptv-org.github.io/iptv/countries/ua.m3u",
    "https://iptv-org.github.io/iptv/countries/ru.m3u",
    "https://iptv-org.github.io/iptv/countries/by.m3u",
    "https://iptv-org.github.io/iptv/languages/ukr.m3u",
    "https://raw.githubusercontent.com/Free-TV/IPTV/master/playlist.m3u8",
]

SEARCH_QUERIES = [
    "iptv",
    "m3u playlist",
    "m3u8 playlist",
    "ukraine iptv",
    "ukrainian tv",
    "ukraine channels",
    "russia iptv",
    "belarus iptv",
    "tv streams",
    "live tv playlist",
    "iptv channels",
    "television playlist",
    "free tv m3u",
    "tv channel list",
    "online television",
    "ua tv m3u",
    "ukr m3u",
    "ru tv m3u",
    "by tv m3u",
    "channels.m3u",
]

MAX_REPOS_PER_QUERY = 50
MAX_FILES_PER_REPO = 20
MAX_WORKERS = 15
MAX_FILE_SIZE = 5 * 1024 * 1024
REQUEST_TIMEOUT = (8, 25)

# Leave empty to keep every channel, including channels from other countries.
# If later you want strict filtering, put channel-name fragments here to remove.
REMOVE = []

ALLOWED_COUNTRIES = {
    "UA", "UKR", "UKRAINE",
    "RU", "RUS", "RUSSIA",
    "BY", "BLR", "BEL", "BELARUS",
}


def read_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError, TypeError):
        return default


def save_json(path, data):
    temp = path + ".tmp"
    with open(temp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(temp, path)


def load_text(url):
    response = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    response.encoding = response.encoding or "utf-8"
    return response.text


def github_get(url, params=None, retries=3):
    for attempt in range(retries):
        try:
            response = requests.get(
                url,
                params=params,
                headers=HEADERS,
                timeout=REQUEST_TIMEOUT,
            )

            if response.status_code in (403, 429):
                remaining = response.headers.get("X-RateLimit-Remaining")
                reset = response.headers.get("X-RateLimit-Reset")

                if remaining == "0" and reset:
                    wait_seconds = max(
                        1, int(reset) - int(time.time()) + 5
                    )
                    if wait_seconds > 300:
                        print(
                            "GitHub API: ліміт вичерпано. "
                            f"Потрібно зачекати приблизно {wait_seconds} с."
                        )
                        return None
                    print(f"GitHub API: пауза {wait_seconds} с.")
                    time.sleep(wait_seconds)
                    continue

                if attempt < retries - 1:
                    time.sleep(10 * (attempt + 1))
                    continue

                print(f"GitHub API повернув {response.status_code}")
                return None

            if response.status_code >= 500:
                if attempt < retries - 1:
                    time.sleep(5 * (attempt + 1))
                    continue
                print(f"GitHub серверна помилка: {response.status_code}")
                return None

            if response.status_code != 200:
                print(f"GitHub API повернув {response.status_code}: {url}")
                return None

            return response

        except requests.RequestException as error:
            print("Помилка GitHub-запиту:", error)
            if attempt < retries - 1:
                time.sleep(3 * (attempt + 1))

    return None


def parse_m3u(text):
    items = []
    current_info = None

    for raw_line in text.splitlines():
        line = raw_line.strip()

        if line.startswith("#EXTINF"):
            current_info = line
        elif line.lower().startswith(("http://", "https://")):
            if current_info:
                items.append((current_info, line))
                current_info = None

    return items


def parse_source_text(text, filename="channel"):
    # M3U/M3U8: keep the channel metadata and its stream URL.
    m3u_items = parse_m3u(text)
    if m3u_items:
        return m3u_items

    # Plain TXT: turn each direct URL into a basic M3U entry.
    items = []
    seen = set()

    for raw_url in re.findall(r'https?://[^\s<>"\']+', text, re.IGNORECASE):
        url = raw_url.rstrip(".,;)]}")
        if url in seen:
            continue
        seen.add(url)

        basename = os.path.basename(url.split("?")[0].split("#")[0])
        channel_name = basename or filename
        items.append((f"#EXTINF:-1,{channel_name}", url))

    return items


def is_target_channel(info):
    # Unknown country is intentionally kept.
    match = re.search(r'tvg-country="([^"]*)"', info, re.IGNORECASE)
    if not match or not match.group(1).strip():
        return True

    countries = re.split(r"[,;|]", match.group(1))
    # Keep UA/RU/BY; unknown or unrecognized metadata is also kept for review.
    # Only explicitly identified other countries are excluded.
    normalized = [country.strip().upper() for country in countries if country.strip()]
    if not normalized:
        return True
    return any(country in ALLOWED_COUNTRIES for country in normalized)


def discover_github():
    found = []
    seen_repositories = set()
    seen_files = set()

    stats = {
        "repositories": 0,
        "files_checked": 0,
        "sources_found": 0,
        "errors": 0,
    }

    print("\n========== GITHUB SEARCH ==========")

    for query_number, query in enumerate(SEARCH_QUERIES, start=1):
        print(f"\n[{query_number}/{len(SEARCH_QUERIES)}] Пошук: {query}")

        response = github_get(
            "https://api.github.com/search/repositories",
            params={
                "q": query,
                "sort": "updated",
                "per_page": 100,
            },
        )

        if response is None:
            stats["errors"] += 1
            continue

        repositories = response.json().get("items", [])
        print("Результатів GitHub:", len(repositories))

        for repository in repositories[:MAX_REPOS_PER_QUERY]:
            full_name = repository.get("full_name")
            if not full_name or full_name in seen_repositories:
                continue

            if repository.get("archived") or repository.get("disabled"):
                continue

            seen_repositories.add(full_name)
            stats["repositories"] += 1

            branch = repository.get("default_branch") or "master"
            tree_url = (
                f"https://api.github.com/repos/{full_name}/git/trees/"
                f"{quote(branch, safe='/')}"
            )

            tree_response = github_get(
                tree_url,
                params={"recursive": "1"},
            )
            if tree_response is None:
                stats["errors"] += 1
                continue

            tree_data = tree_response.json()
            tree_files = tree_data.get("tree", [])

            if tree_data.get("truncated"):
                print("Увага: GitHub обрізав список файлів:", full_name)

            repo_sources = 0

            for file_info in tree_files:
                path = file_info.get("path", "")
                extension = os.path.splitext(path)[1].lower()

                if extension not in (".m3u", ".m3u8", ".txt"):
                    continue
                if file_info.get("type") != "blob":
                    continue
                if file_info.get("size", 0) > MAX_FILE_SIZE:
                    continue

                raw_url = (
                    f"https://raw.githubusercontent.com/{full_name}/"
                    f"{quote(branch, safe='/')}/{quote(path, safe='/')}"
                )

                if raw_url in seen_files:
                    continue
                seen_files.add(raw_url)
                stats["files_checked"] += 1

                try:
                    content = load_text(raw_url)
                    channels = parse_source_text(content, path)
                    useful_channels = [
                        channel for channel in channels
                        if is_target_channel(channel[0])
                    ]

                    if not useful_channels:
                        continue

                    found.append({
                        "url": raw_url,
                        "name": f"{full_name}/{path}",
                    })
                    stats["sources_found"] += 1
                    repo_sources += 1

                    print(
                        f"Знайдено: {full_name}/{path} "
                        f"| каналів у файлі: {len(useful_channels)}"
                    )

                    if repo_sources >= MAX_FILES_PER_REPO:
                        break

                except requests.RequestException as error:
                    stats["errors"] += 1
                    print("Не вдалося завантажити файл:", path, error)

            print(f"Репозиторій {full_name}: джерел {repo_sources}")

            # Small pause to avoid hammering raw.githubusercontent.com.
            time.sleep(0.15)

    print("\n========== ПОШУК ЗАВЕРШЕНО ==========")
    print("Репозиторіїв:", stats["repositories"])
    print("Файлів перевірено:", stats["files_checked"])
    print("Джерел знайдено:", stats["sources_found"])
    print("Помилок:", stats["errors"])

    return found, stats


def update_registry(new_sources):
    old_sources = read_json(REGISTRY, [])
    if not isinstance(old_sources, list):
        old_sources = []

    known_urls = {
        item.get("url")
        for item in old_sources
        if isinstance(item, dict) and item.get("url")
    }

    added = 0
    for item in new_sources:
        url = item.get("url")
        if url and url not in known_urls:
            old_sources.append(item)
            known_urls.add(url)
            added += 1

    save_json(REGISTRY, old_sources)
    print("Нових джерел додано:", added)
    print("Усього джерел у реєстрі:", len(old_sources))
    return old_sources


def get_sources():
    sources = [
        {"url": url, "name": "Основне джерело"}
        for url in INITIAL_SOURCES
    ]

    registry = read_json(REGISTRY, [])
    if isinstance(registry, list):
        for item in registry:
            if (
                isinstance(item, dict)
                and isinstance(item.get("url"), str)
                and item["url"].startswith(("http://", "https://"))
            ):
                sources.append(item)

    result = []
    seen = set()

    for source in sources:
        url = source.get("url")
        if url and url not in seen:
            seen.add(url)
            result.append(source)

    return result


def load_source(source):
    try:
        text = load_text(source["url"])
        return parse_source_text(text, source.get("name", "source"))
    except requests.RequestException as error:
        print("Не вдалося прочитати:", source.get("name"), error)
        return []


def alive(item):
    info, url, source = item

    try:
        with requests.get(
            url,
            headers=HEADERS,
            stream=True,
            timeout=(6, 12),
            allow_redirects=True,
        ) as response:
            if not 200 <= response.status_code < 300:
                return item, False

            content_type = response.headers.get("Content-Type", "").lower()
            if "text/html" in content_type:
                return item, False

            first_chunk = next(response.iter_content(chunk_size=512), b"")
            return item, bool(first_chunk)

    except requests.RequestException:
        return item, False


def build():
    started = time.time()
    print("\n========== IPTV COLLECTOR ==========")

    new_sources, search_stats = discover_github()
    update_registry(new_sources)

    sources = get_sources()
    print("\nУсього джерел для читання:", len(sources))

    candidates = []
    seen_urls = set()
    source_errors = 0

    for number, source in enumerate(sources, start=1):
        print(f"\nЧитаю джерело {number}/{len(sources)}: {source['name']}")

        entries = load_source(source)
        if not entries:
            source_errors += 1
            continue

        for info, url in entries:
            if not is_target_channel(info):
                continue

            # Exact duplicate URLs are removed; different URLs for the
            # same channel name are preserved as alternative streams.
            if url in seen_urls:
                continue

            seen_urls.add(url)
            candidates.append((info, url, source["name"]))

    # Manual playlist is always included and is not country-filtered.
    if os.path.exists(MANUAL):
        try:
            with open(MANUAL, "r", encoding="utf-8") as manual_file:
                manual_entries = parse_source_text(manual_file.read(), "manual")

            for info, url in manual_entries:
                if url not in seen_urls:
                    seen_urls.add(url)
                    candidates.append((info, url, "manual.m3u"))

            print("Ручних каналів прочитано:", len(manual_entries))
        except OSError as error:
            print("Помилка читання manual.m3u:", error)

    print("\nУсього унікальних посилань для перевірки:", len(candidates))

    history = read_json(HISTORY, {})
    if not isinstance(history, dict):
        history = {}

    checked_at = datetime.now(timezone.utc).isoformat()
    working = []
    dead_count = 0

    print("\n========== ПЕРЕВІРКА ПОТОКІВ ==========")

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = [executor.submit(alive, item) for item in candidates]

        completed = 0
        total = len(futures)

        for future in as_completed(futures):
            completed += 1
            try:
                item, is_alive = future.result()
            except Exception:
                continue

            info, url, source_name = item

            history[url] = {
                "source": source_name,
                "last_check": checked_at,
                "status": "working" if is_alive else "dead",
            }

            if is_alive:
                working.append(item)
            else:
                dead_count += 1

            if completed % 25 == 0 or completed == total:
                print(f"Перевірено: {completed}/{total}")

    # Apply optional name-based removals only after stream checks.
    if REMOVE:
        working = [
            item for item in working
            if not any(term.lower() in item[0].lower() for term in REMOVE)
        ]

    # Stable output order by source collection order.
    working_urls = {item[1] for item in working}
    ordered_working = [
        item for item in candidates
        if item[1] in working_urls
    ]

    with open(OUTPUT, "w", encoding="utf-8") as output_file:
        output_file.write("#EXTM3U\n")
        for info, url, source_name in ordered_working:
            output_file.write(info + "\n")
            output_file.write(url + "\n")

    save_json(HISTORY, history)

    elapsed = round(time.time() - started, 1)
    report = [
        "### Звіт оновлення IPTV",
        f"- Пошукових запитів: {len(SEARCH_QUERIES)}",
        f"- Репозиторіїв знайдено/переглянуто: {search_stats['repositories']}",
        f"- Файлів M3U/M3U8/TXT перевірено: {search_stats['files_checked']}",
        f"- Нових джерел знайдено: {search_stats['sources_found']}",
        f"- Усього джерел прочитано: {len(sources)}",
        f"- Посилань зібрано: {len(candidates)}",
        f"- Робочих посилань: {len(ordered_working)}",
        f"- Неробочих посилань: {dead_count}",
        f"- Джерел, які не вдалося прочитати: {source_errors}",
        f"- Помилок пошуку GitHub: {search_stats['errors']}",
        f"- Час виконання: {elapsed} с",
    ]

    report_text = "\n".join(report) + "\n"
    print("\n" + report_text)

    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as summary_file:
            summary_file.write(report_text)

    print("Готовий плейлист:", OUTPUT)


if __name__ == "__main__":
    build()
          
