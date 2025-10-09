import os
import json
import requests
import shutil
import time
from tqdm import tqdm
from urllib.parse import quote
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

RAW_BASE = "https://raw.githubusercontent.com/wldeh/bible-api/refs/heads/main/bibles"
API_BASE = "https://api.github.com/repos/wldeh/bible-api/contents/bibles"
OUTPUT_DIR = "Bible_Texts_RAG"
VERSIONS = ["en-kjv", "en-asv", "en-web", "he-wlc", "grc-srgnt"]

# --- Token setup and check ---
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
if GITHUB_TOKEN:
    print("🔐 Using GitHub API token for fast mode.")
else:
    print("⚠️ No GitHub token detected. Expect slow mode and possible 429s.")
    print("👉 To set one, run: export GITHUB_TOKEN=ghp_yourtoken")

HEADERS = {"Authorization": f"token {GITHUB_TOKEN}"} if GITHUB_TOKEN else {}

# Adaptive delay settings
base_delay = 0.1
max_delay = 5.0
min_delay = 0.05

# -------------------------------
# Helper functions
# -------------------------------
def adaptive_sleep(last_time, ok=True):
    """Adjust delay adaptively depending on rate limit behavior."""
    global base_delay
    if not ok:
        base_delay = min(base_delay * 1.5, max_delay)
        print(f"⏳ Adjusting delay to {base_delay:.2f}s due to throttling...")
    else:
        base_delay = max(base_delay * 0.95, min_delay)
    time.sleep(base_delay)

def api_get(url):
    """GitHub API GET with adaptive throttling."""
    while True:
        r = requests.get(url, headers=HEADERS)
        if r.status_code == 403 or r.status_code == 429:
            adaptive_sleep(time.time(), ok=False)
            continue
        if r.status_code != 200:
            print(f"❌ HTTP {r.status_code}: {url}")
            return None
        adaptive_sleep(time.time())
        return r.json()

def raw_get_json(url):
    """Fetch JSON safely from raw.githubusercontent."""
    for _ in range(3):  # retry a few times
        try:
            r = requests.get(url)
            if r.status_code == 200:
                return json.loads(r.content.decode("utf-8-sig"))
            elif r.status_code == 404:
                return None
        except Exception:
            adaptive_sleep(time.time(), ok=False)
    return None

def clear_output_dir():
    if os.path.exists(OUTPUT_DIR):
        print(f"🧹 Clearing old output: {OUTPUT_DIR}")
        shutil.rmtree(OUTPUT_DIR)
    os.makedirs(OUTPUT_DIR, exist_ok=True)

def fetch_books(version):
    url = f"{API_BASE}/{version}/books"
    data = api_get(url)
    if not data:
        return []
    return [b["name"] for b in data if b["type"] == "dir"]

def fetch_chapters(version, book):
    url = f"{API_BASE}/{version}/books/{quote(book)}/chapters"
    data = api_get(url)
    if not data:
        return []
    return [c["name"] for c in data if c["type"] == "dir"]

def fetch_verses(version, book, chapter):
    url = f"{API_BASE}/{version}/books/{quote(book)}/chapters/{chapter}/verses"
    data = api_get(url)
    if not data:
        return []
    return [v["name"] for v in data if v["name"].endswith(".json")]

# -------------------------------
# Parallel download logic
# -------------------------------
def fetch_chapter(version, book, chapter):
    """Fetch a full chapter in parallel (one thread per chapter)."""
    lines = []
    verses = fetch_verses(version, book, chapter)
    for verse_file in verses:
        verse_url = f"{RAW_BASE}/{version}/books/{quote(book)}/chapters/{chapter}/verses/{verse_file}"
        verse_data = raw_get_json(verse_url)
        if verse_data and "text" in verse_data:
            num = verse_data.get("verse", verse_file.replace(".json", ""))
            text = verse_data["text"].strip()
            lines.append(f"{chapter}:{num} {text}")
    return lines

def process_version(version):
    lang = version.split("-")[0]
    version_dir = os.path.join(OUTPUT_DIR, version)
    os.makedirs(version_dir, exist_ok=True)

    books = fetch_books(version)
    if not books:
        print(f"⚠️ No books found for {version}, skipping.\n")
        return

    print(f"📘 Found {len(books)} books in {version}")

    total_books, total_verses = 0, 0
    for book in books:
        book_path = os.path.join(version_dir, f"{version}_{book}.txt")

        # Resume-safe: skip completed
        if os.path.exists(book_path) and os.path.getsize(book_path) > 1000:
            print(f"↩️ Skipping {book} (already exists)")
            continue

        start_time = time.time()
        book_lines = [f"### Version: {version}", f"### Book: {book}", f"### Language: {lang}", "---"]
        chapters = fetch_chapters(version, book)
        verse_count = 0

        if not chapters:
            print(f"⚠️ No chapters for {book}, skipping.")
            continue

        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = {executor.submit(fetch_chapter, version, book, ch): ch for ch in chapters}
            for future in tqdm(as_completed(futures), total=len(chapters), desc=f"📖 {version}:{book}", unit="ch"):
                chapter_lines = future.result()
                verse_count += len(chapter_lines)
                book_lines.extend(chapter_lines)

        duration = time.time() - start_time
        if verse_count > 0:
            with open(book_path, "w", encoding="utf-8") as f:
                f.write("\n".join(book_lines))
            speed = verse_count / duration if duration > 0 else 0
            print(f"✅ {book}: {verse_count} verses in {duration:.1f}s ({speed:.2f} v/s)")
            total_books += 1
            total_verses += verse_count
        else:
            print(f"⚠️ {book} contained no data.")

    print(f"\n✅ Finished {version}: {total_books} books, {total_verses} verses total.\n")

# -------------------------------
# Entry point
# -------------------------------
def main():
    clear_output_dir()
    print("📘 Starting Parallel Bible Downloader for RAG...\n")
    for version in VERSIONS:
        print(f"\n📖 Processing version: {version}")
        process_version(version)
    print("\n🎉 All selected versions processed successfully.")

if __name__ == "__main__":
    main()
