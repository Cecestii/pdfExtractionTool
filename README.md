<p align="center">
  <b>English</b> | <a href="README.it.md">Italiano</a>
</p>

# PDF to Markdown Converter (VLM Pipeline)

A high-fidelity Python pipeline designed to convert complex PDF slides and lecture handouts into clean **GitHub-Flavored Markdown (GFM)**. It preserves mathematical LaTeX formulas ($...$, $$...$$), structured tables, and high-definition extracted figures by interfacing with local or remote Vision-Language Models (VLMs) via **LM Studio** or any OpenAI-compatible API.

Supports both standard **GFM** output and **Obsidian**-optimized formatting with bilingual native callouts.

---

## 📁 Repository Structure

```text
Converter/
├── pipeline.py                 # Unified conversion & repair engine
├── requirements.txt            # Minimal Python dependencies
├── .gitignore                  # Automated exclusions for outputs, venvs, and cache
├── tests/
│   └── test_converter.py       # Offline unit test suite
├── README.md                   # This guide (English)
└── README.it.md                # Italian documentation
```

---

## ⚙️ Prerequisites

1. **Python 3.9+** (3.10+ recommended).
2. **LM Studio** running locally with a Vision-Language Model loaded (e.g., `Qwen3-VL-8B-Instruct` or `qwen2.5-vl-7b-instruct`) with the Local Server started on port `1234` (or fallback `1235`), or an OpenAI-compatible remote endpoint.

---

## 🚀 Quick Start

```bash
# 1. Create a virtual environment
python3 -m venv venv

# 2. Activate the environment
# On macOS/Linux:
source venv/bin/activate
# On Windows:
# .\venv\Scripts\Activate.ps1

# 3. Install dependencies
pip install -r requirements.txt
```

---

## 📖 Usage

### 1. Full Document Conversion (Default GFM)
```bash
python pipeline.py "path/to/document.pdf"
```

### 2. Obsidian Format (with Native Callouts)
```bash
python pipeline.py "path/to/document.pdf" --format obsidian
```

### 3. Page Range & Custom Output Directory
```bash
python pipeline.py "path/to/document.pdf" 1 15 --output-dir ./notes
```

### 4. Post-Processing & Repair Mode
Normalize image extensions, repair orphan links, consolidate page ordering, and clean markdown tags in an existing output folder:
```bash
python pipeline.py --repair output_markdown/
```

### 5. CLI Arguments Reference
| Argument | Default | Description |
|---|---|---|
| `pdf` | positional | Path to the PDF file to convert |
| `--output-dir`, `-o` | `./output_markdown` | Root directory for converted outputs |
| `--workers`, `-w` | `2` | Number of concurrent worker threads |
| `--timeout` | `90` | API request timeout in seconds |
| `--retries` | `2` | Selective automatic retries with Full Jitter exponential backoff |
| `--max-tokens` | `2500` | Maximum generated tokens per page (flags truncation if reached) |
| `--format` | `gfm` | Output markdown dialect: `gfm` or `obsidian` |
| `--ignore-text` | `""` | Comma-separated custom text patterns to remove from headers/footers |
| `--repair` | `None` | Repair and normalize existing Markdown / images in a folder |
| `--dpi` | `140` | Page rasterization resolution |
| `--max-dim` | `1280` | Maximum pixel dimension (reduces visual token count & accelerates inference) |
| `--model` | `qwen3-vl-8b-instruct` | VLM model identifier |
| `--url` | `http://localhost:1234/v1` | OpenAI-compatible endpoint URL |
| `--api-key` | env / `lm-studio` | API key (`LM_STUDIO_API_KEY` or `OPENAI_API_KEY` env vars supported) |
| `--force` | disabled | Reprocess all pages even if already present in output file |

---

## 📂 Output Structure

Converted outputs are created inside `<output_dir>/<pdf_name>/`:
- `<pdf_name>_estratto.md`: Final formatted Markdown document.
- `images/`: High-resolution figures and clustered vector schematics in PNG with persistent SHA-256 deduplication.

### Key Architecture Features
- **Zero-Copy In-Memory Rendering**: MuPDF scales directly to the target pixel bounds in RAM (`io.BytesIO`), avoiding oversized allocations and unnecessary SSD disk wear.
- **Prefix KV Cache Friendly**: Keeps the system prompt strictly invariant across pages to maximize `llama.cpp` / LM Studio KV-cache reuse.
- **Resilient Sliding Window & Circuit Breaker**: Processes pages concurrently with bounded backpressure, commits sequentially with `os.fsync()`, halts safely on repeated server outages, and consolidates resumed pages in strict numerical order.
- **Vector Schema Clustering**: Automatically clusters adjacent vector drawing primitives into distinct PNG figures when slides contain multiple diagrams.

---

## 🧪 Running Tests

Unit tests run completely offline without an active LM Studio instance or binary dependencies:

```bash
python3 -m unittest discover -s tests -p "test_*.py" -v
```
