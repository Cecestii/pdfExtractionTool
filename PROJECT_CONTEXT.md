# Contesto e Registro di Progetto: Converter

## 📌 Scopo del Progetto
Pipeline autonoma di conversione ad alta fedeltà da PDF (dispense universitarie, slide tecniche) a GitHub-Flavored Markdown con:
- Formule matematiche LaTeX inline ($...$) e display ($$...$$).
- Tabelle ricostruite in formato Markdown GFM.
- Estrazione automatica e deduplica SHA-256 delle immagini e figure raster in PNG.
- Fallback di ritaglio vettoriale per diagrammi e schemi disegnati su slide.
- Integrazione con modelli VLM locali (Qwen 3-VL / Qwen 2.5-VL) su LM Studio.

## 🛠️ Stack Tecnologico
- **Python**: 3.10+
- **PDF Engine**: PyMuPDF (`fitz`)
- **VLM Client**: `openai` SDK puntato a `http://localhost:1234/v1`
- **Image Processing**: Pillow (`PIL`)
- **CLI & Monitoring**: `tqdm`, `argparse`

## 📋 Convenzioni di Output
- I file elaborati vengono salvati in: `output_markdown/<nome_pdf>/`
- File principale: `<nome_pdf>_estratto.md`
- Immagini estratte: `output_markdown/<nome_pdf>/images/`
- Separatori di pagina: `<!-- PAGE X -->` per le pagine valide; `<!-- PAGE X FAILED -->` per eventuali errori da ritentare.
