#!/usr/bin/env python3
"""
download_bibles_rag_clean_auto.py

- Dynamically reads the wldeh/bible-api bibles.json manifest.
- Downloads target versions (default: en-kjv, en-asv, en-web, he-wlc, grc-srgnt).
- Auto-detects verse-text field per-version/book (handles 'text','data', nested objects, lists, dict-of-verses, plain strings).
- Writes only non-empty files. Uses a .tmp file while writing then renames on success.
- Cleans existing version directories before starting.
- Produces SUMMARY.json with list of downloaded books per version.

Usage:
    python download_bibles_rag_clean_auto.py
    python download_bibles_rag_clean_auto.py --only en-kjv en-asv
    python download_bibles_rag_clean_auto.py --targets en-kjv,en-asv --workers 8
"""
import argparse
import json
import shutil
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from time import sleep
from typing import Any, Callable, Optional

import requests

# ----------------------------
# Config / Canonical books
# ----------------------------
BASE_DIR = Path("Bible_Texts_RAG")
BIBLES_JSON_URL = (
    "https://raw.githubusercontent.com/wldeh/bible-api/refs/heads/main/bibles/bibles.json"
)
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

# Default targets (can be overridden with --only)
DEFAULT_TARGET_IDS = {"en-kjv", "en-asv", "en-web", "he-wlc", "grc-srgnt"}
DEFAULT_MAX_WORKERS = 6

# ----------------------------
# Utility: robust text finder
# ----------------------------
def find_string_in(obj: Any) -> Optional[str]:
    """Recursively find the first non-empty string inside obj (dict/list/str)."""
    if obj is None:
        return None
    if isinstance(obj, str):
        s = obj.strip()
        return s if s else None
    if isinstance(obj, (list, tuple)):
        for item in obj:
            s = find_string_in(item)
            if s:
                return s
        return None
    if isinstance(obj, dict):
        # prefer common keys order
        preferred = ("text", "data", "content", "verseText", "verse_text", "t", "v")
        for k in preferred:
            if k in obj:
                s = find_string_in(obj[k])
                if s:
                    return s
        # otherwise scan values
        for v in obj.values():
            s = find_string_in(v)
            if s:
                return s
    return None


def build_extractor_from_sample(sample_item: Any) -> Callable[[Any], str]:
    """
    Create an extractor function for verse items based on sample_item.
    The extractor returns an empty string if no text is found.
    """
    # If sample already a string -> direct
    if isinstance(sample_item, str):
        return lambda v: v.strip() if isinstance(v, str) else (find_string_in(v) or "")

    # If sample is dict, find best key/value
    if isinstance(sample_item, dict):
        # Try preferred keys first
        preferred_keys = ["text", "data", "content", "verseText", "verse_text", "t", "body"]
        for key in preferred_keys:
            val = sample_item.get(key)
            if isinstance(val, str) and val.strip():
                return lambda v, k=key: (v.get(k, "").strip() if isinstance(v, dict) else (find_string_in(v) or ""))
            if isinstance(val, (dict, list)):
                # if nested contains a string, use recursive finder
                nested = find_string_in(val)
                if nested:
                    return lambda v: (find_string_in(v) or "")

        # Otherwise, pick the first string-valued key
        for k, val in sample_item.items():
            if isinstance(val, str) and val.strip():
                return lambda v, k=k: (v.get(k, "").strip() if isinstance(v, dict) else (find_string_in(v) or ""))

        # fallback to recursive search
        return lambda v: (find_string_in(v) or "")

    # if list/other -> use recursive finder
    return lambda v: (find_string_in(v) or "")


# ----------------------------
# HTTP helpers
# ----------------------------
def get_bibles_manifest() -> list:
    r = requests.get(BIBLES_JSON_URL, timeout=15)
    r.raise_for_status()
    # handle BOM
    return json.loads(r.content.decode("utf-8-sig"))


def safe_get_json(url: str):
    try:
        r = requests.get(url, timeout=12)
        if r.status_code != 200:
            return None
        # some jsons might have BOM but requests.json handles most; be defensive
        return r.json()
    except Exception:
        # return None on any error
        return None


# ----------------------------
# Core: fetch & write
# ----------------------------
def fetch_book_for_version(version: dict, book: str, output_dir: Path) -> Optional[str]:
    """
    Attempt to download book for version. Writes to temporary file and renames only if content found.
    Returns status string.
    """
    tmp = output_dir / f".tmp_{book}.writing"
    final = output_dir / f"{version['abbr']}_{book.capitalize()}.txt"
    has_content = False
    extractor = None  # will be set after first successful chapter

    # open temporary file for streaming
    with open(tmp, "w", encoding="utf-8") as fh:
        # write header afterwards (we'll write header even in tmp—it's okay; we'll only keep file if has_content)
        fh.write(f"### Version: {version['abbr']}\n")
        fh.write(f"### Book: {book.capitalize()}\n")
        fh.write(f"### Language: {version.get('lang','unknown')}\n")
        fh.write("---\n")

        chapter = 1
        while True:
            url = f"{CDN_BASE}/{version['id']}/books/{book}/chapters/{chapter}.json"
            data = safe_get_json(url)
            if not data:
                # chapter missing or no content => stop
                break

            # If chapter is a dict-of-verses (verseNum->text/dict)
            if isinstance(data, dict):
                # sort keys numerically if possible
                try:
                    items = sorted(data.items(), key=lambda kv: int(kv[0]))
                except Exception:
                    items = list(data.items())
                # convert to list of verse values for extractor detection
                verse_values = [v for _, v in items]
                # build extractor from first verse if not yet built
                if extractor is None:
                    extractor = build_extractor_from_sample(verse_values[0] if verse_values else "")
                for i, v in enumerate(verse_values, start=1):
                    text = extractor(v).strip() if extractor else (find_string_in(v) or "")
                    if text:
                        has_content = True
                        fh.write(f"{chapter}:{i} {text}\n")
            # If chapter is a list of verse items (strings or dicts)
            elif isinstance(data, (list, tuple)):
                # build extractor from first verse if not yet built
                if not data:
                    break
                if extractor is None:
                    extractor = build_extractor_from_sample(data[0])
                for i, verse_item in enumerate(data, start=1):
                    text = extractor(verse_item).strip() if extractor else (find_string_in(verse_item) or "")
                    if text:
                        has_content = True
                        fh.write(f"{chapter}:{i} {text}\n")
            else:
                # Unexpected format — attempt to extract any string
                text = find_string_in(data)
                if text:
                    has_content = True
                    fh.write(f"{chapter}:1 {text}\n")
                else:
                    break

            chapter += 1
            # brief polite pause to avoid hammering (adjust as needed)
            sleep(0.05)

    # If no content written, remove tmp and return no-data status
    if not has_content:
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass
        return f"⏩ {version['abbr']}:{book} (no data)"
    # rename tmp to final file
    try:
        tmp.rename(final)
    except Exception:
        # fallback: copy then remove
        shutil.copy(tmp, final)
        tmp.unlink(missing_ok=True)
    return f"✅ {version['abbr']}:{book}"


# ----------------------------
# Orchestration
# ----------------------------
def build_targets_from_manifest(manifest: list, target_ids: set, only: Optional[set]) -> list:
    """Return versions dicts with id,name,abbr,lang filtered by target ids or --only set."""
    targets = []
    for entry in manifest:
        bid = entry.get("id")
        if only and bid not in only:
            continue
        if bid in target_ids:
            targets.append(
                {
                    "id": bid,
                    "name": entry.get("version"),
                    "abbr": entry.get("localVersionAbbreviation") or bid,
                    "lang": entry.get("language", {}).get("code", "unknown"),
                }
            )
    return targets


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--only",
        nargs="*",
        help="Limit to specific version IDs (e.g. en-kjv en-asv).",
    )
    parser.add_argument("--targets", help="Comma-separated target ids override (e.g. en-kjv,en-asv)")
    parser.add_argument("--workers", type=int, default=DEFAULT_MAX_WORKERS, help="Thread pool size")
    args = parser.parse_args()

    # determine which target set to use
    target_ids = DEFAULT_TARGET_IDS.copy()
    if args.targets:
        target_ids = set(x.strip() for x in args.targets.split(",") if x.strip())

    only_set = set(args.only) if args.only else None

    print("Fetching bibles manifest...")
    manifest = get_manifest = None
    try:
        manifest = get_bibles_manifest()
    except Exception as exc:
        print("Failed to fetch manifest:", exc)
        return

    targets = build_targets_from_manifest(manifest, target_ids, only_set)
    if not targets:
        print("No matching target versions found in manifest. Check --only or --targets values.")
        return

    print(f"Will attempt to download {len(targets)} versions:")
    for t in targets:
        print(f" - {t['id']} ({t['name']}) [{t['lang']}]")

    # summary storage
    summary = {}

    # For each version: wipe directory, then download books concurrently
    for version in targets:
        version_dir = BASE_DIR / version["abbr"]
        if version_dir.exists():
            print(f"Cleaning existing directory: {version_dir}")
            shutil.rmtree(version_dir)
        version_dir.mkdir(parents=True, exist_ok=True)

        print(f"\nProcessing version {version['id']} -> folder {version['abbr']} ...")
        saved_books = []
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            futures = {ex.submit(fetch_book_for_version, version, book, version_dir): book for book in BOOKS}
            for fut in as_completed(futures):
                result = fut.result()
                print(result)
                # result format "✅ abbr:book" or "⏩ abbr:book (no data)"
                if result.startswith("✅"):
                    # parse book
                    _, rest = result.split(" ", 1)
                    abbr_book = rest.strip()
                    # abbr_book like "abbr:book"
                    try:
                        _, bookname = abbr_book.split(":", 1)
                        saved_books.append(bookname)
                    except Exception:
                        pass

        summary[version["abbr"]] = saved_books
        print(f"Completed version {version['abbr']}. Saved {len(saved_books)} books.")

    # write summary json
    summary_path = BASE_DIR / "SUMMARY.json"
    with open(summary_path, "w", encoding="utf-8") as sjson:
        json.dump(summary, sjson, ensure_ascii=False, indent=2)

    print(f"\nAll done. Summary saved to {summary_path}")
    print("Files organized under:", BASE_DIR.resolve())


if __name__ == "__main__":
    main()
