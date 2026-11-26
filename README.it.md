<p align="center">
  <a href="README.md">English</a> | <b>Italiano</b>
</p>

# PDF to Markdown Converter (VLM Pipeline)

Pipeline Python autonoma ad alta fedeltà per convertire slide e dispense PDF in formato **GitHub-Flavored Markdown (GFM)** con formule LaTeX, tabelle e figure estratte in alta definizione, interfacciandosi con modelli Vision-Language (VLM) in locale su **LM Studio** o endpoint remoti compatibili OpenAI.

Supporta esportazione in formato standard **GFM** o ottimizzata per **Obsidian** con callout nativi bilingui.

---

## 📁 Struttura della Cartella

```text
Converter/
├── pipeline.py                 # Motore unificato di conversione e riparazione
├── requirements.txt            # Dipendenze Python minime
├── .gitignore                  # Esclusione automatica di output, venv e cache
├── tests/
│   └── test_converter.py       # Suite di test unitari offline
├── README.md                   # Guida in inglese
└── README.it.md                # Questa guida (Italiano)
```

---

## ⚙️ Prerequisiti

1. **Python 3.9+** (consigliato 3.10+).
2. **LM Studio** aperto con un modello VLM caricato (es. `Qwen3-VL-8B-Instruct` o `qwen2.5-vl-7b-instruct`) e Local Server avviato su porta `1234` (o fallback `1235`), oppure un endpoint remoto compatibile OpenAI.

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

### 3. Intervallo di pagine e cartella di output personalizzata
```bash
python pipeline.py "documento.pdf" 1 15 --output-dir ./appunti
```

### 4. Modalità Riparazione e Normalizzazione
Per normalizzare estensioni immagini, correggere link orfani, riordinare i blocchi pagina e ripulire tag Markdown in una cartella di output esistente:
```bash
python pipeline.py --repair output_markdown/
```

### 5. Tabella dei parametri disponibili
| Parametro | Default | Descrizione |
|---|---|---|
| `pdf` | posizionale | Percorso del file PDF da elaborare |
| `--output-dir`, `-o` | `./output_markdown` | Cartella radice di destinazione |
| `--workers`, `-w` | `2` | Numero di thread paralleli per le chiamate VLM |
| `--timeout` | `90` | Timeout in secondi per singola richiesta API |
| `--retries` | `2` | Tentativi selettivi su errore transitorio con backoff esponenziale (Full Jitter) |
| `--max-tokens` | `2500` | Limite massimo di token generati per pagina (segnala eventuali troncamenti) |
| `--format` | `gfm` | Formato di output Markdown: `gfm` o `obsidian` |
| `--ignore-text` | `""` | Testi addizionali separati da virgola da filtrare nei footer |
| `--repair` | `None` | Ripara e normalizza file Markdown ed estensioni immagini in una cartella |
| `--dpi` | `140` | Risoluzione di rendering delle pagine |
| `--max-dim` | `1280` | Dimensione massima lato lungo in pixel (ottimizza token visivi) |
| `--model` | `qwen3-vl-8b-instruct` | ID del modello VLM |
| `--url` | `http://localhost:1234/v1` | Endpoint API compatibile OpenAI / LM Studio |
| `--api-key` | env / `lm-studio` | Chiave API per endpoint remoti (supporta `LM_STUDIO_API_KEY` e `OPENAI_API_KEY`) |
| `--force` | disattivato | Rielabora tutte le pagine anche se già presenti |

---

## 📂 Output Generato

I file convertiti vengono posizionati in `<output_dir>/<nome_pdf>/`:
- `<nome_pdf>_estratto.md`: file Markdown formattato finale.
- `images/`: figure e schemi vettoriali estratti in formato PNG con deduplica SHA-256 persistente.

### Caratteristiche Architetturali Principali
- **Rendering Zero-Copy in RAM**: MuPDF calcola la scala target direttamente prima della rasterizzazione in memoria, eliminando allocazioni superflue e usura SSD.
- **Ottimizzato per Prefix KV Caching**: Mantiene il `SYSTEM_PROMPT` invariante tra tutte le pagine per massimizzare il riutilizzo della cache su LM Studio / `llama.cpp`.
- **Sliding Window & Circuit Breaker**: Esecuzione concorrente con backpressure limitata, persistenza ordinata con `os.fsync()`, blocco preventivo su errori ripetuti del server e riordino automatico delle pagine riprese in fase di resume.
- **Clustering Schemi Vettoriali**: Raggruppa automaticamente primitive vettoriali distinte in più figure PNG separate quando una slide contiene più grafici.

---

## 🧪 Esecuzione dei Test

I test unitari girano interamente offline senza richiedere un server LM Studio attivo:

```bash
python3 -m unittest discover -s tests -p "test_*.py" -v
```
