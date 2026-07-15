"""
Tests for llampaca/attachments.py (the /attach extraction layer).

The PDF test uses a minimal hand-written PDF (one page, one line of text)
embedded as bytes: it keeps the test hermetic — no fixture files, no
dependency on a PDF-authoring library. The .docx test builds a real
document in a temp dir via python-docx, which is already a dependency.
"""

import tempfile
import unittest
from pathlib import Path

from llampaca.attachments import (
    ATTACH_MAX_CONTEXT_FRACTION,
    AttachmentError,
    attachment_token_budget,
    build_attachment_block,
    estimate_tokens,
    extract_text,
)

def _minimal_pdf() -> bytes:
    """
    Assemble a minimal single-page PDF containing "Hello Llampaca", with a
    byte-exact xref table and startxref (pypdf refuses files without one).
    Built programmatically so the offsets are always right, keeping the
    test hermetic — no fixture files, no PDF-authoring dependency.
    """
    stream = b"BT /F1 24 Tf 72 720 Td (Hello Llampaca) Tj ET"
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n%s\nendstream" % (len(stream), stream),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]

    out = bytearray(b"%PDF-1.4\n")
    offsets = []  # byte offset of each numbered object, for the xref table
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n%s\nendobj\n" % (number, body)

    xref_start = len(out)
    out += b"xref\n0 %d\n" % (len(objects) + 1)
    out += b"0000000000 65535 f \n"  # entry for the free object 0
    for offset in offsets:
        out += b"%010d 00000 n \n" % offset
    out += (
        b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n"
        % (len(objects) + 1, xref_start)
    )
    return bytes(out)


MINIMAL_PDF = _minimal_pdf()


class TestExtractText(unittest.TestCase):
    def setUp(self):
        # Fresh temp dir per test; TemporaryDirectory cleans up on close.
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_plain_text_roundtrip(self):
        p = self.tmp / "note.txt"
        p.write_text("hello attachment", encoding="utf-8")
        self.assertEqual(extract_text(p), "hello attachment")

    def test_markdown_is_treated_as_text(self):
        p = self.tmp / "doc.md"
        p.write_text("# Title\nbody", encoding="utf-8")
        self.assertIn("# Title", extract_text(p))

    def test_missing_file_raises(self):
        with self.assertRaises(AttachmentError):
            extract_text(self.tmp / "nope.txt")

    def test_directory_raises(self):
        with self.assertRaises(AttachmentError):
            extract_text(self.tmp)

    def test_unsupported_extension_raises_and_names_it(self):
        p = self.tmp / "image.png"
        p.write_bytes(b"\x89PNG fake")
        with self.assertRaises(AttachmentError) as ctx:
            extract_text(p)
        self.assertIn(".png", str(ctx.exception))

    def test_legacy_doc_gets_actionable_message(self):
        p = self.tmp / "old.doc"
        p.write_bytes(b"\xd0\xcf\x11\xe0 fake ole")
        with self.assertRaises(AttachmentError) as ctx:
            extract_text(p)
        self.assertIn(".docx", str(ctx.exception))  # tells the user the fix

    def test_empty_text_file_raises(self):
        p = self.tmp / "empty.txt"
        p.write_text("", encoding="utf-8")
        with self.assertRaises(AttachmentError):
            extract_text(p)

    def test_pdf_extraction(self):
        p = self.tmp / "mini.pdf"
        p.write_bytes(MINIMAL_PDF)
        text = extract_text(p)
        self.assertIn("Hello Llampaca", text)
        # Page markers keep model references ("on page N") meaningful.
        self.assertIn("--- Page 1 ---", text)

    def test_corrupt_pdf_raises(self):
        p = self.tmp / "broken.pdf"
        p.write_bytes(b"%PDF-1.4 this is not really a pdf")
        with self.assertRaises(AttachmentError):
            extract_text(p)

    def test_docx_extraction_paragraphs_and_tables(self):
        from docx import Document

        doc = Document()
        doc.add_paragraph("First paragraph.")
        table = doc.add_table(rows=1, cols=2)
        table.rows[0].cells[0].text = "cell A"
        table.rows[0].cells[1].text = "cell B"
        p = self.tmp / "doc.docx"
        doc.save(str(p))

        text = extract_text(p)
        self.assertIn("First paragraph.", text)
        # Tables are rendered as pipe-separated rows after the paragraphs.
        self.assertIn("cell A | cell B", text)

    def test_corrupt_docx_raises(self):
        p = self.tmp / "broken.docx"
        p.write_bytes(b"not a zip archive")
        with self.assertRaises(AttachmentError):
            extract_text(p)


class TestBudget(unittest.TestCase):
    def test_estimate_tokens_uses_chars_over_four(self):
        self.assertEqual(estimate_tokens("x" * 400), 100)

    def test_budget_is_fraction_of_context(self):
        self.assertEqual(
            attachment_token_budget(4096),
            int(4096 * ATTACH_MAX_CONTEXT_FRACTION),
        )

    def test_typical_budgets(self):
        # Regression guard on the numbers quoted in docs/UX: ~35% of -c.
        self.assertEqual(attachment_token_budget(4096), 1433)
        self.assertEqual(attachment_token_budget(8192), 2867)


class TestAttachmentBlock(unittest.TestCase):
    def test_block_has_begin_and_end_markers(self):
        block = build_attachment_block("report.pdf", "content here")
        # Both markers must name the file so the model can tell documents
        # apart when several are attached in one message.
        self.assertTrue(block.startswith("[Attached file: report.pdf"))
        self.assertTrue(block.endswith("\n[End of attached file: report.pdf]"))
        self.assertIn("content here", block)
        # The inline-content hint keeps small models from hunting for the
        # file with read_file/search_text instead of reading the block.
        self.assertIn("do NOT use tools", block)


if __name__ == "__main__":
    unittest.main()
