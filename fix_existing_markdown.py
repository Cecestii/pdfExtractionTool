from __future__ import annotations

import io
import os
import re
import shutil
from pathlib import Path
from PIL import Image

OUTPUT_DIR = Path("./output_markdown")

# Pattern di righe da rimuovere (header/footer ripetitivi di ateneo)
DEPT_PATTERNS = [
    re.compile(r"^#*\s*Dept\.?\s+of\s+Mechanical\s+and\s+Industrial\s+Engineering.*Brescia.*$", re.IGNORECASE),
    re.compile(r"^#*\s*Department\s+of\s+Mechanical\s+and\s+Industrial\s+Engineering.*Brescia.*$", re.IGNORECASE),
    re.compile(r"^#*\s*University\s+of\s+Brescia\s+\(Italy\)\s*$", re.IGNORECASE),
]

def convert_images_to_png(images_dir: Path) -> tuple[int, int]:
    """Converte tutti i file .jpeg/.jpg in .png se il .png corrispondente non esiste già."""
    if not images_dir.exists():
        return 0, 0

    converted_count = 0
    already_png_count = 0
    for img_file in list(images_dir.glob("*.jpeg")) + list(images_dir.glob("*.jpg")):
        target_png = img_file.with_suffix(".png")
        if not target_png.exists():
            try:
                with Image.open(img_file) as pil_img:
                    if pil_img.mode in ("CMYK", "P"):
                        pil_img = pil_img.convert("RGB")
                    pil_img.save(target_png, format="PNG")
                converted_count += 1
            except Exception as e:
                print(f"  [!] Errore conversione {img_file.name}: {e}")
        else:
            already_png_count += 1

    return converted_count, already_png_count

def clean_page_content(page_text: str) -> str:
    """Rimuove wrapper di codice e footer di ateneo da un singolo blocco pagina."""
    lines = page_text.strip().splitlines()
    if not lines:
        return ""

    filtered_lines = []
    for line in lines:
        stripped = line.strip()
        if stripped in ("```markdown", "```md"):
            continue
        if any(pat.match(stripped) for pat in DEPT_PATTERNS):
            continue
        filtered_lines.append(line)

    if filtered_lines and filtered_lines[0].strip() == "```":
        filtered_lines = filtered_lines[1:]
    if filtered_lines and filtered_lines[-1].strip() == "```":
        filtered_lines = filtered_lines[:-1]

    cleaned = "\n".join(filtered_lines).strip()
    return cleaned

def fix_image_links(chunk: str, images_dir: Path) -> str:
    """Risolve o converte i link a figure inesistenti."""
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

def fix_markdown_file(md_path: Path):
    print(f"\nElaborazione file: {md_path}")
    images_dir = md_path.parent / "images"

    bak_path = md_path.with_suffix(".md.bak")
    if not bak_path.exists():
        shutil.copy2(md_path, bak_path)
        print(f"  [+] Creato backup: {bak_path.name}")

    content = md_path.read_text(encoding="utf-8", errors="ignore")

    page_chunks = re.split(r"(<!--\s*PAGE\s+\d+(?:\s+FAILED)?\s*-->)", content, flags=re.IGNORECASE)

    new_chunks = []
    for chunk in page_chunks:
        if re.match(r"^<!--\s*PAGE\s+\d+(?:\s+FAILED)?\s*-->$", chunk.strip(), flags=re.IGNORECASE):
            new_chunks.append(f"\n\n{chunk.strip()}\n\n")
        else:
            cleaned = clean_page_content(chunk)
            if cleaned:
                cleaned = re.sub(r"(\!\[.*?\]\(images/pag_\d+_fig_[a-zA-Z0-9_-]+)\.(?:jpeg|jpg)\)", r"\1.png)", cleaned)
                if images_dir.exists():
                    cleaned = fix_image_links(cleaned, images_dir)
                new_chunks.append(cleaned)

    final_text = "".join(new_chunks).strip() + "\n"
    md_path.write_text(final_text, encoding="utf-8")
    print(f"  ✓ Markdown ripulito e salvato ({len(final_text)} caratteri).")

def main():
    if not OUTPUT_DIR.exists():
        print(f"Cartella {OUTPUT_DIR} non trovata.")
        return

    print("=" * 60)
    print("Avvio fix cartella output_markdown/")
    print("=" * 60)

    for item in sorted(OUTPUT_DIR.iterdir()):
        if not item.is_dir():
            continue

        images_dir = item / "images"
        if images_dir.exists():
            converted, existing = convert_images_to_png(images_dir)
            print(f"Cartella '{item.name}': {converted} immagini convertite in PNG ({existing} già presenti).")

        for md_file in item.glob("*.md"):
            if md_file.name.endswith(".bak"):
                continue
            fix_markdown_file(md_file)

    print("\n" + "=" * 60)
    print("Operazione completata con successo.")
    print("=" * 60)

if __name__ == "__main__":
    main()
