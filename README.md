<p align="center">
  <b>English</b> | <a href="README.it.md">Italiano</a>
</p>

# PDF to Markdown Converter (VLM Pipeline)

A high-fidelity Python pipeline designed to convert complex PDF slides and lecture handouts into clean **GitHub-Flavored Markdown (GFM)**. It preserves mathematical LaTeX formulas ($...$, $$...$$), structured tables, and high-definition extracted figures by interfacing with local Vision-Language Models (VLMs) via **LM Studio**.

Supports both standard **GFM** output and **Obsidian**-optimized formatting with native callouts.

---

## 📁 Repository Structure

```text
Converter/
├── pipeline.py                 # Core OCR / VLM conversion engine
├── fix_existing_markdown.py     # Post-processing script for markdown cleanup & image link fixes
├── requirements.txt            # Minimal Python dependencies
├── PROJECT_CONTEXT.md          # Internal conventions and project status log
├── .gitignore                  # Automated exclusions for outputs, venvs, and cache
├── tests/
│   └── test_converter.py       # Automated unit tests
├── .antigravity/
│   └── instructions.md         # Autonomous agent operational guide
├── README.md                   # This guide (English)
└── README.it.md                # Italian documentation
```

---

## ⚙️ Prerequisites

1. **Python 3.10+** (or Python 3.9 using the standard library).
2. **LM Studio** running locally with a Vision-Language Model loaded (e.g., `Qwen3-VL-8B-Instruct` or `qwen2.5-vl-7b-instruct`) with the Local Server started on port `1234` (or fallback `1235`).

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

### 3. Page Range Conversion
```bash
python pipeline.py "path/to/document.pdf" 1 15
```

### 4. CLI Arguments Reference
| Argument | Default | Description |
|---|---|---|
| `--workers`, `-w` | `2` | Number of concurrent worker threads |
| `--timeout` | `90` | API request timeout in seconds |
| `--retries` | `2` | Automatic retries with exponential backoff on failure |
| `--format` | `gfm` | Output markdown dialect: `gfm` or `obsidian` |
| `--ignore-text` | `""` | Comma-separated custom text patterns to remove from headers/footers |
| `--dpi` | `140` | Page rasterization resolution |
| `--max-dim` | `1280` | Maximum pixel dimension (reduces visual token count & accelerates inference) |
| `--model` | `qwen3-vl-8b-instruct` | VLM model identifier in LM Studio |
| `--url` | `http://localhost:1234/v1` | OpenAI-compatible endpoint URL |
| `--force` | disabled | Reprocess all pages even if already present in output file |

---

## 📂 Output Structure

Converted outputs are created inside `output_markdown/<pdf_name>/`:
- `<pdf_name>_estratto.md`: Final formatted Markdown document.
- `images/`: High-resolution figures and vector schematics in PNG with SHA-256 deduplication.

### Key Features
- **In-Memory Rendering**: Page previews are converted in RAM using `io.BytesIO`, preventing unnecessary SSD disk wear.
- **Smart Resume**: Interrupted runs automatically pick up exactly where they left off, retrying any previously failed pages without reprocessing completed ones.
- **Ordered Concurrent Streaming**: Pages are processed in parallel using thread pools but appended sequentially to maintain strict document order.
- **Performance Summary**: Prints execution metrics including elapsed time, seconds per page, and processing throughput (pages/min).

---

## 🧪 Running Tests

Unit tests do not require an active LM Studio instance and can be executed anytime:

```bash
python3 -m unittest discover -s tests -p "test_*.py" -v
```
