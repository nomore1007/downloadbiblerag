# 📖 Bible RAG Dataset Builder

A Python utility for downloading public-domain Bible texts (including original Hebrew and Greek) directly from the [`wldeh/bible-api`](https://github.com/wldeh/bible-api) repository, preparing them into clean `.txt` files optimized for **Retrieval-Augmented Generation (RAG)** ingestion.

---

## ✨ Features

- ✅ Downloads **KJV**, **ASV**, **WEB**, **Hebrew (WLC)**, and **Greek (SRGNT)** texts
- ⚡ **Parallelized downloads** — chapters fetched in multiple threads
- 🧠 **Adaptive throttling** to handle GitHub API limits automatically
- 🔁 **Resume support** — skips already completed books
- 📊 **Progress bars** and **speed metrics**
- 🧹 Clears incomplete data from previous runs
- 🔐 Optional **GitHub API token** for high-speed mode
- 🧩 Outputs clean text files, RAG-friendly and easy to search/index

---

## 🧱 Repository Structure

Bible_RAG_Builder/
├── download_bibles_rag_clean.py # main script
├── README.md # documentation
└── Bible_Texts_RAG/ # generated dataset output
├── en-kjv/
│ ├── en-kjv_genesis.txt
│ ├── en-kjv_exodus.txt
│ └── ...
├── he-wlc/
├── grc-srgnt/
└── ...


---

## 🧩 Requirements

Python ≥ 3.9

```bash
pip install requests tqdm

⚙️ Usage

# Optional: set a GitHub token for speed
export GITHUB_TOKEN=ghp_yourgithubapitoken

# Run the downloader
python download_bibles_rag_clean.py
