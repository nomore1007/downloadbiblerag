import requests
import json
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from time import sleep
import argparse
import shutil

# -----------------------------------
# Config
# -----------------------------------
BASE_DIR = Path("Bible_Texts_RAG")
BIBLES_JSON_URL = "https://raw.githubusercontent.com/wldeh/bible-api/refs/heads/main/bibles/bibles.json"
CDN_BASE = "https://cdn.jsdelivr.net/gh/wldeh/bible-api/bibles"

BOOKS = [
    "genesis","exodus","leviticus","numbers","deuteronomy","joshua","judges","ruth",
    "1samuel","2samuel","1kings","2kings","1chronicles","2chronicles","ezra","nehemiah",
    "esther","job","psalms","proverbs","ecclesiastes","songofsolomon","isaiah","jeremiah",
    "lamentations","ezekiel","daniel","hosea","joel","amos","obadiah","jonah","micah",
    "nahum","habakkuk","zephaniah","haggai","zechariah","malachi","matthew","mark","luke",
    "john","acts","romans","1corinthians","2corinthians","galatians","ephesians","philippians",
    "colossians","1thessalonians","2thessalonians","1timothy","2timothy","titus","philemon",
    "hebrews","james","1peter","2peter","1john","2john","3john","jude","revelation"
]

TARGET_IDS = {"en-kjv", "en-asv", "en-web"}  # you can expand later
MAX_WORKERS = 6


# -----------------------------------
# Fetch Bible metadata
# -----------------------------------
def get_available_bibles():
    r = requests.get(BIBLES_JSON_URL)
    r.raise_for_status()
    return json.loads(r.content.decode("utf-8-sig"))

def find_target_versions(bibles, only=None):
    targets = []
    for bible in bibles:
        bid = bible.get("id", "")
        if only and bid not in only:
            continue
        if bid in TARGET_IDS:
            targets.append({
                "id": bid,
                "name": bible["version"],
                "abbr": bible.get("localVersionAbbreviation", bid),
                "lang": bible.get("language", {}).get("code", "unknown")
            })
    return targets


# -----------------------------------
# Fetch one book (multi-chapter)
# -----------------------------------
def fetch_book(version, book_name, output_format="txt", chunk_by="verse"):
    version_dir = BASE_DIR / version["abbr"]
    tmpfile = version_dir / f".tmp_{book_name}.writing"
    has_content = False

    if output_format == "txt":
        filename = version_dir / f"{version['abbr']}_{book_name.capitalize()}.txt"
    else:
        filename = version_dir / f"{version['abbr']}_{book_name.capitalize()}.jsonl"

    with open(tmpfile, "w", encoding="utf-8") as f:
        if output_format == "txt":
            f.write(f"### Version: {version['abbr']}\n")
            f.write(f"### Book: {book_name.capitalize()}\n")
            f.write(f"### Language: {version['lang']}\n---\n")

        chapter = 1
        while True:
            url = f"{CDN_BASE}/{version['id']}/books/{book_name}/chapters/{chapter}.json"
            try:
                r = requests.get(url, timeout=10)
            except Exception:
                break

            if r.status_code != 200:
                break

            try:
                data = r.json()
            except Exception:
                break
            if not data:
                break

            has_content = True
            if output_format == "jsonl":
                if chunk_by == "chapter":
                    chapter_text = " ".join(
                        v["text"] if isinstance(v, dict) else str(v)
                        for v in data
                    )
                    obj = {
                        "version": version["abbr"],
                        "book": book_name.capitalize(),
                        "chapter": chapter,
                        "text": chapter_text
                    }
                    f.write(json.dumps(obj, ensure_ascii=False) + "\n")
                else:
                    for verse_num, verse in enumerate(data, start=1):
                        text = verse.get("text", "") if isinstance(verse, dict) else str(verse)
                        obj = {
                            "version": version["abbr"],
                            "book": book_name.capitalize(),
                            "chapter": chapter,
                            "verse": verse_num,
                            "text": text.strip()
                        }
                        f.write(json.dumps(obj, ensure_ascii=False) + "\n")
            else:
                for verse_num, verse in enumerate(data, start=1):
                    text = verse.get("text", "") if isinstance(verse, dict) else str(verse)
                    f.write(f"{chapter}:{verse_num} {text.strip()}\n")

            chapter += 1

    # Rename file only if content was written
    if has_content:
        tmpfile.rename(filename)
        return f"✅ {version['abbr']}:{book_name}"
    else:
        tmpfile.unlink(missing_ok=True)
        return f"⏩ {version['abbr']}:{book_name} (no data)"


# -----------------------------------
# Main
# -----------------------------------
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--format", choices=["txt", "jsonl"], default="txt", help="Output format")
    parser.add_argument("--chunk-by", choices=["verse", "chapter"], default="verse", help="Chunking method for JSONL")
    parser.add_argument("--only", nargs="*", help="Limit to specific version IDs (e.g. en-kjv en-asv)")
    args = parser.parse_args()

    BASE_DIR.mkdir(exist_ok=True)
    print("Fetching Bible metadata...")
    bibles = get_available_bibles()
    targets = find_target_versions(bibles, args.only)

    print(f"\nFound {len(targets)} target versions:")
    for v in targets:
        print(f" - {v['abbr']} ({v['name']}) [{v['lang']}]")

    for version in targets:
        version_dir = BASE_DIR / version["abbr"]
        if version_dir.exists():
            shutil.rmtree(version_dir)
        version_dir.mkdir(parents=True, exist_ok=True)

        print(f"\n📘 Processing {version['abbr']} ({version['name']})...")
        tasks = []
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            for book in BOOKS:
                tasks.append(executor.submit(fetch_book, version, book, args.format, args.chunk_by))
            for future in as_completed(tasks):
                print(future.result())
        print(f"✅ Completed {version['abbr']}.\n")

    print("📦 All downloads finished. Ready for RAG ingestion!")

if __name__ == "__main__":
    main()
