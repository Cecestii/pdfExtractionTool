from __future__ import annotations

import sys
import unittest
from pathlib import Path
import tempfile
import shutil
from unittest.mock import MagicMock

# Mock per dipendenze esterne pesanti se il test gira fuori da venv
for mod in ["openai", "fitz", "PIL", "PIL.Image", "tqdm"]:
    if mod not in sys.modules:
        try:
            __import__(mod)
        except ImportError:
            sys.modules[mod] = MagicMock()

from pipeline import (
    clean_markdown_fences,
    convert_to_obsidian_callouts,
    get_processed_pages,
    fix_image_links,
)


class TestConverterPipeline(unittest.TestCase):
    def setUp(self):
        self.test_dir = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_clean_markdown_fences_strips_codeblocks_and_think_tags(self):
        sample = (
            "<think>Questo è un monologo interno del modello.</think>\n"
            "```markdown\n"
            "# Titolo Slide\n"
            "- Punto elenco con formula $\\sigma = E \\cdot \\varepsilon$\n"
            "```"
        )
        cleaned = clean_markdown_fences(sample)
        self.assertNotIn("<think>", cleaned)
        self.assertNotIn("```markdown", cleaned)
        self.assertIn("# Titolo Slide", cleaned)
        self.assertIn("$\\sigma = E \\cdot \\varepsilon$", cleaned)

    def test_convert_to_obsidian_callouts(self):
        text = (
            "> **Dettagli tecnici figura:** Schema cinematologico a 4 barre.\n"
            "> **Schema grafico:** Rappresentazione delle forze normali.\n"
            "> **Nota:** Ricordare di verificare il coefficiente di sicurezza."
        )
        converted = convert_to_obsidian_callouts(text)
        self.assertIn("> [!info] Dettagli tecnici figura", converted)
        self.assertIn("> [!example] Schema grafico", converted)
        self.assertIn("> [!note] Nota", converted)

    def test_get_processed_pages_includes_success_and_excludes_failed(self):
        md_file = self.test_dir / "sample_estratto.md"
        content = (
            "<!-- PAGE 1 -->\nContenuto pagina 1\n\n"
            "<!-- PAGE 2 FAILED -->\nErrore pagina 2\n\n"
            "<!-- PAGE 3 -->\nContenuto pagina 3\n"
        )
        md_file.write_text(content, encoding="utf-8")

        processed = get_processed_pages(md_file)
        self.assertIn(1, processed)
        self.assertNotIn(2, processed, "Le pagine marcate come FAILED devono poter essere ritentate")
        self.assertIn(3, processed)

    def test_fix_image_links_fallback_to_alt_text_if_missing(self):
        images_dir = self.test_dir / "images"
        images_dir.mkdir(parents=True, exist_ok=True)

        chunk = "Ecco il diagramma: ![Diagramma di trazione](images/pag_5_fig_1.png)"
        fixed = fix_image_links(chunk, images_dir)
        self.assertEqual(fixed, "Ecco il diagramma: *Diagramma di trazione*")


if __name__ == "__main__":
    unittest.main()
