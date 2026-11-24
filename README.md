# PDF to Markdown Converter (VLM Pipeline)

Pipeline Python autonoma ad alta fedeltà per convertire slide e dispense PDF in formato **GitHub-Flavored Markdown** con formule LaTeX, tabelle e figure estratte in alta definizione, interfacciandosi con un modello Vision-Language (VLM) in locale su **LM Studio**.

Supporta esportazione in formato standard **GFM** o ottimizzata per **Obsidian** con callout nativi.

---

## 📁 Struttura della Cartella

```text
Converter/
├── pipeline.py                 # Motore principale di conversione OCR / VLM
├── fix_existing_markdown.py     # Script di utility per pulizia Markdown e fix link immagini
├── requirements.txt            # Dipendenze Python minime
├── PROJECT_CONTEXT.md          # Registro e convenzioni operative del progetto
├── .gitignore                  # Esclusione automatica di output, venv e cache
├── tests/
│   └── test_converter.py       # Test unitari automatici
├── .antigravity/
│   └── instructions.md         # Istruzioni operative per l'agente
└── README.md                   # Questa guida
```

---

## ⚙️ Prerequisiti

1. **Python 3.10+** (o Python 3.9 con standard library).
2. **LM Studio** aperto con un modello VLM caricato (es. `Qwen3-VL-8B-Instruct` o `qwen2.5-vl-7b-instruct`) e Local Server avviato su porta `1234` (o `1235`).

---

## 🚀 Installazione Rapida

```bash
# 1. Creazione ambiente virtuale
python3 -m venv venv

# 2. Attivazione
# Su macOS/Linux:
source venv/bin/activate
# Su Windows:
# .\venv\Scripts\Activate.ps1

# 3. Installazione dipendenze
pip install -r requirements.txt
```

---

## 📖 Utilizzo

### 1. Conversione standard (GFM)
```bash
python pipeline.py "documento.pdf"
```

### 2. Conversione con formato per Obsidian (callout nativi)
```bash
python pipeline.py "documento.pdf" --format obsidian
```

### 3. Conversione di un intervallo di pagine
```bash
python pipeline.py "documento.pdf" 1 15
```

### 4. Tabella dei parametri disponibili
| Parametro | Default | Descrizione |
|---|---|---|
| `--workers`, `-w` | `2` | Numero di thread paralleli per le chiamate VLM |
| `--timeout` | `90` | Timeout in secondi per singola richiesta API |
| `--retries` | `2` | Tentativi di retry automatico con backoff esponenziale su errore |
| `--format` | `gfm` | Formato di output Markdown: `gfm` o `obsidian` |
| `--ignore-text` | `""` | Testi addizionali separati da virgola da filtrare nei footer |
| `--dpi` | `140` | Risoluzione di rendering delle pagine |
| `--max-dim` | `1280` | Dimensione massima lato lungo in pixel (ottimizza token visivi) |
| `--model` | `qwen3-vl-8b-instruct` | ID del modello su LM Studio |
| `--url` | `http://localhost:1234/v1` | Endpoint API compatibile OpenAI / LM Studio |
| `--force` | disattivato | Rielabora tutte le pagine anche se già presenti |

---

## 📂 Output Generato

I file convertiti vengono posizionati in `output_markdown/<nome_pdf>/`:
- `<nome_pdf>_estratto.md`: file Markdown formattato finale.
- `images/`: figure e schemi estratti in formato PNG con deduplica SHA-256.

A fine esecuzione lo script stampa un riepilogo prestazionale con tempo totale, velocità media per pagina e pagine al minuto.

---

## 🧪 Esecuzione dei Test

I test unitari non richiedono un server LM Studio attivo e possono essere eseguiti in qualsiasi momento:

```bash
python3 -m unittest discover -s tests -p "test_*.py" -v
```
