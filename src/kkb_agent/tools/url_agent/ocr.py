"""Unlimited-OCR access, layout-tag handling and the image content handler."""

from __future__ import annotations

import base64
import hashlib
import re
from collections.abc import MutableMapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from kkb_agent.tools.url_agent.timeouts import Deadline

from kkb_agent.tools.url_agent.handlers import DEFAULT_LIMITS, ExtractionLimits, content_sha256
from kkb_agent.tools.url_agent.models import (
    DocumentKind,
    ExtractedPage,
    OCRBackendError,
    URLDocument,
)
from kkb_agent.tools.url_safety import UntrustedContent

# Unlimited-OCR accepts at most three images per prompt, and the model guide pairs a
# multi-image prompt with window_size 1024 and a single-image prompt with 128.
MAX_IMAGES_PER_CALL = 3
MULTI_IMAGE_WINDOW_SIZE = 1024
SINGLE_IMAGE_WINDOW_SIZE = 128
OCR_NGRAM_SIZE = 35
OCR_MAX_TOKENS = 8192
OCR_PROMPT = "<image>\ndocument parsing"

# <|ref|>label<|/ref|><|det|>[[x1, y1, x2, y2]]<|/det|> carries a layout box in an
# undocumented coordinate space. The label is kept; the box is dropped rather than
# republished as a position this tool cannot honestly interpret.
_REF_PATTERN = re.compile(r"<\|ref\|>(.*?)<\|/ref\|>", re.DOTALL)
_DET_PATTERN = re.compile(r"<\|det\|>.*?<\|/det\|>", re.DOTALL)
_RESIDUAL_TAG_PATTERN = re.compile(r"<\|/?[a-z_]+\|>")


class OCRBackend(Protocol):
    """Reads rendered images and returns exactly one text block per supplied image."""

    def read_page_images(self, images: Sequence[bytes]) -> tuple[str, ...]: ...


def strip_layout_tags(text: str) -> str:
    """Keep OCR reference labels, drop coordinate boxes and any residual control tags."""

    without_boxes = _DET_PATTERN.sub("", text)
    with_labels = _REF_PATTERN.sub(lambda match: match.group(1), without_boxes)
    return _RESIDUAL_TAG_PATTERN.sub("", with_labels).strip()


@dataclass
class MIAOCRBackend:
    """Call Unlimited-OCR through MIA with the parameters the model guide requires.

    One image is sent per call. The model returns a single text block per prompt, so a
    multi-image prompt would merge several pages into one block with no way to say which
    page a number came from. Page attribution is worth more than the saved calls.
    """

    mia_client: object
    max_tokens: int = OCR_MAX_TOKENS

    def read_page_images(self, images: Sequence[bytes]) -> tuple[str, ...]:
        if len(images) > MAX_IMAGES_PER_CALL:
            raise OCRBackendError(
                f"Unlimited-OCR accepts at most {MAX_IMAGES_PER_CALL} images per call"
            )
        return tuple(self._read_one(image) for image in images)

    def _read_one(self, image: bytes) -> str:
        encoded = base64.b64encode(image).decode("ascii")
        client = self.mia_client.get_client()
        try:
            response = client.chat.completions.create(
                model=self.mia_client.ocr_model,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image_url",
                                "image_url": {"url": f"data:image/png;base64,{encoded}"},
                            },
                            {"type": "text", "text": OCR_PROMPT},
                        ],
                    }
                ],
                max_tokens=self.max_tokens,
                temperature=0.0,
                extra_body={
                    "skip_special_tokens": False,
                    "vllm_xargs": {
                        "ngram_size": OCR_NGRAM_SIZE,
                        "window_size": SINGLE_IMAGE_WINDOW_SIZE,
                    },
                },
            )
        except Exception as exc:
            raise OCRBackendError("Unlimited-OCR request failed") from exc

        message = response.choices[0].message.content if response.choices else None
        if not message:
            raise OCRBackendError("Unlimited-OCR returned an empty response")
        return message


@dataclass
class OCRCache:
    """Caches OCR text by the hash of the exact image that produced it."""

    entries: MutableMapping[str, str] = field(default_factory=dict)

    def get(self, image_sha256: str) -> str | None:
        return self.entries.get(image_sha256)

    def set(self, image_sha256: str, text: str) -> None:
        self.entries[image_sha256] = text


def ocr_images(
    images: Sequence[bytes],
    ocr: OCRBackend,
    cache: OCRCache | None = None,
    *,
    deadline: Deadline | None = None,
) -> tuple[str, ...]:
    """Read every image, reusing cached text and batching within the model's image limit.

    With a `deadline`, the budget is checked before each call. Running out raises
    `ToolTimeoutError` carrying the pages already read on `partial`, so a caller can show
    an incomplete document and say so rather than discarding the work.
    """

    cache = cache if cache is not None else OCRCache()
    hashes = [hashlib.sha256(image).hexdigest() for image in images]
    results: list[str | None] = [cache.get(digest) for digest in hashes]

    pending = [index for index, value in enumerate(results) if value is None]
    for start in range(0, len(pending), MAX_IMAGES_PER_CALL):
        batch = pending[start : start + MAX_IMAGES_PER_CALL]
        if deadline is not None:
            deadline.require(
                f"OCR of image {batch[0] + 1} of {len(images)}",
                partial=tuple(value or "" for value in results),
            )
        returned = ocr.read_page_images([images[index] for index in batch])
        if len(returned) != len(batch):
            raise OCRBackendError(
                f"OCR backend returned {len(returned)} block(s) for {len(batch)} image(s)"
            )
        for index, raw in zip(batch, returned, strict=True):
            text = strip_layout_tags(raw)
            results[index] = text
            cache.set(hashes[index], text)

    return tuple(value or "" for value in results)


def extract_image(
    content: UntrustedContent,
    *,
    ocr: OCRBackend,
    cache: OCRCache | None = None,
    limits: ExtractionLimits = DEFAULT_LIMITS,
) -> URLDocument:
    """Read a single image document with OCR."""

    text = ocr_images([content.body], ocr, cache)[0]
    notes: tuple[str, ...] = ("Text was read from an image by OCR and may contain reading errors.",)
    if len(text) > limits.max_text_chars:
        text = text[: limits.max_text_chars]
        notes += (f"Text truncated to the {limits.max_text_chars} character limit.",)
    return URLDocument(
        requested_url=content.requested_url,
        final_url=content.final_url,
        content_type=content.content_type,
        kind=DocumentKind.IMAGE,
        extraction_method="unlimited-ocr",
        text=text,
        pages=(ExtractedPage(page_number=1, text=text, extraction_method="unlimited-ocr"),),
        page_count=1,
        byte_count=len(content.body),
        content_sha256=content_sha256(content.body),
        notes=notes,
    )
