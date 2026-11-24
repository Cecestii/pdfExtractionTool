# PDF to Markdown Converter (VLM Pipeline)

Pipeline Python autonoma ad alta fedeltà per convertire slide e dispense PDF in formato **GitHub-Flavored Markdown** con formule LaTeX, tabelle e figure estratte in alta definizione, interfacciandosi con un modello Vision-Language (VLM) in locale su **LM Studio**.

---

## 📁 Struttura della Cartella

```text
Converter/
├── pipeline.py                 # Motore principale di conversione OCR / VLM
├── fix_existing_markdown.py     # Script di utility per pulizia Markdown e fix link immagini
├── requirements.txt            # Dipendenze Python minime
├── PROJECT_CONTEXT.md          # Registro e convenzioni operative del progetto
├── .gitignore                  # Esclusione automatica di output, venv e cache
├── .antigravity/
│   └── instructions.md         # Istruzioni operative per l'agente
└── README.md                   # Questa guida
```

---

## ⚙️ Prerequisiti

1. **Python 3.10+** installato.
2. **LM Studio** aperto con un modello VLM caricato (es. `Qwen3-VL-8B-Instruct` o `qwen2.5-vl-7b-instruct`) e Local Server avviato su porta `1234` (o `1235`).

---

## 🚀 Installazione

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

### Conversione intero documento
```bash
python pipeline.py "documento.pdf"
```

### Conversione intervallo di pagine
```bash
python pipeline.py "documento.pdf" 1 15
```

### Parametri opzionali
| Parametro | Default | Descrizione |
|---|---|---|
| `--workers`, `-w` | `2` | Numero di worker paralleli |
| `--timeout` | `90` | Timeout in secondi per singola richiesta API |
| `--dpi` | `140` | Risoluzione di rendering delle pagine |
| `--max-dim` | `1280` | Limite pixel lato lungo (riduce token e latenza) |
| `--model` | `qwen3-vl-8b-instruct` | ID modello caricato su LM Studio |
| `--url` | `http://localhost:1234/v1` | Endpoint API compatibile OpenAI |
| `--force` | disattivato | Rielabora tutte le pagine anche se già salvate |

---

## 📂 Output Generato

I file convertiti vengono posizionati in `output_markdown/<nome_pdf>/`:
- `<nome_pdf>_estratto.md`: file Markdown formattato finale.
- `images/`: figure e schemi estratti in formato PNG.

Grazie al **Resume automatico**, in caso di interruzione lo script riprenderà esattamente dalle pagine non ancora elaborate o fallite.
