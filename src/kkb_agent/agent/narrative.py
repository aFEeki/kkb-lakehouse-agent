"""Evidence-bounded Turkish narrative selection and deterministic rendering."""

import json
from collections.abc import Iterable

from pydantic import BaseModel, ConfigDict, Field, model_validator

from kkb_agent.agent.planner import MAX_ERROR_CONTEXT_CHARS, MAX_TOKENS, NO_THINK
from kkb_agent.frame import AnalysisFrame, Finding
from kkb_agent.llm.client import MIAClient


class NarrativeContract(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ComputedToolResult(NarrativeContract):
    """Already-computed tool evidence supplied to the narrative boundary."""

    result_id: str = Field(min_length=1)
    tool_name: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    displayed_values: tuple[str, ...] = ()


class NarrativeCitation(NarrativeContract):
    claim_index: int = Field(ge=0)
    tool_names: tuple[str, ...] = Field(min_length=1)
    tool_result_ids: tuple[str, ...] = ()
    finding_ids: tuple[str, ...] = ()


class NarrativeResult(NarrativeContract):
    answer: str = Field(min_length=1)
    claims: tuple[str, ...] = ()
    citations: tuple[NarrativeCitation, ...] = ()
    surfaced_caveats: tuple[str, ...] = ()
    surfaced_finding_ids: tuple[str, ...] = ()
    insufficient_evidence: bool = False


class NarrativeError(RuntimeError):
    """Base error for narrative generation failures."""


class NarrativeModelError(NarrativeError):
    """The MIA call failed before evidence selection could be validated."""


class NarrativeValidationError(NarrativeError):
    """MIA failed to select a valid evidence-bounded narrative."""


class _ClaimSelection(NarrativeContract):
    text: str = Field(min_length=1)
    tool_result_ids: tuple[str, ...] = ()
    finding_ids: tuple[str, ...] = ()

    @model_validator(mode="after")
    def require_evidence_reference(self):
        if not self.tool_result_ids and not self.finding_ids:
            raise ValueError("Every narrative claim requires an evidence reference")
        return self


class _NarrativeSelection(NarrativeContract):
    claims: tuple[_ClaimSelection, ...] = ()
    insufficient_evidence: bool

    @model_validator(mode="after")
    def consistent_insufficiency(self):
        if self.insufficient_evidence and self.claims:
            raise ValueError("An insufficient-evidence response cannot contain claims")
        if not self.insufficient_evidence and not self.claims:
            raise ValueError("A supported response must contain at least one claim")
        return self


class NarrativeGenerator:
    """Select provided evidence with MIA, then render a cited Turkish answer."""

    def __init__(self, mia_client: MIAClient):
        self._mia_client = mia_client

    def generate(
        self,
        *,
        user_question: str,
        findings: Iterable[Finding],
        tool_results: Iterable[ComputedToolResult],
        frame_context: AnalysisFrame,
    ) -> NarrativeResult:
        if not user_question.strip():
            raise ValueError("user_question must not be empty")
        if not isinstance(frame_context, AnalysisFrame):
            raise TypeError("frame_context must be an AnalysisFrame")

        supplied_findings = tuple(findings)
        supplied_results = tuple(tool_results)
        effective_findings = self._validate_inputs(
            frame_context, supplied_findings, supplied_results
        )
        caveats = tuple(
            dict.fromkeys(caveat for finding in effective_findings for caveat in finding.caveats)
        )

        if not effective_findings and not supplied_results:
            return NarrativeResult(
                answer="Bu soruyu yanıtlamak için yeterli hesaplanmış kanıt bulunmuyor.",
                surfaced_caveats=caveats,
                insufficient_evidence=True,
            )

        messages = self._messages(
            user_question, effective_findings, supplied_results, frame_context
        )
        failures: list[str] = []
        for attempt in range(2):
            content = self._complete(messages)
            try:
                selection = _NarrativeSelection.model_validate_json(content)
                return self._validate_and_render(
                    selection, effective_findings, supplied_results, caveats
                )
            except (ValueError, TypeError) as exc:
                context = str(exc)[:MAX_ERROR_CONTEXT_CHARS]
                failures.append(context)
                if attempt == 0:
                    messages = [
                        *messages,
                        {"role": "assistant", "content": content},
                        {
                            "role": "user",
                            "content": (
                                "Önceki seçim geçersizdi. Yalnızca verilen kanıtlardaki exact "
                                "statement/summary metinlerini ve geçerli kimlikleri kullan. "
                                "Doğrulama hatası:\n" + context
                            ),
                        },
                    ]
        raise NarrativeValidationError(
            "MIA iki kez geçersiz narrative evidence seçimi üretti: " + " | ".join(failures)
        )

    @staticmethod
    def _validate_inputs(
        frame: AnalysisFrame,
        findings: tuple[Finding, ...],
        results: tuple[ComputedToolResult, ...],
    ) -> tuple[Finding, ...]:
        finding_ids = [finding.finding_id for finding in findings]
        if len(finding_ids) != len(set(finding_ids)):
            raise ValueError("findings must have unique IDs")
        frame_findings = {finding.finding_id: finding for finding in frame.findings}
        for finding in findings:
            if frame_findings.get(finding.finding_id) != finding:
                raise ValueError(
                    f"Finding {finding.finding_id!r} is not present unchanged in frame_context"
                )
        result_ids = [result.result_id for result in results]
        if len(result_ids) != len(set(result_ids)):
            raise ValueError("tool_results must have unique result IDs")

        superseded = {finding.supersedes for finding in findings if finding.supersedes}
        return tuple(
            finding
            for finding in findings
            if finding.finding_id not in superseded and finding.status == "active"
        )

    def _complete(self, messages: list[dict[str, str]]) -> str:
        try:
            response = self._mia_client.get_client().chat.completions.create(
                model=self._mia_client.chat_model,
                messages=messages,
                max_tokens=MAX_TOKENS,
                extra_body=NO_THINK,
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": "narrative_evidence_selection",
                        "schema": _NarrativeSelection.model_json_schema(),
                        "strict": True,
                    },
                },
            )
            content = response.choices[0].message.content
        except Exception as exc:
            raise NarrativeModelError(f"MIA narrative call failed: {exc}") from exc
        return content if isinstance(content, str) else ""

    @staticmethod
    def _messages(
        question: str,
        findings: tuple[Finding, ...],
        results: tuple[ComputedToolResult, ...],
        frame: AnalysisFrame,
    ) -> list[dict[str, str]]:
        evidence = {
            "findings": [
                {
                    "finding_id": finding.finding_id,
                    "statement": finding.statement,
                    "producing_tool": finding.producing_tool,
                    "caveats": finding.caveats,
                }
                for finding in findings
            ],
            "tool_results": [result.model_dump() for result in results],
            "frame": {
                "frame_id": frame.frame_id,
                "version": frame.version,
                "columns": [{"key": column.key, "label": column.label} for column in frame.columns],
            },
        }
        return [
            {
                "role": "system",
                "content": (
                    "Select evidence for a final Turkish answer. Do not calculate, paraphrase, "
                    "invent facts or introduce numbers. Each claim text must exactly equal one "
                    "provided tool summary or active finding statement and cite its IDs. Include "
                    "every active finding, including refusals. Return only the strict JSON schema."
                ),
            },
            {
                "role": "user",
                "content": f"Soru:\n{question}\nKanıt:\n{json.dumps(evidence, ensure_ascii=False)}",
            },
        ]

    @staticmethod
    def _validate_and_render(
        selection: _NarrativeSelection,
        findings: tuple[Finding, ...],
        results: tuple[ComputedToolResult, ...],
        caveats: tuple[str, ...],
    ) -> NarrativeResult:
        result_by_id = {result.result_id: result for result in results}
        finding_by_id = {finding.finding_id: finding for finding in findings}
        seen_findings: set[str] = set()
        citations: list[NarrativeCitation] = []

        for index, claim in enumerate(selection.claims):
            unknown_results = set(claim.tool_result_ids) - set(result_by_id)
            unknown_findings = set(claim.finding_ids) - set(finding_by_id)
            if unknown_results or unknown_findings:
                raise ValueError(
                    f"Claim {index} cites unknown evidence: "
                    f"tool_results={sorted(unknown_results)!r}, "
                    f"findings={sorted(unknown_findings)!r}"
                )
            evidence_texts = {
                *(result_by_id[result_id].summary for result_id in claim.tool_result_ids),
                *(finding_by_id[finding_id].statement for finding_id in claim.finding_ids),
            }
            if claim.text not in evidence_texts:
                raise ValueError(f"Claim {index} text is not an exact supplied evidence statement")

            seen_findings.update(claim.finding_ids)
            tool_names = tuple(
                dict.fromkeys(
                    [result_by_id[item].tool_name for item in claim.tool_result_ids]
                    + [finding_by_id[item].producing_tool for item in claim.finding_ids]
                )
            )
            citations.append(
                NarrativeCitation(
                    claim_index=index,
                    tool_names=tool_names,
                    tool_result_ids=claim.tool_result_ids,
                    finding_ids=claim.finding_ids,
                )
            )

        required_findings = set(finding_by_id)
        if seen_findings != required_findings:
            raise ValueError(
                f"Narrative omitted active findings: {sorted(required_findings - seen_findings)!r}"
            )

        if selection.insufficient_evidence:
            answer = "Bu soruyu yanıtlamak için sağlanan hesaplanmış kanıt yetersiz."
        else:
            lines = ["Yanıt:"]
            for claim, citation in zip(selection.claims, citations, strict=True):
                references = ", ".join(
                    [f"{result_by_id[item].tool_name}:{item}" for item in citation.tool_result_ids]
                    + [
                        f"{finding_by_id[item].producing_tool}:finding:{item}"
                        for item in citation.finding_ids
                    ]
                )
                lines.append(f"- {claim.text} [Kaynak: {references}]")
            if caveats:
                lines.extend(["", "Sınırlamalar:"])
                lines.extend(f"- {caveat}" for caveat in caveats)
            answer = "\n".join(lines)

        return NarrativeResult(
            answer=answer,
            claims=tuple(claim.text for claim in selection.claims),
            citations=tuple(citations),
            surfaced_caveats=caveats,
            surfaced_finding_ids=tuple(finding.finding_id for finding in findings),
            insufficient_evidence=selection.insufficient_evidence,
        )
