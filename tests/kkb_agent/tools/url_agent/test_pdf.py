import hashlib
import zlib
from io import BytesIO

import pytest

from kkb_agent.tools.url_agent import (
    ContentExtractionError,
    DocumentKind,
    MIAOCRBackend,
    OCRBackendError,
    OCRCache,
    PDFTextLayerMissingError,
    create_content_type_router,
    extract_pdf,
    ocr_images,
    strip_layout_tags,
)
from kkb_agent.tools.url_safety import UntrustedContent


def _minimal_pdf(page_texts: tuple[str, ...]) -> bytes:
    """Hand-build a PDF whose pages each show one line of Latin text."""
    objects: list[bytes] = []

    def add(body: bytes) -> int:
        objects.append(body)
        return len(objects)

    font_id = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    page_ids: list[int] = []
    content_ids: list[int] = []
    for text in page_texts:
        escaped = text.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
        stream = f"BT /F1 24 Tf 72 700 Td ({escaped}) Tj ET".encode("latin-1")
        content_ids.append(add(b"<< /Length %d >>\nstream\n%s\nendstream" % (len(stream), stream)))
    pages_id = len(objects) + len(page_texts) + 1
    for content_id in content_ids:
        page_ids.append(
            add(
                b"<< /Type /Page /Parent %d 0 R /MediaBox [0 0 612 792] "
                b"/Resources << /Font << /F1 %d 0 R >> >> /Contents %d 0 R >>"
                % (pages_id, font_id, content_id)
            )
        )
    kids = b" ".join(b"%d 0 R" % page_id for page_id in page_ids)
    pages_obj = add(b"<< /Type /Pages /Kids [%s] /Count %d >>" % (kids, len(page_ids)))
    catalog_id = add(b"<< /Type /Catalog /Pages %d 0 R >>" % pages_obj)

    out = BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = [0]
    for index, body in enumerate(objects, start=1):
        offsets.append(out.tell())
        out.write(b"%d 0 obj\n" % index)
        out.write(body)
        out.write(b"\nendobj\n")
    xref_offset = out.tell()
    out.write(b"xref\n0 %d\n" % (len(objects) + 1))
    out.write(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        out.write(b"%010d 00000 n \n" % offset)
    out.write(b"trailer\n<< /Size %d /Root %d 0 R >>\n" % (len(objects) + 1, catalog_id))
    out.write(b"startxref\n%d\n%%%%EOF\n" % xref_offset)
    return out.getvalue()


def _image_only_pdf() -> bytes:
    """A one-page PDF whose only content is an image: no text layer at all."""
    raw = bytes([255, 255, 255] * 4)
    compressed = zlib.compress(raw)
    objects: list[bytes] = []

    def add(body: bytes) -> int:
        objects.append(body)
        return len(objects)

    image_id = add(
        b"<< /Type /XObject /Subtype /Image /Width 2 /Height 2 /ColorSpace /DeviceRGB "
        b"/BitsPerComponent 8 /Filter /FlateDecode /Length %d >>\nstream\n%s\nendstream"
        % (len(compressed), compressed)
    )
    stream = b"q 200 0 0 200 100 500 cm /Im0 Do Q"
    content_id = add(b"<< /Length %d >>\nstream\n%s\nendstream" % (len(stream), stream))
    pages_id = len(objects) + 2
    page_id = add(
        b"<< /Type /Page /Parent %d 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /XObject << /Im0 %d 0 R >> >> /Contents %d 0 R >>"
        % (pages_id, image_id, content_id)
    )
    pages_obj = add(b"<< /Type /Pages /Kids [%d 0 R] /Count 1 >>" % page_id)
    catalog_id = add(b"<< /Type /Catalog /Pages %d 0 R >>" % pages_obj)

    out = BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = [0]
    for index, body in enumerate(objects, start=1):
        offsets.append(out.tell())
        out.write(b"%d 0 obj\n" % index)
        out.write(body)
        out.write(b"\nendobj\n")
    xref_offset = out.tell()
    out.write(b"xref\n0 %d\n" % (len(objects) + 1))
    out.write(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        out.write(b"%010d 00000 n \n" % offset)
    out.write(b"trailer\n<< /Size %d /Root %d 0 R >>\n" % (len(objects) + 1, catalog_id))
    out.write(b"startxref\n%d\n%%%%EOF\n" % xref_offset)
    return out.getvalue()


def content(body: bytes, content_type: str | None = "application/pdf") -> UntrustedContent:
    return UntrustedContent(
        requested_url="https://example.org/dosyalar/kmp_au.pdf",
        final_url="https://example.org/dosyalar/kmp_au.pdf",
        content_type=content_type,
        body=body,
        redirect_count=0,
    )


class RecordingOCR:
    """Fake OCR backend: returns canned text and records every call it received."""

    def __init__(self, texts: tuple[str, ...] = ("OCR page",)):
        self.texts = texts
        self.calls: list[int] = []
        self.images_seen = 0

    def read_page_images(self, images):
        self.calls.append(len(images))
        self.images_seen += len(images)
        return tuple(self.texts[index % len(self.texts)] for index in range(len(images)))


def test_text_layer_is_read_by_pypdf_without_any_ocr_call():
    ocr = RecordingOCR()
    document = extract_pdf(content(_minimal_pdf(("Altin 4.512,30", "Gumus 52,10"))), ocr=ocr)

    assert document.kind is DocumentKind.PDF
    assert document.extraction_method == "pypdf"
    assert document.page_count == 2
    assert "4.512,30" in document.pages[0].text
    assert "52,10" in document.pages[1].text
    assert document.pages[0].page_number == 1
    assert document.pages[1].page_number == 2
    assert all(page.extraction_method == "pypdf" for page in document.pages)
    assert ocr.calls == []  # OCR is the fallback, never the default


def test_pdf_without_a_text_layer_and_without_ocr_fails_honestly():
    with pytest.raises(PDFTextLayerMissingError, match="no text layer"):
        extract_pdf(content(_image_only_pdf()))


def test_pdf_without_a_text_layer_falls_back_to_ocr_per_page():
    ocr = RecordingOCR(texts=("Taranmis sayfa",))
    document = extract_pdf(content(_image_only_pdf()), ocr=ocr)

    assert document.extraction_method == "unlimited-ocr"
    assert document.pages[0].text == "Taranmis sayfa"
    assert document.pages[0].extraction_method == "unlimited-ocr"
    assert ocr.images_seen == 1
    assert any("OCR" in note for note in document.notes)


def test_ocr_results_are_cached_by_image_hash():
    ocr = RecordingOCR()
    cache = OCRCache()

    first = extract_pdf(content(_image_only_pdf()), ocr=ocr, cache=cache)
    second = extract_pdf(content(_image_only_pdf()), ocr=ocr, cache=cache)

    assert first.text == second.text
    assert ocr.images_seen == 1  # the identical page was not sent twice


def test_ocr_batches_never_exceed_the_three_image_limit():
    ocr = RecordingOCR()
    ocr_images([b"a", b"b", b"c", b"d", b"e"], ocr)

    assert ocr.calls == [3, 2]
    assert max(ocr.calls) <= 3


def test_ocr_backend_returning_the_wrong_block_count_is_rejected():
    class Mismatched:
        def read_page_images(self, images):
            return ("only one",)

    with pytest.raises(OCRBackendError, match="block"):
        ocr_images([b"a", b"b"], Mismatched())


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("<|ref|>Altin<|/ref|><|det|>[[1,2,3,4]]<|/det|> 4.512,30", "Altin 4.512,30"),
        ("<|det|>[[0,0,1,1]]<|/det|>sade metin", "sade metin"),
        ("<|im_start|>metin<|im_end|>", "metin"),
        ("hic etiket yok", "hic etiket yok"),
    ],
)
def test_layout_tags_are_stripped_and_labels_kept(raw, expected):
    assert strip_layout_tags(raw) == expected


def test_rendered_pages_are_structurally_valid_png():
    import struct

    import numpy as np

    from kkb_agent.tools.url_agent.pdf import encode_png

    pixels = np.zeros((3, 4, 3), dtype=np.uint8)
    pixels[1, 2] = (255, 128, 0)
    png = encode_png(pixels)

    assert png.startswith(b"\x89PNG\r\n\x1a\n")
    assert png.endswith(b"IEND\xaeB`\x82")
    length, tag = struct.unpack(">I4s", png[8:16])
    assert tag == b"IHDR" and length == 13
    width, height, depth, color_type = struct.unpack(">IIBB", png[16:26])
    assert (width, height, depth, color_type) == (4, 3, 8, 2)

    # Every chunk's CRC must check out, and the pixel stream must round-trip.
    idat = png[png.index(b"IDAT") + 4 : png.index(b"IEND") - 8]
    raw = zlib.decompress(idat)
    assert len(raw) == height * (1 + width * 3)
    assert raw[0] == 0  # filter type None on the first scanline
    assert raw[1 + (1 + 4 * 3) + 2 * 3 : 1 + (1 + 4 * 3) + 2 * 3 + 3] == b"\xff\x80\x00"


def test_png_encoder_rejects_arrays_it_cannot_describe():
    import numpy as np

    from kkb_agent.tools.url_agent.pdf import encode_png

    with pytest.raises(ContentExtractionError, match="8-bit image array"):
        encode_png(np.zeros((2, 2), dtype=np.uint8))
    with pytest.raises(ContentExtractionError, match="channels"):
        encode_png(np.zeros((2, 2, 5), dtype=np.uint8))


def test_malformed_pdf_is_rejected_with_a_clear_reason():
    with pytest.raises(ContentExtractionError, match="malformed"):
        extract_pdf(content(b"%PDF-1.4\nnot really a pdf"))


def test_router_reads_a_text_layer_pdf_without_an_ocr_backend():
    body = _minimal_pdf(("Rapor 2026",))
    document = create_content_type_router().route(content(body))

    assert document.kind is DocumentKind.PDF
    assert "Rapor 2026" in document.text
    assert document.content_sha256 == hashlib.sha256(body).hexdigest()


def test_router_registers_image_extraction_only_when_ocr_is_supplied():
    without_ocr = create_content_type_router()
    with_ocr = create_content_type_router(ocr=RecordingOCR())

    assert DocumentKind.IMAGE not in without_ocr.supported_kinds
    assert DocumentKind.IMAGE in with_ocr.supported_kinds


def test_image_documents_are_read_by_ocr_and_marked_as_such():
    ocr = RecordingOCR(texts=("Resimden okunan",))
    png = UntrustedContent(
        requested_url="https://example.org/tablo.png",
        final_url="https://example.org/tablo.png",
        content_type="image/png",
        body=b"\x89PNG\r\n\x1a\npayload",
        redirect_count=0,
    )

    document = create_content_type_router(ocr=ocr).route(png)

    assert document.kind is DocumentKind.IMAGE
    assert document.text == "Resimden okunan"
    assert document.pages[0].page_number == 1
    assert any("OCR" in note for note in document.notes)


def test_mia_backend_sends_one_image_per_call_with_documented_parameters():
    captured: list[dict] = []

    class FakeCompletions:
        def create(self, **kwargs):
            captured.append(kwargs)

            class Message:
                content = "<|ref|>Altin<|/ref|> 4.512,30"

            class Choice:
                message = Message()

            class Response:
                choices = [Choice()]

            return Response()

    class FakeClient:
        chat = type("Chat", (), {"completions": FakeCompletions()})()

    class FakeMIA:
        ocr_model = "kkbhackathon2026/Unlimited-OCR"

        def get_client(self):
            return FakeClient()

    backend = MIAOCRBackend(FakeMIA())
    assert backend.read_page_images([b"one", b"two"]) == (
        "<|ref|>Altin<|/ref|> 4.512,30",
        "<|ref|>Altin<|/ref|> 4.512,30",
    )
    assert len(captured) == 2  # one call per image keeps page attribution exact
    for call in captured:
        assert call["model"] == "kkbhackathon2026/Unlimited-OCR"
        assert call["temperature"] == 0.0
        assert call["extra_body"]["skip_special_tokens"] is False
        assert call["extra_body"]["vllm_xargs"]["window_size"] == 128
        assert call["extra_body"]["vllm_xargs"]["ngram_size"] == 35
        assert call["messages"][0]["content"][-1]["text"].startswith("<image>")


def test_mia_backend_refuses_more_images_than_the_model_accepts():
    class FakeMIA:
        ocr_model = "x"

        def get_client(self):  # pragma: no cover - never reached
            raise AssertionError("must not be called")

    with pytest.raises(OCRBackendError, match="at most 3 images"):
        MIAOCRBackend(FakeMIA()).read_page_images([b"a", b"b", b"c", b"d"])
