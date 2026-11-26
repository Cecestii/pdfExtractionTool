from __future__ import annotations

import argparse
import base64
import concurrent.futures
import hashlib
import io
import os
import random
import re
import shutil
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

try:
    import fitz  # PyMuPDF
    from openai import OpenAI
    from PIL import Image
    from tqdm import tqdm
except ImportError:
    fitz = None
    OpenAI = None
    Image = None
    tqdm = None

# ---------------- CONFIGURAZIONE PREDEFINITA ----------------
DEFAULT_LM_STUDIO_URL = os.environ.get("LM_STUDIO_URL", "http://localhost:1234/v1")
DEFAULT_API_KEY = os.environ.get("LM_STUDIO_API_KEY") or os.environ.get("OPENAI_API_KEY") or "lm-studio"
DEFAULT_MODEL_ID = os.environ.get("LM_STUDIO_MODEL", "qwen3-vl-8b-instruct")
DEFAULT_OUTPUT_DIR = "./output_markdown"
DEFAULT_DPI_RENDER = 140
DEFAULT_MAX_DIM = 1280          # Tetto massimo pixel per ridurre i visual token (~56% più veloce)
DEFAULT_WORKERS = 2             # Ottimizzato per 16GB Mac con 2 slot paralleli su LM Studio
DEFAULT_TIMEOUT = 90            # Timeout in secondi per le chiamate API verso LM Studio
DEFAULT_RETRIES = 2             # Numero massimo di tentativi automatici su errore transitorio
DEFAULT_MAX_TOKENS = 2500       # Margine ampio per formule LaTeX estese, tabelle e descrizioni
MAX_CONSECUTIVE_FAILURES = 5    # Soglia Circuit Breaker su errori consecutivi di sistema
DEFAULT_IGNORE_PATTERNS = (
    "University of Brescia",
    "TecMec",
    "Department of Mechanical and Industrial Engineering",
    "Dept. of Mechanical",
)
# ------------------------------------------------------------

# Prompt statico invariante tra tutte le pagine per massimizzare il Prefix KV Caching
SYSTEM_PROMPT = """Sei un ingegnere meccanico e un trascrittore tecnico di altissima precisione.
Il tuo compito è convertire questa pagina/slide di ingegneria meccanica in formato GitHub-Flavored Markdown pulito e rigoroso.

Regole tassative:
1. TESTO ED ELENCHI:
   - Trascrivi integralmente elenchi puntati, definizioni, titoli e paragrafi senza riassumere o omettere concetti tecnici.
2. FORMULE MATEMATICHE E PRIORITÀ VISIVA:
   - Trascrivi equazioni, variabili e parametri in LaTeX rigoroso ($...$ inline, $$...$$ a blocco). Usa simboli convenzionali (es. $\\sigma$, $\\varepsilon$, $\\tau$, $\\mu$).
   - Qualora la traccia testuale digitale di riferimento contenga simboli matematici corrotti o disallineati, dai sempre priorità assoluta a ciò che è visibile nell'immagine della slide.
3. TABELLE:
   - Ricostruiscile fedelmente in Markdown standard (| col | col |).
4. SCHEMI E FIGURE:
   - Usa ESCLUSIVAMENTE i percorsi file elencati nel messaggio utente nella sezione 'FIGURE DISPONIBILI'.
   - Per ciascuna figura presente nell'elenco e visibile nella slide, inserisci:
     ![Descrizione Tecnica Chiara](images/nome_file.png)
   - Subito sotto ciascuna figura, aggiungi un blocco di citazione (> **Dettagli tecnici figura:**) spiegando parametri visibili, quote, frecce di flusso, componenti o significato cinematico/fisico.
   - NON inventare percorsi di figure non presenti nell'elenco. Se una slide non ha figure nell'elenco, NON inserire alcun tag ![...](images/...). Se vedi diagrammi o schemi disegnati a mano/vettoriali, descrivili semplicemente nel testo.
5. PULIZIA E FORMATO:
   - Ignora completamente piè di pagina e intestazioni ripetitive di ateneo o dipartimento (es. 'Department of Mechanical and Industrial Engineering', 'University of Brescia', 'TecMec', numeri di slide isolati o loghi).
   - Restituisci ESCLUSIVAMENTE il contenuto Markdown puro, SENZA racchiudere l'intera risposta in blocchi di codice (NON usare ```markdown né ``` all'inizio o alla fine)."""


@dataclass(frozen=True)
class ConversionConfig:
    """Configurazione immutabile per una sessione di conversione PDF -> Markdown."""

    model_id: str = DEFAULT_MODEL_ID
    dpi: int = DEFAULT_DPI_RENDER
    max_dim: int = DEFAULT_MAX_DIM
    timeout: int = DEFAULT_TIMEOUT
    retries: int = DEFAULT_RETRIES
    max_tokens: int = DEFAULT_MAX_TOKENS
    output_format: str = "gfm"
    ignore_patterns: tuple[str, ...] = DEFAULT_IGNORE_PATTERNS


class ConversionSession:
    """Stato thread-safe isolato per singola esecuzione (nessuno stato globale condiviso)."""

    def __init__(self, hash_cache_file: Path | None = None, reset_cache: bool = False):
        self._seen_hashes: set[str] = set()
        self._lock = threading.Lock()
        self._cache_file = hash_cache_file
        if self._cache_file is not None:
            if reset_cache:
                self._cache_file.unlink(missing_ok=True)
            elif self._cache_file.exists():
                self._load_hashes()

    def _load_hashes(self) -> None:
        if self._cache_file is None:
            return
        try:
            lines = self._cache_file.read_text(encoding="utf-8", errors="ignore").splitlines()
            self._seen_hashes.update(h.strip() for h in lines if h.strip())
        except OSError:
            pass

    def register_if_new(self, image_bytes: bytes) -> bool:
        """Registra l'hash SHA-256 in modo atomico; restituisce True se l'immagine non è duplicata."""
        img_hash = hashlib.sha256(image_bytes).hexdigest()
        with self._lock:
            if img_hash in self._seen_hashes:
                return False
            self._seen_hashes.add(img_hash)
            if self._cache_file is not None:
                try:
                    self._cache_file.parent.mkdir(parents=True, exist_ok=True)
                    with open(self._cache_file, "a", encoding="utf-8") as hf:
                        hf.write(f"{img_hash}\n")
                except OSError:
                    pass
            return True


def clean_markdown_fences(text: str) -> str:
    """Rimuove eventuali blocchi di pensiero (<think>...</think>) e wrapper di codice spuri (```markdown) generati dal VLM."""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    lines = text.strip().splitlines()
    if not lines:
        return ""
    filtered = []
    for line in lines:
        if line.strip() in ("```markdown", "```md"):
            continue
        filtered.append(line)
    if filtered and filtered[0].strip() == "```":
        filtered = filtered[1:]
    if filtered and filtered[-1].strip() == "```":
        filtered = filtered[:-1]
    return "\n".join(filtered).strip()


def convert_to_obsidian_callouts(text: str) -> str:
    """Trasforma blocchi di citazione standard (IT/EN) in callout nativi per Obsidian."""
    text = re.sub(
        r"^>\s*\*\*(?:Dettagli\s+tecnici\s+figura|Technical\s+(?:figure\s+)?details|Figure\s+details):?\*\*",
        "> [!info] Dettagli tecnici figura",
        text,
        flags=re.MULTILINE | re.IGNORECASE,
    )
    text = re.sub(
        r"^>\s*\*\*(?:Schema\s+grafico|Graphic\s+schema|Diagram):?\*\*",
        "> [!example] Schema grafico",
        text,
        flags=re.MULTILINE | re.IGNORECASE,
    )
    text = re.sub(
        r"^>\s*\*\*(?:Nota|Note):?\*\*",
        "> [!note] Nota",
        text,
        flags=re.MULTILINE | re.IGNORECASE,
    )
    return text


def fix_image_links(chunk: str, images_dir: Path) -> str:
    """Risolve o converte i link a figure inesistenti nel Markdown con verifica anti path-traversal."""
    resolved_root = images_dir.parent.resolve()

    def replacer(match: re.Match[str]) -> str:
        alt_text = match.group(1)
        link_path = match.group(2)
        full_path = images_dir.parent / link_path
        try:
            resolved_full = full_path.resolve()
            if resolved_full.is_relative_to(resolved_root) and resolved_full.exists():
                return match.group(0)
        except (OSError, ValueError):
            pass

        p_match = re.search(r"pag_(\d+)_", link_path)
        if p_match:
            p_num = p_match.group(1)
            real_imgs = sorted(images_dir.glob(f"pag_{p_num}_fig_*.png"))
            if real_imgs:
                return f"![{alt_text}](images/{real_imgs[0].name})"
            return f"*{alt_text}*"
        return f"*{alt_text}*"

    return re.sub(r"\!\[(.*?)\]\((images/[^\)]+)\)", replacer, chunk)


def consolidate_markdown_pages(md_path: Path) -> None:
    """
    Riordina in modo deterministico i blocchi pagina nel file Markdown finale,
    eliminando vecchi segnaposto FAILED qualora la pagina sia stata successivamente recuperata.
    """
    if not md_path.exists():
        return

    content = md_path.read_text(encoding="utf-8", errors="ignore")
    marker_pattern = re.compile(r"(<!--\s*PAGE\s+(\d+)(\s+FAILED)?\s*-->)", re.IGNORECASE)
    parts = marker_pattern.split(content)

    # parts ha struttura: [preambolo, marker_full, num_str, failed_opt, body, ...]
    if len(parts) < 5:
        return

    preamble = parts[0].strip()
    pages: dict[int, tuple[bool, str]] = {}

    idx = 1
    while idx + 3 < len(parts):
        page_num = int(parts[idx + 1])
        is_failed = bool(parts[idx + 2])
        body = parts[idx + 3].strip()
        idx += 4

        prev = pages.get(page_num)
        # Mantieni la versione valida rispetto a FAILED, oppure la più recente tra versioni dello stesso tipo
        if prev is None or (prev[0] and not is_failed) or (prev[0] == is_failed):
            pages[page_num] = (is_failed, body)

    ordered_chunks: list[str] = []
    if preamble:
        ordered_chunks.append(preamble)

    for page_num in sorted(pages):
        is_failed, body = pages[page_num]
        tag = f"<!-- PAGE {page_num} FAILED -->" if is_failed else f"<!-- PAGE {page_num} -->"
        ordered_chunks.append(f"{tag}\n\n{body}")

    final_text = "\n\n".join(ordered_chunks).strip() + "\n"
    tmp_path = md_path.with_suffix(".md.tmp")
    tmp_path.write_text(final_text, encoding="utf-8")
    os.replace(tmp_path, md_path)


def repair_markdown_directory(target_path: Path) -> None:
    """
    Riparazione e normalizzazione di una cartella Markdown/immagini esistente:
    sincronizza file JPEG in PNG, aggiorna estensioni e link orfani e crea backup .bak.
    """
    if Image is None:
        print("Errore: modulo Pillow mancante. Installa le dipendenze con: pip install -r requirements.txt", file=sys.stderr)
        return

    if not target_path.exists():
        print(f"Errore: il percorso '{target_path}' non esiste.", file=sys.stderr)
        return

    subdirs = [target_path] if (target_path / "images").exists() else [d for d in target_path.iterdir() if d.is_dir()]

    print("=" * 65)
    print(f"Avvio riparazione per: {target_path}")
    print("=" * 65)

    for item in sorted(subdirs):
        images_dir = item / "images"
        if images_dir.exists():
            for img_file in list(images_dir.glob("*.jpeg")) + list(images_dir.glob("*.jpg")):
                target_png = img_file.with_suffix(".png")
                if not target_png.exists():
                    try:
                        with Image.open(img_file) as pil_img:
                            if pil_img.mode in ("CMYK", "P"):
                                pil_img = pil_img.convert("RGB")
                            pil_img.save(target_png, format="PNG")
                    except Exception as e:
                        print(f"  [!] Errore conversione immagine {img_file.name}: {e}", file=sys.stderr)

        for md_file in item.glob("*.md"):
            if md_file.name.endswith(".bak"):
                continue

            bak_path = md_file.with_suffix(".md.bak")
            if not bak_path.exists():
                shutil.copy2(md_file, bak_path)

            content = md_file.read_text(encoding="utf-8", errors="ignore")
            page_chunks = re.split(r"(<!--\s*PAGE\s+\d+(?:\s+FAILED)?\s*-->)", content, flags=re.IGNORECASE)

            new_chunks = []
            for chunk in page_chunks:
                if re.match(r"^<!--\s*PAGE\s+\d+(?:\s+FAILED)?\s*-->$", chunk.strip(), flags=re.IGNORECASE):
                    new_chunks.append(f"\n\n{chunk.strip()}\n\n")
                else:
                    cleaned = clean_markdown_fences(chunk)
                    if cleaned:
                        cleaned = re.sub(r"(\!\[.*?\]\(images/pag_\d+_fig_[a-zA-Z0-9_-]+)\.(?:jpeg|jpg)\)", r"\1.png)", cleaned)
                        if images_dir.exists():
                            cleaned = fix_image_links(cleaned, images_dir)
                        new_chunks.append(cleaned)

            final_text = "".join(new_chunks).strip() + "\n"
            md_file.write_text(final_text, encoding="utf-8")
            consolidate_markdown_pages(md_file)
            print(f"✓ File riparato e sincronizzato: {md_file.name}")

    print("\n✓ Riparazione completata con successo.")


def _cluster_vector_rects(
    rects: list[fitz.Rect], margin: float = 25.0, min_shapes: int = 3
) -> list[fitz.Rect]:
    """Raggruppa primitive vettoriali contigue in cluster separati per estrarre schemi multipli."""
    clusters: list[tuple[fitz.Rect, int]] = []
    for r in rects:
        expanded = fitz.Rect(r.x0 - margin, r.y0 - margin, r.x1 + margin, r.y1 + margin)
        merged_idx = None
        for idx, (c_rect, c_count) in enumerate(clusters):
            if c_rect.intersects(expanded):
                c_rect.include_rect(r)
                clusters[idx] = (c_rect, c_count + 1)
                merged_idx = idx
                break
        if merged_idx is None:
            clusters.append((fitz.Rect(r), 1))

    changed = True
    while changed:
        changed = False
        new_clusters: list[tuple[fitz.Rect, int]] = []
        for c_rect, c_count in clusters:
            c_exp = fitz.Rect(c_rect.x0 - margin, c_rect.y0 - margin, c_rect.x1 + margin, c_rect.y1 + margin)
            merged = False
            for idx, (n_rect, n_count) in enumerate(new_clusters):
                if n_rect.intersects(c_exp):
                    n_rect.include_rect(c_rect)
                    new_clusters[idx] = (n_rect, n_count + c_count)
                    merged = True
                    changed = True
                    break
            if not merged:
                new_clusters.append((c_rect, c_count))
        clusters = new_clusters

    valid = [
        c_rect for c_rect, c_count in clusters
        if c_count >= min_shapes and not c_rect.is_empty and c_rect.width > 60 and c_rect.height > 60
    ]
    valid.sort(key=lambda b: (round(b.y0 / 40), b.x0))
    return valid


def extract_individual_images(
    page: fitz.Page,
    page_num: int,
    images_dir: Path,
    session: ConversionSession | None = None,
) -> list[str]:
    """
    Estrae le immagini raster dalla pagina, deduplica loghi e sfondi con hash SHA-256
    e le salva SEMPRE in formato PNG. In assenza di bitmap, raggruppa ed estrae schemi vettoriali.
    """
    active_session = session or ConversionSession()
    images_dir.mkdir(parents=True, exist_ok=True)
    image_list = page.get_images(full=True)
    saved_images: list[str] = []

    valid_img_idx = 1
    for img_info in image_list:
        xref = img_info[0]
        base_image = page.parent.extract_image(xref)
        image_bytes = base_image["image"]

        if base_image["width"] < 50 or base_image["height"] < 50:
            continue

        if not active_session.register_if_new(image_bytes):
            continue

        file_name = f"pag_{page_num}_fig_{valid_img_idx}.png"
        image_path = images_dir / file_name

        try:
            with Image.open(io.BytesIO(image_bytes)) as pil_img:
                if pil_img.mode in ("CMYK", "P"):
                    pil_img = pil_img.convert("RGB")
                pil_img.save(image_path, format="PNG")
        except Exception:
            try:
                pix = fitz.Pixmap(page.parent, xref)
                if pix.n >= 5:
                    pix = fitz.Pixmap(fitz.csRGB, pix)
                pix.save(str(image_path))
            except Exception as e:
                print(f"    [!] Errore salvataggio immagine {file_name}: {e}", file=sys.stderr)
                continue

        saved_images.append(file_name)
        valid_img_idx += 1

    if not saved_images:
        drawings = page.get_drawings()
        pw, ph = page.rect.width, page.rect.height
        valid_rects = []
        for d in drawings:
            r = d["rect"]
            if r.width >= pw * 0.95 and r.height >= ph * 0.95:
                continue
            if r.x1 <= 85 or r.y1 <= 40 or r.y0 >= ph - 25:
                continue
            valid_rects.append(r)

        for idx, bbox in enumerate(_cluster_vector_rects(valid_rects), start=1):
            crop_rect = fitz.Rect(
                max(85, bbox.x0 - 15),
                max(35, bbox.y0 - 15),
                min(pw, bbox.x1 + 15),
                min(ph - 25, bbox.y1 + 15),
            )
            pix = page.get_pixmap(matrix=fitz.Matrix(2.0, 2.0), clip=crop_rect)
            file_name = f"pag_{page_num}_fig_{idx}.png"
            pix.save(str(images_dir / file_name))
            saved_images.append(file_name)

    return saved_images


def render_full_page_base64(page: fitz.Page, dpi: int, max_dim: int) -> str:
    """
    Renderizza la pagina PDF direttamente alla scala target in MuPDF (zero-copy, senza Pillow)
    e restituisce la stringa Base64 PNG. Previene allocazioni oversize su PDF ad alta risoluzione.
    """
    rect = page.rect
    raw_scale = dpi / 72.0
    max_side = max(rect.width, rect.height)
    scale = min(raw_scale, max_dim / max_side) if max_side > 0 and max_dim > 0 else raw_scale

    pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
    return base64.b64encode(pix.tobytes("png")).decode("ascii")


def is_transient_error(exc: Exception) -> bool:
    """Distingue errori transitori (timeout, rete, 429, 5xx) da errori permanenti (400, 401, 404)."""
    status_code = getattr(exc, "status_code", None)
    if isinstance(status_code, int):
        return status_code in (408, 429) or status_code >= 500
    exc_name = type(exc).__name__.lower()
    if any(k in exc_name for k in ("timeout", "connection", "ratelimit", "internalserver", "apiconnection")):
        return True
    msg = str(exc).lower()
    return any(k in msg for k in ("timed out", "timeout", "connection", "502", "503", "504", "429", "reset by peer"))


def process_page(
    doc: fitz.Document,
    page_idx: int,
    book_dir: Path,
    client: OpenAI,
    config: ConversionConfig,
    session: ConversionSession | None = None,
) -> str:
    """Elabora una singola pagina PDF inviandola al VLM con retry selettivo (Full Jitter) ed export GFM/Obsidian."""
    page_num = page_idx + 1
    page = doc.load_page(page_idx)

    images_dir = book_dir / "images"
    extracted_imgs = extract_individual_images(page, page_num, images_dir, session)
    base64_img = render_full_page_base64(page, config.dpi, config.max_dim)

    if extracted_imgs:
        figure_list_str = "\n".join(f"- images/{fn}" for fn in extracted_imgs)
        figure_clause = (
            f"FIGURE DISPONIBILI PER QUESTA SLIDE ({len(extracted_imgs)} file estratti):\n"
            f"{figure_list_str}\n"
            "Usa ESCLUSIVAMENTE questi percorsi esatti nei tag ![...](images/nome_file.png). "
            "Associa ciascuna figura al punto corrispondente del testo."
        )
    else:
        figure_clause = (
            "FIGURE DISPONIBILI: Nessuna immagine raster estratta per questa slide. "
            "NON generare alcun tag ![...](images/...). Se sono presenti diagrammi o schemi nella slide, "
            "descrivili a parole nel testo o con un blocco '> **Schema grafico:**'."
        )

    text_hint = ""
    raw_text = page.get_text("text").strip()
    if raw_text:
        clean_raw_lines = [
            line.strip()
            for line in raw_text.splitlines()
            if line.strip() and not any(p.lower() in line.lower() for p in config.ignore_patterns)
        ]
        clean_raw_text = "\n".join(clean_raw_lines)
        if clean_raw_text:
            text_hint = (
                "TESTO DIGITALE ESTRATTO DAL PDF (traccia OCR di supporto; "
                "dai priorità visiva all'immagine in caso di formule ambigue):\n"
                f"\"\"\"\n{clean_raw_text}\n\"\"\""
            )

    # Inserimento parti dinamiche nel messaggio utente per mantenere invariante SYSTEM_PROMPT (KV Prefix Cache)
    user_text_parts = [f"Trascrivi integralmente e con rigore ingegneristico la slide {page_num}:", figure_clause]
    if text_hint:
        user_text_parts.append(text_hint)
    user_prompt_text = "\n\n".join(user_text_parts)

    last_error: Exception | None = None
    for attempt in range(1 + config.retries):
        try:
            response = client.chat.completions.create(
                model=config.model_id,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": user_prompt_text},
                            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{base64_img}"}},
                        ],
                    },
                ],
                temperature=0.0,
                max_tokens=config.max_tokens,
                timeout=config.timeout,
            )
            choice = response.choices[0]
            raw_md = choice.message.content or ""
            clean_md = clean_markdown_fences(raw_md)

            missing_imgs = [fn for fn in extracted_imgs if f"images/{fn}" not in clean_md]
            if missing_imgs:
                clean_md += "\n\n### Schemi e figure della slide:\n"
                for m_img in missing_imgs:
                    clean_md += f"\n![Figura tecnica](images/{m_img})\n"

            if config.output_format == "obsidian":
                clean_md = convert_to_obsidian_callouts(clean_md)

            if getattr(choice, "finish_reason", None) == "length":
                clean_md += "\n\n<!-- [WARNING: OUTPUT TRUNCATED - increase --max-tokens] -->"

            return clean_md
        except Exception as e:
            last_error = e
            if attempt < config.retries and is_transient_error(e):
                cap = min(30.0, (2 ** attempt) + random.uniform(0.0, 1.0))
                time.sleep(random.uniform(0.5, max(0.5, cap)))
            else:
                raise last_error

    return f"> **[ERRORE ELABORAZIONE PAGINA {page_num}]**: {last_error}"


def worker_task(
    pdf_path: Path,
    page_idx: int,
    book_dir: Path,
    client: OpenAI,
    config: ConversionConfig,
    session: ConversionSession,
) -> tuple[int, str]:
    """Task eseguito in parallelo dal ThreadPool: apre un proprio handle fitz thread-safe."""
    page_num = page_idx + 1
    doc = fitz.open(pdf_path)
    try:
        res = process_page(doc, page_idx, book_dir, client, config, session)
        return page_num, res
    finally:
        doc.close()


def get_processed_pages(md_path: Path) -> set[int]:
    """
    Legge il file Markdown di output e restituisce l'insieme dei numeri di pagina già completati con successo.
    Esclude deliberatamente eventuali pagine marcate come FAILED per permettere il re-try automatico.
    """
    if not md_path.exists():
        return set()
    try:
        content = md_path.read_text(encoding="utf-8", errors="ignore")
        matches = re.findall(r"<!--\s*PAGE\s+(\d+)\s*-->", content, re.IGNORECASE)
        return {int(m) for m in matches}
    except Exception:
        return set()


def resolve_active_lm_studio_url(requested_url: str) -> str:
    """Se l'URL predefinito non risponde, tenta automaticamente porte comuni (1234, 1235)."""
    import urllib.request
    candidate_urls = [requested_url]
    if "1234" in requested_url:
        candidate_urls.append(requested_url.replace("1234", "1235"))
    elif "1235" in requested_url:
        candidate_urls.append(requested_url.replace("1235", "1234"))

    for u in candidate_urls:
        try:
            req = urllib.request.Request(f"{u.rstrip('/')}/models", headers={"User-Agent": "pipeline"})
            with urllib.request.urlopen(req, timeout=1.5) as resp:
                if resp.status == 200:
                    return u
        except Exception:
            continue
    return requested_url


def check_lm_studio_connection(url: str, model_id: str, client: OpenAI) -> str:
    """Verifica rapida della connessione a LM Studio prima di avviare il processo e valida il modello."""
    try:
        models = client.models.list()
        avail_ids = [m.id for m in models.data]
        if model_id in avail_ids:
            print(f"✓ Connessione a LM Studio OK (Modello: {model_id})")
            return model_id
        matching = [m_id for m_id in avail_ids if "qwen3" in m_id.lower() or "vl" in m_id.lower()]
        if matching:
            print(f"[i] Modello esatto '{model_id}' non nell'elenco, selezionato automaticamente '{matching[0]}'")
            return matching[0]

        print(f"[!] Attenzione: Il modello '{model_id}' non risulta caricato in LM Studio.", file=sys.stderr)
        print(f"    Modelli rilevati: {avail_ids}", file=sys.stderr)
        print(f"    Proseguo ugualmente con '{model_id}'.", file=sys.stderr)
        return model_id
    except Exception as e:
        print(f"[!] Errore connessione a LM Studio ({url}): {e}", file=sys.stderr)
        print("    Assicurati che LM Studio sia aperto e con il server locale avviato (porta 1234 o 1235).", file=sys.stderr)
        return model_id


def parse_arguments(argv: list[str] | None = None):
    """Supporta sia flag moderni sia argomenti posizionali legacy."""
    parser = argparse.ArgumentParser(
        description="Estrae testo, formule LaTeX e immagini da slide PDF usando Qwen 3-VL (8B) via LM Studio."
    )
    parser.add_argument("pdf", type=str, nargs="?", default=None, help="Percorso del file PDF da elaborare.")
    parser.add_argument("start", type=int, nargs="?", default=None, help="Pagina iniziale (opzionale).")
    parser.add_argument("end", type=int, nargs="?", default=None, help="Pagina finale (opzionale).")
    parser.add_argument("--start-page", type=int, dest="flag_start", default=None, help="Pagina iniziale.")
    parser.add_argument("--end-page", type=int, dest="flag_end", default=None, help="Pagina finale.")
    parser.add_argument("--output-dir", "-o", type=str, default=DEFAULT_OUTPUT_DIR, help="Cartella radice di destinazione (default: ./output_markdown).")
    parser.add_argument("--dpi", type=int, default=DEFAULT_DPI_RENDER, help="DPI di render (default: 140).")
    parser.add_argument("--max-dim", type=int, default=DEFAULT_MAX_DIM, help="Tetto massimo pixel lato lungo (default: 1280).")
    parser.add_argument("--max-tokens", type=int, default=DEFAULT_MAX_TOKENS, help="Limite massimo token per pagina generata (default: 2500).")
    parser.add_argument("--workers", "-w", type=int, default=DEFAULT_WORKERS, help="Numero richieste concorrenti (default: 2 per 16GB Mac).")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT, help="Timeout in secondi per richiesta API (default: 90).")
    parser.add_argument("--retries", type=int, default=DEFAULT_RETRIES, help="Tentativi di retry su errore transitorio (default: 2).")
    parser.add_argument("--format", type=str, choices=["gfm", "obsidian"], default="gfm", help="Formato Markdown output: 'gfm' o 'obsidian' (callout nativi).")
    parser.add_argument("--ignore-text", type=str, default="", help="Testi o parole chiave addizionali da ignorare nei footer/header (separati da virgola).")
    parser.add_argument("--repair", type=str, default=None, metavar="PATH", help="Ripara file Markdown ed estensioni immagini in una cartella di output.")
    parser.add_argument("--url", type=str, default=DEFAULT_LM_STUDIO_URL, help="URL endpoint OpenAI/LM Studio.")
    parser.add_argument("--api-key", type=str, default=DEFAULT_API_KEY, help="API key per endpoint remoto (default: env LM_STUDIO_API_KEY / OPENAI_API_KEY o 'lm-studio').")
    parser.add_argument("--model", type=str, default=DEFAULT_MODEL_ID, help="Nome modello (default: qwen3-vl-8b-instruct).")
    parser.add_argument("--force", action="store_true", help="Rielabora anche le pagine già presenti nel file di output.")

    return parser, parser.parse_args(argv)


def _write_page_record(md_out, page_num: int, is_success: bool, content: str) -> None:
    """Scrive una singola pagina su disco garantendo il flush su kernel e dispositivo fisico."""
    tag = f"<!-- PAGE {page_num} -->" if is_success else f"<!-- PAGE {page_num} FAILED -->"
    md_out.write(f"\n\n{tag}\n\n{content}")
    md_out.flush()
    try:
        os.fsync(md_out.fileno())
    except OSError:
        pass


def main():
    parser, args = parse_arguments()

    if args.repair:
        repair_markdown_directory(Path(args.repair))
        sys.exit(0)

    if not args.pdf:
        parser.print_help()
        sys.exit(1)

    if any(m is None for m in (fitz, OpenAI, Image, tqdm)):
        print("Errore: dipendenze mancanti. Installa le dipendenze con:", file=sys.stderr)
        print("  pip install -r requirements.txt", file=sys.stderr)
        sys.exit(1)

    start_wall_time = time.time()
    pdf_path = Path(args.pdf)
    if not pdf_path.exists():
        print(f"Errore: il file PDF '{pdf_path}' non esiste.", file=sys.stderr)
        sys.exit(1)

    doc_probe = fitz.open(pdf_path)
    total_pages = len(doc_probe)
    doc_probe.close()

    start_p = args.flag_start if args.flag_start is not None else (args.start if args.start is not None else 1)
    end_p = args.flag_end if args.flag_end is not None else (args.end if args.end is not None else total_pages)

    start_p = max(1, start_p)
    end_p = min(total_pages, end_p)

    if start_p > end_p:
        print(f"Errore: pagina iniziale ({start_p}) maggiore della finale ({end_p}).", file=sys.stderr)
        sys.exit(1)

    ignore_list = list(DEFAULT_IGNORE_PATTERNS)
    if args.ignore_text:
        ignore_list.extend(p.strip() for p in args.ignore_text.split(",") if p.strip())

    active_url = resolve_active_lm_studio_url(args.url)
    client = OpenAI(base_url=active_url, api_key=args.api_key)

    print("=" * 65)
    print(f"Elaborazione PDF: {pdf_path.name}")
    print(f"Pagine da elaborare: da {start_p} a {end_p} (totale: {end_p - start_p + 1} su {total_pages})")
    print(f"Concorrenza: {args.workers} workers | Formato: {args.format.upper()} | Retry: {args.retries}")
    print(f"DPI: {args.dpi} | Max Dim: {args.max_dim}px | Max Tokens: {args.max_tokens} | Timeout: {args.timeout}s")
    print(f"Modello: {args.model} | Endpoint: {active_url}")
    print("=" * 65)

    model_to_use = check_lm_studio_connection(active_url, args.model, client)

    config = ConversionConfig(
        model_id=model_to_use,
        dpi=args.dpi,
        max_dim=args.max_dim,
        timeout=args.timeout,
        retries=args.retries,
        max_tokens=args.max_tokens,
        output_format=args.format,
        ignore_patterns=tuple(ignore_list),
    )

    book_folder_name = pdf_path.stem
    book_dir = Path(args.output_dir) / book_folder_name
    book_dir.mkdir(parents=True, exist_ok=True)
    out_file = book_dir / f"{book_folder_name}_estratto.md"

    had_existing_file = out_file.exists()
    processed_pages = get_processed_pages(out_file)
    if processed_pages and not args.force:
        print(f"ℹ Trovate {len(processed_pages)} pagine già presenti in '{out_file.name}'.")
        print("  Le pagine già elaborate verranno saltate (usa --force per rielaborare tutto).")

    session = ConversionSession(hash_cache_file=book_dir / ".seen_hashes", reset_cache=args.force)

    print(f"\nDestinazione: {out_file}\n")

    pages_to_process = [
        p + 1 for p in range(start_p - 1, end_p)
        if (p + 1 not in processed_pages) or args.force
    ]
    pages_skipped = (end_p - start_p + 1) - len(pages_to_process)

    if not pages_to_process:
        print("✓ Tutte le pagine richieste risultano già elaborate nel file di destinazione!")
        sys.exit(0)

    print(f"Avvio elaborazione concorrente su {len(pages_to_process)} pagine rimanenti...\n")

    pages_done = 0
    pages_failed = 0
    consecutive_failures = 0

    active_futures: dict[concurrent.futures.Future, int] = {}
    completed_results: dict[int, tuple[bool, str]] = {}
    next_submit_idx = 0
    next_write_idx = 0
    max_window_distance = max(args.workers * 3, 4)

    def submit_next(executor_pool: concurrent.futures.ThreadPoolExecutor) -> bool:
        nonlocal next_submit_idx
        if next_submit_idx >= len(pages_to_process):
            return False
        if (next_submit_idx - next_write_idx) >= max_window_distance:
            return False
        p_num = pages_to_process[next_submit_idx]
        next_submit_idx += 1
        fut = executor_pool.submit(worker_task, pdf_path, p_num - 1, book_dir, client, config, session)
        active_futures[fut] = p_num
        return True

    pbar = tqdm(
        total=len(pages_to_process),
        desc="Elaborazione pagine",
        unit="pag",
        dynamic_ncols=True,
    )

    executor = concurrent.futures.ThreadPoolExecutor(max_workers=args.workers)
    interrupted = False
    try:
        with open(out_file, "a", encoding="utf-8") as md_out:
            for _ in range(min(args.workers * 2, len(pages_to_process))):
                submit_next(executor)

            while active_futures or (
                next_write_idx < len(pages_to_process)
                and pages_to_process[next_write_idx] in completed_results
            ):
                if next_write_idx < len(pages_to_process):
                    target_page = pages_to_process[next_write_idx]
                    if target_page in completed_results:
                        is_success, res_md = completed_results.pop(target_page)
                        _write_page_record(md_out, target_page, is_success, res_md)
                        if is_success:
                            pbar.write(f"✓ [Salvata] Pagina {target_page}/{total_pages} completata.")
                            pages_done += 1
                        else:
                            pbar.write(f"✗ [Registrata con errore] Pagina {target_page}/{total_pages} (sarà ritentata al prossimo avvio).")
                            pages_failed += 1
                        pbar.update(1)
                        next_write_idx += 1
                        # Riempi la sliding window ora che la testa è avanzata
                        while len(active_futures) < args.workers * 2 and submit_next(executor):
                            pass
                        continue

                if not active_futures:
                    break

                done, _ = concurrent.futures.wait(
                    active_futures.keys(),
                    return_when=concurrent.futures.FIRST_COMPLETED,
                )

                for fut in done:
                    p_num = active_futures.pop(fut)
                    try:
                        _, res_md = fut.result()
                        completed_results[p_num] = (True, res_md)
                        consecutive_failures = 0
                        pbar.write(f"  -> [Inferenza completata] Pagina {p_num}")
                    except Exception as e:
                        consecutive_failures += 1
                        pbar.write(f"✗ Errore su pagina {p_num}: {e}")
                        completed_results[p_num] = (False, f"> **[ERRORE ELABORAZIONE PAGINA {p_num}]**: {e}")

                    if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                        pbar.write(
                            f"\n[!] Circuit Breaker aperto: {consecutive_failures} errori consecutivi. "
                            "Arresto preventivo per evitare stallo su server non raggiungibile."
                        )
                        for pending_fut in list(active_futures.keys()):
                            pending_fut.cancel()
                        active_futures.clear()
                        break

                    submit_next(executor)

    except KeyboardInterrupt:
        interrupted = True
        pbar.write("\n[!] Ricevuto segnale di interruzione (Ctrl+C). Arresto immediato...")
    finally:
        executor.shutdown(wait=not interrupted, cancel_futures=interrupted)
        pbar.close()

    # Se è una ripresa o un --force su file esistente, riordina e ripulisce i vecchi FAILED
    if had_existing_file and (pages_done > 0 or pages_failed > 0):
        consolidate_markdown_pages(out_file)

    elapsed = time.time() - start_wall_time
    sec_per_page = (elapsed / pages_done) if pages_done > 0 else 0
    pages_per_min = (pages_done / (elapsed / 60.0)) if elapsed > 0 else 0

    print("\n" + "=" * 65)
    print("📊 RIEPILOGO ELABORAZIONE")
    print(f"- Pagine elaborate con successo: {pages_done}")
    print(f"- Pagine saltate (già presenti): {pages_skipped}")
    print(f"- Pagine con errori:            {pages_failed}")
    print(f"- Tempo totale trascorso:       {elapsed:.1f}s")
    if pages_done > 0:
        print(f"- Velocità media:               {sec_per_page:.1f} s/pagina (~{pages_per_min:.1f} pag/min)")
    print(f"- File di output:               {out_file}")
    print("=" * 65)


if __name__ == "__main__":
    main()
