from __future__ import annotations

import argparse
import base64
import concurrent.futures
import hashlib
import io
import os
import re
import shutil
import sys
import threading
import time
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
DEFAULT_MODEL_ID = os.environ.get("LM_STUDIO_MODEL", "qwen3-vl-8b-instruct")
DEFAULT_DPI_RENDER = 140
DEFAULT_MAX_DIM = 1280  # Tetto massimo pixel per ridurre i visual token (~50% più veloce)
DEFAULT_WORKERS = 2     # Ottimizzato per 16GB Mac con 2 slot paralleli su LM Studio
DEFAULT_TIMEOUT = 90    # Timeout in secondi per le chiamate API verso LM Studio
DEFAULT_RETRIES = 2     # Numero massimo di tentativi automatici su errore temporaneo
DEFAULT_IGNORE_PATTERNS = [
    "University of Brescia",
    "TecMec",
    "Department of Mechanical and Industrial Engineering",
    "Dept. of Mechanical",
]
# ------------------------------------------------------------

SYSTEM_PROMPT = """Sei un ingegnere meccanico e un trascrittore tecnico di altissima precisione.
Il tuo compito è convertire questa pagina/slide di ingegneria meccanica in formato GitHub-Flavored Markdown pulito e rigoroso.

Regole tassative:
1. TESTO ED ELENCHI:
   - Trascrivi integralmente elenchi puntati, definizioni, titoli e paragrafi senza riassumere o omettere concetti tecnici.
2. FORMULE MATEMATICHE:
   - Trascrivi equazioni, variabili e parametri in LaTeX rigoroso ($...$ inline, $$...$$ a blocco). Usa simboli convenzionali (es. $\\sigma$, $\\varepsilon$, $\\tau$, $\\mu$).
3. TABELLE:
   - Ricostruiscile fedelmente in Markdown standard (| col | col |).
4. SCHEMI E FIGURE:
   - Usa ESCLUSIVAMENTE i percorsi file elencati sotto nella sezione 'FIGURE DISPONIBILI'.
   - Per ciascuna figura presente nell'elenco e visibile nella slide, inserisci:
     ![Descrizione Tecnica Chiara](images/nome_file.png)
   - Subito sotto ciascuna figura, aggiungi un blocco di citazione (> **Dettagli tecnici figura:**) spiegando parametri visibili, quote, frecce di flusso, componenti o significato cinematico/fisico.
   - NON inventare percorsi di figure non presenti nell'elenco. Se una slide non ha figure nell'elenco, NON inserire alcun tag ![...](images/...). Se vedi diagrammi o schemi disegnati a mano/vettoriali, descrivili semplicemente nel testo.
5. PULIZIA E FORMATO:
   - Ignora completamente piè di pagina e intestazioni ripetitive di ateneo o dipartimento (es. 'Department of Mechanical and Industrial Engineering', 'University of Brescia', 'TecMec', numeri di slide isolati o loghi).
   - Restituisci ESCLUSIVAMENTE il contenuto Markdown puro, SENZA racchiudere l'intera risposta in blocchi di codice (NON usare ```markdown né ``` all'inizio o alla fine)."""

SEEN_HASHES = set()
HASH_LOCK = threading.Lock()


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
    """Trasforma blocchi di citazione standard in callout nativi per Obsidian."""
    text = re.sub(
        r"^>\s*\*\*Dettagli\s+tecnici\s+figura:?\*\*",
        "> [!info] Dettagli tecnici figura",
        text,
        flags=re.MULTILINE | re.IGNORECASE,
    )
    text = re.sub(
        r"^>\s*\*\*Schema\s+grafico:?\*\*",
        "> [!example] Schema grafico",
        text,
        flags=re.MULTILINE | re.IGNORECASE,
    )
    text = re.sub(
        r"^>\s*\*\*Nota:?\*\*",
        "> [!note] Nota",
        text,
        flags=re.MULTILINE | re.IGNORECASE,
    )
    return text


def fix_image_links(chunk: str, images_dir: Path) -> str:
    """Risolve o converte i link a figure inesistenti nel Markdown."""
    def replacer(match):
        alt_text = match.group(1)
        link_path = match.group(2)
        full_path = images_dir.parent / link_path
        if full_path.exists():
            return match.group(0)

        p_match = re.search(r"pag_(\d+)_", link_path)
        if p_match:
            p_num = p_match.group(1)
            real_imgs = sorted(images_dir.glob(f"pag_{p_num}_fig_*.png"))
            if real_imgs:
                return f"![{alt_text}](images/{real_imgs[0].name})"
            else:
                return f"*{alt_text}*"
        return f"*{alt_text}*"

    return re.sub(r"\!\[(.*?)\]\((images/[^\)]+)\)", replacer, chunk)


def repair_markdown_directory(target_path: Path) -> None:
    """
    Riparazione e normalizzazione di una cartella Markdown/immagini esistente:
    sincronizza file JPEG in PNG, aggiorna estensioni e link orfani e crea backup .bak.
    """
    if Image is None:
        print("Errore: modulo Pillow mancante. Installa le dipendenze con: pip install -r requirements.txt")
        return

    if not target_path.exists():
        print(f"Errore: il percorso '{target_path}' non esiste.")
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
                        print(f"  [!] Errore conversione immagine {img_file.name}: {e}")

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
            print(f"✓ File riparato e sincronizzato: {md_file.name}")

    print("\n✓ Riparazione completata con successo.")


def extract_individual_images(page: fitz.Page, page_num: int, images_dir: Path) -> list[str]:
    """
    Estrae le immagini raster dalla pagina, deduplica loghi e sfondi con hash SHA-256
    e le salva SEMPRE in formato PNG per garantire la coerenza con i link Markdown.
    """
    images_dir.mkdir(parents=True, exist_ok=True)
    image_list = page.get_images(full=True)
    saved_images = []

    valid_img_idx = 1
    for img_info in image_list:
        xref = img_info[0]
        base_image = page.parent.extract_image(xref)
        image_bytes = base_image["image"]

        if base_image["width"] < 50 or base_image["height"] < 50:
            continue

        img_hash = hashlib.sha256(image_bytes).hexdigest()
        with HASH_LOCK:
            if img_hash in SEEN_HASHES:
                continue
            SEEN_HASHES.add(img_hash)

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
                print(f"    [!] Errore salvataggio immagine {file_name}: {e}")
                continue

        saved_images.append(file_name)
        valid_img_idx += 1

    if not saved_images:
        drawings = page.get_drawings()
        pw, ph = page.rect.width, page.rect.height
        bbox = fitz.Rect()
        valid_shapes = 0
        for d in drawings:
            r = d["rect"]
            if r.width >= pw * 0.95 and r.height >= ph * 0.95:
                continue
            if r.x1 <= 85:
                continue
            if r.y1 <= 40 or r.y0 >= ph - 25:
                continue
            bbox.include_rect(r)
            valid_shapes += 1

        if valid_shapes >= 3 and not bbox.is_empty and bbox.width > 60 and bbox.height > 60:
            crop_rect = fitz.Rect(
                max(85, bbox.x0 - 15),
                max(35, bbox.y0 - 15),
                min(pw, bbox.x1 + 15),
                min(ph - 25, bbox.y1 + 15),
            )
            mat = fitz.Matrix(2.0, 2.0)
            pix = page.get_pixmap(matrix=mat, clip=crop_rect)
            file_name = f"pag_{page_num}_fig_1.png"
            image_path = images_dir / file_name
            pix.save(str(image_path))
            saved_images.append(file_name)

    return saved_images


def render_full_page_base64(page: fitz.Page, dpi: int, max_dim: int) -> str:
    """
    Renderizza la pagina PDF a DPI specificato e applica un cap alla dimensione massima (default: 1280px).
    Esegue l'encoding Base64 direttamente in memoria RAM tramite io.BytesIO, eliminando l'I/O disco temporaneo.
    """
    zoom = dpi / 72.0
    mat = fitz.Matrix(zoom, zoom)
    pix = page.get_pixmap(matrix=mat)

    buf = io.BytesIO()
    if max(pix.width, pix.height) > max_dim:
        img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
        img.thumbnail((max_dim, max_dim), Image.Resampling.LANCZOS)
        img.save(buf, format="PNG", optimize=True)
    else:
        buf.write(pix.tobytes("png"))

    return base64.b64encode(buf.getvalue()).decode("utf-8")


def process_page(
    doc: fitz.Document,
    page_idx: int,
    book_dir: Path,
    client: OpenAI,
    model_id: str,
    dpi: int,
    max_dim: int,
    timeout: int = DEFAULT_TIMEOUT,
    retries: int = DEFAULT_RETRIES,
    output_format: str = "gfm",
    ignore_patterns: list[str] | None = None,
) -> str:
    """Elabora una singola pagina PDF inviandola al VLM con retry automatico ed eventuale export Obsidian."""
    page_num = page_idx + 1
    page = doc.load_page(page_idx)

    patterns = ignore_patterns if ignore_patterns is not None else DEFAULT_IGNORE_PATTERNS

    images_dir = book_dir / "images"
    extracted_imgs = extract_individual_images(page, page_num, images_dir)

    base64_img = render_full_page_base64(page, dpi, max_dim)

    if extracted_imgs:
        figure_list_str = "\n".join(f"- images/{fn}" for fn in extracted_imgs)
        figure_clause = (
            f"\n\nFIGURE DISPONIBILI PER QUESTA SLIDE ({len(extracted_imgs)} file estratti):\n"
            f"{figure_list_str}\n"
            "Usa ESCLUSIVAMENTE questi percorsi esatti nei tag ![...](images/nome_file.png). "
            "Associa ciascuna figura al punto corrispondente del testo."
        )
    else:
        figure_clause = (
            "\n\nFIGURE DISPONIBILI: Nessuna immagine raster estratta per questa slide. "
            "NON generare alcun tag ![...](images/...). Se sono presenti diagrammi o schemi nella slide, "
            "descrivili a parole nel testo o con un blocco '> **Schema grafico:**'."
        )

    raw_text = page.get_text("text").strip()
    if raw_text:
        clean_raw_lines = [
            line.strip()
            for line in raw_text.splitlines()
            if line.strip() and not any(p.lower() in line.lower() for p in patterns)
        ]
        clean_raw_text = "\n".join(clean_raw_lines)
        if clean_raw_text:
            text_hint = (
                f"\n\nTESTO DIGITALE ESTRATTO DAL PDF (utilizzalo come traccia OCR rapida per garantire massima velocità e precisione):\n"
                f"\"\"\"\n{clean_raw_text}\n\"\"\""
            )
        else:
            text_hint = ""
    else:
        text_hint = ""

    system_content = SYSTEM_PROMPT + figure_clause + text_hint

    last_error = None
    for attempt in range(1 + retries):
        try:
            response = client.chat.completions.create(
                model=model_id,
                messages=[
                    {"role": "system", "content": system_content},
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": f"Trascrivi integralmente e con rigore ingegneristico la slide {page_num}:",
                            },
                            {
                                "type": "image_url",
                                "image_url": {"url": f"data:image/png;base64,{base64_img}"},
                            },
                        ],
                    },
                ],
                temperature=0.0,
                max_tokens=1500,
                timeout=timeout,
            )
            raw_md = response.choices[0].message.content or ""
            clean_md = clean_markdown_fences(raw_md)

            missing_imgs = [fn for fn in extracted_imgs if f"images/{fn}" not in clean_md]
            if missing_imgs:
                clean_md += "\n\n### Schemi e figure della slide:\n"
                for m_img in missing_imgs:
                    clean_md += f"\n![Figura tecnica](images/{m_img})\n"

            if output_format == "obsidian":
                clean_md = convert_to_obsidian_callouts(clean_md)

            return clean_md
        except Exception as e:
            last_error = e
            if attempt < retries:
                time.sleep(2 ** attempt)
            else:
                raise last_error

    return f"> **[ERRORE ELABORAZIONE PAGINA {page_num}]**: {last_error}"


def worker_task(
    pdf_path: Path,
    page_idx: int,
    book_dir: Path,
    client: OpenAI,
    model_id: str,
    dpi: int,
    max_dim: int,
    timeout: int = DEFAULT_TIMEOUT,
    retries: int = DEFAULT_RETRIES,
    output_format: str = "gfm",
    ignore_patterns: list[str] | None = None,
) -> tuple[int, str]:
    """Task eseguito in parallelo dal ThreadPool: apre un proprio handle fitz thread-safe."""
    page_num = page_idx + 1
    doc = fitz.open(pdf_path)
    try:
        res = process_page(
            doc,
            page_idx,
            book_dir,
            client,
            model_id,
            dpi,
            max_dim,
            timeout,
            retries,
            output_format,
            ignore_patterns,
        )
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

        print(f"[!] Attenzione: Il modello '{model_id}' non risulta caricato in LM Studio.")
        print(f"    Modelli rilevati: {avail_ids}")
        print(f"    Proseguo ugualmente con '{model_id}'.")
        return model_id
    except Exception as e:
        print(f"[!] Errore connessione a LM Studio ({url}): {e}")
        print("    Assicurati che LM Studio sia aperto e con il server locale avviato (porta 1234 o 1235).")
        return model_id


def parse_arguments():
    """Supporta sia flag moderni sia argomenti posizionali legacy."""
    parser = argparse.ArgumentParser(
        description="Estrae testo, formule LaTeX e immagini da slide PDF usando Qwen 3-VL (8B) via LM Studio."
    )
    parser.add_argument("pdf", type=str, nargs="?", default=None, help="Percorso del file PDF da elaborare.")
    parser.add_argument("start", type=int, nargs="?", default=None, help="Pagina iniziale (opzionale).")
    parser.add_argument("end", type=int, nargs="?", default=None, help="Pagina finale (opzionale).")
    parser.add_argument("--start-page", type=int, dest="flag_start", default=None, help="Pagina iniziale.")
    parser.add_argument("--end-page", type=int, dest="flag_end", default=None, help="Pagina finale.")
    parser.add_argument("--dpi", type=int, default=DEFAULT_DPI_RENDER, help="DPI di render (default: 140).")
    parser.add_argument("--max-dim", type=int, default=DEFAULT_MAX_DIM, help="Tetto massimo pixel lato lungo (default: 1280).")
    parser.add_argument("--workers", "-w", type=int, default=DEFAULT_WORKERS, help="Numero richieste concorrenti (default: 2 per 16GB Mac).")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT, help="Timeout in secondi per richiesta API (default: 90).")
    parser.add_argument("--retries", type=int, default=DEFAULT_RETRIES, help="Tentativi di retry su errore API (default: 2).")
    parser.add_argument("--format", type=str, choices=["gfm", "obsidian"], default="gfm", help="Formato Markdown output: 'gfm' o 'obsidian' (callout nativi).")
    parser.add_argument("--ignore-text", type=str, default="", help="Testi o parole chiave addizionali da ignorare nei footer/header (separati da virgola).")
    parser.add_argument("--repair", type=str, default=None, metavar="PATH", help="Ripara file Markdown ed estensioni immagini in una cartella di output.")
    parser.add_argument("--url", type=str, default=DEFAULT_LM_STUDIO_URL, help="URL endpoint OpenAI/LM Studio.")
    parser.add_argument("--model", type=str, default=DEFAULT_MODEL_ID, help="Nome modello (default: qwen3-vl-8b-instruct).")
    parser.add_argument("--force", action="store_true", help="Rielabora anche le pagine già presenti nel file di output.")

    return parser, parser.parse_args()


def main():
    parser, args = parse_arguments()

    if args.repair:
        repair_markdown_directory(Path(args.repair))
        sys.exit(0)

    if not args.pdf:
        parser.print_help()
        sys.exit(1)

    if any(m is None for m in (fitz, OpenAI, Image, tqdm)):
        print("Errore: dipendenze mancanti. Installa le dipendenze con:")
        print("  pip install -r requirements.txt")
        sys.exit(1)

    start_wall_time = time.time()
    pdf_path = Path(args.pdf)
    if not pdf_path.exists():
        print(f"Errore: il file PDF '{pdf_path}' non esiste.")
        sys.exit(1)

    doc_probe = fitz.open(pdf_path)
    total_pages = len(doc_probe)
    doc_probe.close()

    start_p = args.flag_start if args.flag_start is not None else (args.start if args.start is not None else 1)
    end_p = args.flag_end if args.flag_end is not None else (args.end if args.end is not None else total_pages)

    start_p = max(1, start_p)
    end_p = min(total_pages, end_p)

    if start_p > end_p:
        print(f"Errore: pagina iniziale ({start_p}) maggiore della finale ({end_p}).")
        sys.exit(1)

    ignore_list = list(DEFAULT_IGNORE_PATTERNS)
    if args.ignore_text:
        custom_patterns = [p.strip() for p in args.ignore_text.split(",") if p.strip()]
        ignore_list.extend(custom_patterns)

    active_url = resolve_active_lm_studio_url(args.url)
    client = OpenAI(base_url=active_url, api_key="lm-studio")

    print("=" * 65)
    print(f"Elaborazione PDF: {pdf_path.name}")
    print(f"Pagine da elaborare: da {start_p} a {end_p} (totale: {end_p - start_p + 1} su {total_pages})")
    print(f"Concorrenza: {args.workers} workers | Formato: {args.format.upper()} | Retry: {args.retries}")
    print(f"DPI: {args.dpi} | Max Dim: {args.max_dim}px | Timeout: {args.timeout}s")
    print(f"Modello: {args.model} | Endpoint: {active_url}")
    print("=" * 65)

    model_to_use = check_lm_studio_connection(active_url, args.model, client)

    book_folder_name = pdf_path.stem
    book_dir = Path("./output_markdown") / book_folder_name
    book_dir.mkdir(parents=True, exist_ok=True)
    out_file = book_dir / f"{book_folder_name}_estratto.md"

    processed_pages = get_processed_pages(out_file)
    if processed_pages and not args.force:
        print(f"ℹ Trovate {len(processed_pages)} pagine già presenti in '{out_file.name}'.")
        print("  Le pagine già elaborate verranno saltate (usa --force per rielaborare tutto).")

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

    active_futures = {}
    completed_results = {}
    page_iter = iter(pages_to_process)
    next_page_idx = 0
    next_page_to_write = pages_to_process[next_page_idx]

    pbar = tqdm(
        total=len(pages_to_process),
        desc="Elaborazione pagine",
        unit="pag",
        dynamic_ncols=True,
    )

    with open(out_file, "a", encoding="utf-8") as md_out:
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as executor:
            for _ in range(min(args.workers * 2, len(pages_to_process))):
                p_num = next(page_iter, None)
                if p_num is not None:
                    fut = executor.submit(
                        worker_task,
                        pdf_path,
                        p_num - 1,
                        book_dir,
                        client,
                        model_to_use,
                        args.dpi,
                        args.max_dim,
                        args.timeout,
                        args.retries,
                        args.format,
                        ignore_list,
                    )
                    active_futures[fut] = p_num

            try:
                while active_futures or (next_page_to_write in completed_results):
                    if next_page_to_write in completed_results:
                        is_success, res_md = completed_results.pop(next_page_to_write)
                        if is_success:
                            md_out.write(f"\n\n<!-- PAGE {next_page_to_write} -->\n\n")
                            md_out.write(res_md)
                            md_out.flush()
                            pbar.write(f"✓ [Salvata] Pagina {next_page_to_write}/{total_pages} completata.")
                            pages_done += 1
                        else:
                            md_out.write(f"\n\n<!-- PAGE {next_page_to_write} FAILED -->\n\n")
                            md_out.write(res_md)
                            md_out.flush()
                            pbar.write(f"✗ [Registrata con errore] Pagina {next_page_to_write}/{total_pages} (sarà ritentata al prossimo avvio).")
                            pages_failed += 1

                        pbar.update(1)

                        next_page_idx += 1
                        if next_page_idx < len(pages_to_process):
                            next_page_to_write = pages_to_process[next_page_idx]
                        else:
                            next_page_to_write = None
                        continue

                    done, _ = concurrent.futures.wait(
                        active_futures.keys(),
                        return_when=concurrent.futures.FIRST_COMPLETED,
                    )

                    for fut in done:
                        p_num = active_futures.pop(fut)
                        try:
                            _, res_md = fut.result()
                            completed_results[p_num] = (True, res_md)
                            pbar.write(f"  -> [Inferenza completata] Pagina {p_num}")
                        except Exception as e:
                            pbar.write(f"✗ Errore su pagina {p_num}: {e}")
                            completed_results[p_num] = (False, f"> **[ERRORE ELABORAZIONE PAGINA {p_num}]**: {e}")

                        next_p = next(page_iter, None)
                        if next_p is not None:
                            new_fut = executor.submit(
                                worker_task,
                                pdf_path,
                                next_p - 1,
                                book_dir,
                                client,
                                model_to_use,
                                args.dpi,
                                args.max_dim,
                                args.timeout,
                                args.retries,
                                args.format,
                                ignore_list,
                            )
                            active_futures[new_fut] = next_p

            except KeyboardInterrupt:
                pbar.write("\n[!] Ricevuto segnale di interruzione (Ctrl+C). Arresto sicuro dei thread in corso...")
                executor.shutdown(wait=False, cancel_futures=True)
                pbar.write("    Il lavoro completato fino a questo momento è salvato su file.")
            finally:
                pbar.close()

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
