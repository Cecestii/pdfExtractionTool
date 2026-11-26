from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# Mock per dipendenze esterne pesanti se il test gira fuori da venv
for mod in ["openai", "fitz", "PIL", "PIL.Image", "tqdm"]:
    if mod not in sys.modules:
        try:
            __import__(mod)
        except ImportError:
            sys.modules[mod] = MagicMock()

import pipeline as pl
from pipeline import (
    ConversionConfig,
    ConversionSession,
    SYSTEM_PROMPT,
    clean_markdown_fences,
    consolidate_markdown_pages,
    convert_to_obsidian_callouts,
    fix_image_links,
    get_processed_pages,
    is_transient_error,
    parse_arguments,
    process_page,
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

    def test_convert_to_obsidian_callouts_bilingual(self):
        text = (
            "> **Dettagli tecnici figura:** Schema cinematologico a 4 barre.\n"
            "> **Technical figure details:** 4-bar linkage diagram.\n"
            "> **Schema grafico:** Rappresentazione delle forze normali.\n"
            "> **Diagram:** Force equilibrium layout.\n"
            "> **Nota:** Ricordare di verificare il coefficiente di sicurezza.\n"
            "> **Note:** Check safety factor."
        )
        converted = convert_to_obsidian_callouts(text)
        self.assertEqual(converted.count("> [!info] Dettagli tecnici figura"), 2)
        self.assertEqual(converted.count("> [!example] Schema grafico"), 2)
        self.assertEqual(converted.count("> [!note] Nota"), 2)

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

    def test_consolidate_markdown_pages_reorders_and_clears_recovered_failed(self):
        md_file = self.test_dir / "resumed_estratto.md"
        content = (
            "<!-- PAGE 1 -->\n\nTesto 1\n\n"
            "<!-- PAGE 2 FAILED -->\n\n> Errore temporaneo\n\n"
            "<!-- PAGE 3 -->\n\nTesto 3\n\n"
            "<!-- PAGE 2 -->\n\nTesto 2 recuperato\n"
        )
        md_file.write_text(content, encoding="utf-8")

        consolidate_markdown_pages(md_file)
        result = md_file.read_text(encoding="utf-8")

        self.assertNotIn("FAILED", result)
        idx1 = result.index("<!-- PAGE 1 -->")
        idx2 = result.index("<!-- PAGE 2 -->")
        idx3 = result.index("<!-- PAGE 3 -->")
        self.assertTrue(idx1 < idx2 < idx3)
        self.assertIn("Testo 2 recuperato", result)

    def test_fix_image_links_fallback_and_path_traversal_protection(self):
        images_dir = self.test_dir / "images"
        images_dir.mkdir(parents=True, exist_ok=True)
        secret_file = self.test_dir.parent / "secret.png"
        secret_file.write_bytes(b"secret")
        try:
            chunk = (
                "Ok: ![Diagramma](images/pag_5_fig_1.png) "
                "Traversal: ![Leak](images/../../secret.png)"
            )
            fixed = fix_image_links(chunk, images_dir)
            self.assertIn("*Diagramma*", fixed)
            self.assertIn("*Leak*", fixed)
            self.assertNotIn("secret.png", fixed)
        finally:
            secret_file.unlink(missing_ok=True)

    def test_conversion_session_isolation_and_persistence(self):
        cache_file = self.test_dir / ".seen_hashes"
        s1 = ConversionSession(hash_cache_file=cache_file)
        s2 = ConversionSession()  # sessione isolata in memoria

        self.assertTrue(s1.register_if_new(b"logo_bytes"))
        self.assertFalse(s1.register_if_new(b"logo_bytes"))
        # s2 non condivide lo stato con s1
        self.assertTrue(s2.register_if_new(b"logo_bytes"))

        # s3 ricarica dalla cache persistente di s1
        s3 = ConversionSession(hash_cache_file=cache_file)
        self.assertFalse(s3.register_if_new(b"logo_bytes"))

        # reset_cache=True azzera la cache
        s4 = ConversionSession(hash_cache_file=cache_file, reset_cache=True)
        self.assertTrue(s4.register_if_new(b"logo_bytes"))

    def test_is_transient_error_classification(self):
        class FakeTimeoutError(Exception):
            pass

        class FakeHTTPError(Exception):
            def __init__(self, status_code: int):
                self.status_code = status_code

        self.assertTrue(is_transient_error(FakeTimeoutError("timed out")))
        self.assertTrue(is_transient_error(FakeHTTPError(503)))
        self.assertTrue(is_transient_error(FakeHTTPError(429)))
        self.assertFalse(is_transient_error(FakeHTTPError(400)))
        self.assertFalse(is_transient_error(ValueError("invalid argument")))

    def test_process_page_selective_retry_kv_cache_and_truncation_warning(self):
        fake_doc = MagicMock()
        fake_page = MagicMock()
        fake_page.rect.width = 960.0
        fake_page.rect.height = 540.0
        fake_page.get_images.return_value = []
        fake_page.get_drawings.return_value = []
        fake_page.get_text.return_value = "University of Brescia\nEquilibrio statico\n"
        fake_pix = MagicMock()
        fake_pix.tobytes.return_value = b"fake_png_bytes"
        fake_page.get_pixmap.return_value = fake_pix
        fake_doc.load_page.return_value = fake_page

        class TransientConnError(Exception):
            status_code = 503

        fake_choice = MagicMock()
        fake_choice.message.content = "# Equilibrio statico"
        fake_choice.finish_reason = "length"
        fake_response = MagicMock()
        fake_response.choices = [fake_choice]

        fake_client = MagicMock()
        fake_client.chat.completions.create.side_effect = [
            TransientConnError("temporary overload"),
            fake_response,
        ]

        cfg = ConversionConfig(retries=2, max_tokens=2000, output_format="gfm")
        with patch.object(pl, "fitz") as mock_fitz, patch("pipeline.time.sleep") as mock_sleep:
            mock_fitz.Matrix.return_value = "mat"
            result = process_page(fake_doc, 0, self.test_dir, fake_client, cfg)

        self.assertEqual(fake_client.chat.completions.create.call_count, 2)
        mock_sleep.assert_called_once()
        # Verifica che SYSTEM_PROMPT resti invariato per il KV Prefix Caching
        sent_messages = fake_client.chat.completions.create.call_args.kwargs["messages"]
        self.assertEqual(sent_messages[0]["content"], SYSTEM_PROMPT)
        # Verifica warning di troncamento
        self.assertIn("OUTPUT TRUNCATED", result)

    def test_parse_arguments_defaults_and_custom_flags(self):
        _, args = parse_arguments([
            "slide.pdf", "2", "10",
            "--output-dir", "/tmp/out",
            "--max-tokens", "3000",
            "--format", "obsidian",
            "--api-key", "sk-test",
        ])
        self.assertEqual(args.pdf, "slide.pdf")
        self.assertEqual(args.start, 2)
        self.assertEqual(args.end, 10)
        self.assertEqual(args.output_dir, "/tmp/out")
        self.assertEqual(args.max_tokens, 3000)
        self.assertEqual(args.format, "obsidian")
        self.assertEqual(args.api_key, "sk-test")


if __name__ == "__main__":
    unittest.main()
