"""Find the document a landing page links to, then read it."""

from __future__ import annotations

import re
import unicodedata
from typing import Protocol

from pydantic import Field

from kkb_agent.catalog.identity import turkish_casefold
from kkb_agent.frame._base import Contract
from kkb_agent.tools.url_agent.models import (
    DocumentKind,
    DocumentNotFoundError,
    ExtractedLink,
    HopLimitError,
    URLDocument,
)
from kkb_agent.tools.url_agent.router import ContentTypeRouter
from kkb_agent.tools.url_safety import UntrustedContent

DEFAULT_MAX_HOPS = 2
DEFAULT_ACCEPTED_KINDS = frozenset(
    {DocumentKind.PDF, DocumentKind.EXCEL, DocumentKind.IMAGE, DocumentKind.TEXT}
)

# Weights are fixed so a choice is reproducible and can be argued with afterwards.
LINK_TEXT_TERM_WEIGHT = 2.0
URL_TERM_WEIGHT = 1.0
DOCUMENT_SUFFIX_WEIGHT = 1.0

_DOCUMENT_SUFFIXES = (".pdf", ".xlsx", ".xls", ".csv", ".txt")
_TERM_PATTERN = re.compile(r"[^\W_]+", re.UNICODE)


class Fetcher(Protocol):
    """Anything that can turn a URL into untrusted bytes, i.e. `SafeURLFetcher`."""

    def fetch(self, url: str) -> UntrustedContent: ...


class DiscoveryHop(Contract):
    """One page visited on the way to the document, and the link taken from it."""

    url: str
    kind: DocumentKind
    followed_url: str | None = None
    followed_text: str | None = None
    score: float | None = None
    reason: str


class DiscoveredDocument(Contract):
    """The document that was reached, with the full trail that led to it."""

    query: str
    document: URLDocument
    hops: tuple[DiscoveryHop, ...] = Field(min_length=1)


def terms(text: str) -> tuple[str, ...]:
    """Split text into casefolded word terms, correctly for Turkish."""

    return tuple(_TERM_PATTERN.findall(turkish_casefold(text)))


def fold_ascii(term: str) -> str:
    """Reduce a Turkish term to its unaccented ASCII skeleton.

    Publishers write the same word as "Altın", "ALTIN" and "altin", and a URL slug is
    almost always the ASCII form. Strict Turkish casefolding is still applied first --
    this only decides whether two already-casefolded terms are the same word.
    """

    lowered = term.replace("ı", "i").replace("İ", "i")
    decomposed = unicodedata.normalize("NFKD", lowered)
    return "".join(char for char in decomposed if not unicodedata.combining(char))


def _matches(query_term: str, candidates: set[str], folded_candidates: set[str]) -> bool:
    return query_term in candidates or fold_ascii(query_term) in folded_candidates


def score_link(link: ExtractedLink, query_terms: tuple[str, ...]) -> tuple[float, str]:
    """Score one candidate link and state, in words, exactly why it scored that way."""

    text_terms = set(terms(link.text))
    url_terms = set(terms(link.url))
    folded_text = {fold_ascii(term) for term in text_terms}
    folded_url = {fold_ascii(term) for term in url_terms}

    matched_in_text = sorted(
        term for term in query_terms if _matches(term, text_terms, folded_text)
    )
    matched_in_url = sorted(
        term
        for term in query_terms
        if _matches(term, url_terms, folded_url) and term not in matched_in_text
    )
    suffix = next(
        (item for item in _DOCUMENT_SUFFIXES if link.url.lower().split("?")[0].endswith(item)),
        None,
    )

    # A document suffix only reinforces a link the request already matched. Without this
    # guard every .pdf on the page scores above zero, and a request for one metal happily
    # follows the link for another.
    matched_anything = bool(matched_in_text or matched_in_url)
    score = (
        LINK_TEXT_TERM_WEIGHT * len(matched_in_text)
        + URL_TERM_WEIGHT * len(matched_in_url)
        + (DOCUMENT_SUFFIX_WEIGHT if suffix and matched_anything else 0.0)
    )

    parts = []
    if matched_in_text:
        parts.append(f"link text matched {matched_in_text}")
    if matched_in_url:
        parts.append(f"URL matched {matched_in_url}")
    if suffix and matched_anything:
        parts.append(f"URL ends in {suffix}")
    reason = "; ".join(parts) if parts else "nothing in the request matched this link"
    return score, reason


def discover_document(
    start_url: str,
    *,
    query: str,
    fetcher: Fetcher,
    router: ContentTypeRouter,
    max_hops: int = DEFAULT_MAX_HOPS,
    accepted_kinds: frozenset[DocumentKind] = DEFAULT_ACCEPTED_KINDS,
) -> DiscoveredDocument:
    """Walk from a landing page to the document it links, at most `max_hops` times.

    The search is deterministic: links are scored by term overlap with the request, ties
    break towards the link that appears first in the page, and every step records which
    link it took and why. No model is consulted.
    """

    if type(max_hops) is not int or max_hops < 0:
        raise ValueError("max_hops must be a non-negative integer")

    query_terms = terms(query)
    hops: list[DiscoveryHop] = []
    current_url = start_url
    visited = {current_url}

    for hop_index in range(max_hops + 1):
        document = router.route(fetcher.fetch(current_url))

        if document.kind in accepted_kinds:
            hops.append(
                DiscoveryHop(
                    url=document.final_url,
                    kind=document.kind,
                    reason=f"{document.kind.value} document accepted as the requested target",
                )
            )
            return DiscoveredDocument(query=query, document=document, hops=tuple(hops))

        if hop_index == max_hops:
            hops.append(
                DiscoveryHop(
                    url=document.final_url,
                    kind=document.kind,
                    reason=f"hop limit of {max_hops} reached before a document was found",
                )
            )
            raise HopLimitError(
                f"{start_url!r} did not lead to a document within {max_hops} hop(s); "
                f"stopped at {document.final_url!r}"
            )

        candidates = [
            (score, reason, link)
            for link in document.links
            if link.url not in visited
            for score, reason in [score_link(link, query_terms)]
            if score > 0
        ]
        if not candidates:
            hops.append(
                DiscoveryHop(
                    url=document.final_url,
                    kind=document.kind,
                    reason=(
                        f"none of the {len(document.links)} link(s) on this page matched {query!r}"
                    ),
                )
            )
            raise DocumentNotFoundError(
                f"No link on {document.final_url!r} matched {query!r}; "
                f"{len(document.links)} link(s) were considered"
            )

        # max() keeps the first of equal scores, so page order breaks ties.
        best_score, best_reason, best_link = max(candidates, key=lambda item: item[0])
        hops.append(
            DiscoveryHop(
                url=document.final_url,
                kind=document.kind,
                followed_url=best_link.url,
                followed_text=best_link.text or None,
                score=best_score,
                reason=best_reason,
            )
        )
        visited.add(best_link.url)
        current_url = best_link.url

    raise HopLimitError(  # pragma: no cover - the loop returns or raises before this
        f"{start_url!r} did not lead to a document within {max_hops} hop(s)"
    )
