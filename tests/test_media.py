"""OCR, formulas, voice helpers. The OCR tests run the real engine on images drawn in the test."""
import io
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import ocr, voice  # noqa: E402


def draw(lines, size=(1000, 300)):
    from PIL import Image, ImageDraw, ImageFont
    im = Image.new("RGB", size, "white")
    d = ImageDraw.Draw(im)
    f = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 32)
    for text, xy in lines:
        d.text(xy, text, fill="black", font=f)
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


class Layout(unittest.TestCase):
    def test_rows_and_table_cells(self):
        def it(t, x0, x1, cy):
            return {"text": t, "x0": x0, "x1": x1, "cy": cy, "h": 20}
        items = [it("Total", 10, 70, 100), it("$5", 400, 440, 101), it("Name", 10, 70, 50), it("Qty", 400, 440, 49)]
        self.assertEqual(ocr.lines_to_text(items), "Name | Qty\nTotal | $5")
        self.assertEqual(ocr.lines_to_text([]), "")

    def test_formula_detection(self):
        self.assertTrue(ocr.looks_math("E = mc^2"))
        self.assertTrue(ocr.looks_math("\u2211 x_i \u2264 10"))
        self.assertFalse(ocr.looks_math("Please send the report tomorrow"))
        self.assertEqual(ocr.find_formulas("Hello\nA = pi r^2\nbye"), ["A = pi r^2"])

    def test_clean_latex(self):
        self.assertEqual(ocr.clean_latex("```latex\n$$E = mc^2$$\n```"), ["E = mc^2"])
        self.assertEqual(ocr.clean_latex("UNREADABLE"), [])
        self.assertEqual(ocr.clean_latex("\\[ x^2 \\]\n$y$"), ["x^2", "y"])


@unittest.skipUnless(ocr.available(), "rapidocr not installed")
class RealOcr(unittest.TestCase):
    def test_text_and_numbers(self):
        r = ocr.ocr_image_bytes(draw([("Total amount due: $1,459.90", (30, 40)), ("Invoice number 48213", (30, 120))]))
        self.assertIn("1,459.90", r["text"])
        self.assertIn("48213", r["text"].replace(" ", ""))
        self.assertGreater(r["confidence"], 0.8)
        self.assertTrue(all(len(l["box"]) == 4 for l in r["lines"]))

    def test_table(self):
        rows = [("Product", "Units"), ("Laptop", "120"), ("Phone", "340")]
        lines = [(c, (40 + ci * 400, 30 + ri * 70)) for ri, row in enumerate(rows) for ci, c in enumerate(row)]
        text = ocr.ocr_image_bytes(draw(lines, (900, 260)))["text"].splitlines()
        self.assertEqual([ln.replace(" ", "") for ln in text], ["Product|Units", "Laptop|120", "Phone|340"])

    def test_not_an_image(self):
        with self.assertRaises(ocr.OcrError):
            ocr.ocr_image_bytes(b"definitely not an image")

    def test_pdf_text_layer_vs_scan(self):
        import pymupdf
        tmp = Path(tempfile.mkdtemp())
        layer = pymupdf.open()
        layer.new_page().insert_text((72, 100), "This page has a real text layer and needs no OCR at all.")
        layer.save(str(tmp / "layer.pdf"))
        layer.close()
        scan = pymupdf.open()
        pg = scan.new_page(width=612, height=300)
        png = tmp / "p.png"
        png.write_bytes(draw([("Scanned page with no text layer", (30, 40))], (1000, 200)))
        pg.insert_image(pg.rect, filename=str(png))
        scan.save(str(tmp / "scan.pdf"))
        scan.close()
        a = ocr.ocr_pdf(str(tmp / "layer.pdf"))
        b = ocr.ocr_pdf(str(tmp / "scan.pdf"))
        self.assertEqual(a["pages"][0]["source"], "text-layer")
        self.assertEqual(b["pages"][0]["source"], "ocr")
        self.assertIn("Scanned", b["pages"][0]["text"])
        (tmp / "broken.pdf").write_bytes(b"this is not a pdf at all")
        with self.assertRaises(ocr.OcrError):
            ocr.ocr_pdf(str(tmp / "broken.pdf"))


class VoiceHelpers(unittest.TestCase):
    def test_filler_cleanup_is_conservative(self):
        self.assertEqual(voice.clean_text("um so uh we should, you know, ship it hmm on Friday"), "So we should ship it on Friday")
        self.assertEqual(voice.clean_text("Umbrella is under the hummus. Um, ok."), "Umbrella is under the hummus. Ok.")

    def test_validation_errors_are_user_facing(self):
        with self.assertRaises(voice.VoiceError):
            voice.transcribe("C:/definitely/missing.wav")
        with self.assertRaises(voice.VoiceError):
            voice.transcribe(__file__, size="gigantic")


if __name__ == "__main__":
    unittest.main()
