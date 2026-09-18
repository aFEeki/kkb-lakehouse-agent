"""Route a natural-language question to one of the brief's tools.

The brief requires it in as many words: "Doğal dilde gelen soruyu ilgili tool'lara
yönlendirip yanıtı verebilmelidir." Until now nothing did. Five tools were built and
tested - causality carries 1,707 lines of tests, the URL agent passes six live checks
against Borsa İstanbul - and none of them could be reached by asking a question. The turns
pass `tool="lakehouse"` as a stage label; no tool module was ever invoked.

The split is the same one the planner and executor already use, because it is the reason
any of this can be trusted:

    the model decides WHICH tool      -> schema-constrained to the names that exist
    the code decides IF that is legal -> an unknown name is unrepresentable, not rejected
    the code supplies the data        -> tools receive values and dates, never a question

A deterministic keyword fallback runs when MIA is unreachable, so the demo survives the
model being down - and `chosen_by` says which path ran, because "the agent chose this" and
"a keyword matched" must never look the same.

What this deliberately does not do is guess. A question matching no tool is refused, as it
was before. A router that silently picks the wrong tool is worse than an honest refusal:
the answer still arrives, still looks computed, and is about something else.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

# The brief's six tools, by its own names. `web_search` is listed because the brief lists
# it; selecting it yields an honest refusal until SCRUM-67 builds it, which is better than
# pretending the question was unroutable.
TOOLS = (
    "lakehouse",
    "anomaly",
    "causality",
    "change_detection",
    "url_agent",
    "web_search",
)

UNIMPLEMENTED = {"web_search"}

_URL = re.compile(r"https?://\S+", re.IGNORECASE)

# Turkish cues per tool, checked against a casefolded question. Deliberately narrow: a cue
# that fires on an ordinary data question would route it away from the lakehouse and hand a
# reviewer a confidently wrong answer.
_CUES: dict[str, tuple[str, ...]] = {
    "anomaly": (
        "anomali",
        "aykırı",
        "olağandışı",
        "olağan dışı",
        "sapma",
        "uç değer",
        "sıra dışı",
        "outlier",
    ),
    "change_detection": (
        "kırılma",
        "kırılım",
        "rejim",
        "değişim noktası",
        "trend değiş",
        "seviye değiş",
        "yapısal değişim",
        "break",
    ),
    "causality": (
        "nedensellik",
        "neden oldu",
        "yol açtı",
        "etkiliyor mu",
        "etkiledi mi",
        "sebep oldu",
        "granger",
        "neden sonuç",
        "neden-sonuç",
    ),
}


@dataclass(frozen=True)
class ToolChoice:
    """Which tool a question wants, and how that was decided."""

    tool: str | None
    reason: str
    chosen_by: str  # "planner" | "rules"

    @property
    def routable(self) -> bool:
        return self.tool is not None and self.tool not in UNIMPLEMENTED


def tool_choice_schema() -> dict:
    """One enum over the tool names, so an invented tool cannot be returned.

    `tool` is nullable on purpose. Forcing a choice would make "none of these" the one
    answer the model cannot give, and the model would pick the closest name instead - which
    is precisely the silent mis-route this router exists to avoid.
    """
    return {
        "type": "object",
        "properties": {
            "tool": {"type": ["string", "null"], "enum": [*TOOLS, None]},
            "reason": {"type": "string"},
        },
        "required": ["tool", "reason"],
        "additionalProperties": False,
    }


_SYSTEM = (
    "Bir finansal veri ajanının araç seçicisisin. Kullanıcının sorusunu oku ve hangi "
    "aracın çalıştırılması gerektiğini seç.\n\n"
    "lakehouse: kataloğdaki seriyi sorgulama, birleştirme, gösterme\n"
    "anomaly: aykırı değer, olağandışı hareket, sapma tespiti\n"
    "causality: iki seri arasında neden-sonuç ilişkisi olup olmadığı\n"
    "change_detection: seviye, trend veya rejim kırılması tespiti\n"
    "url_agent: soruda verilen bir URL'deki içeriği okuma\n"
    "web_search: internette arama\n\n"
    "Hiçbiri uymuyorsa tool alanını null bırak. Emin değilsen null bırak: yanlış araç "
    "seçmek, seçmemekten kötüdür."
)


def rule_choice(question: str) -> ToolChoice:
    """The deterministic fallback, and the reference for what each tool is for.

    Order matters. A URL in the question settles it before any keyword is consulted: the
    content has to be read before anything can be said about it.
    """
    text = question.casefold()

    if _URL.search(question):
        return ToolChoice("url_agent", "soruda bir URL var", "rules")

    for tool, cues in _CUES.items():
        hit = next((cue for cue in cues if cue in text), None)
        if hit is not None:
            return ToolChoice(tool, f"anahtar ifade: {hit!r}", "rules")

    return ToolChoice(None, "hiçbir araç bu soruyla eşleşmedi", "rules")


def select_tool(question: str, *, planner=None, mia_client=None) -> ToolChoice:
    """Ask the model which tool to run; fall back to the keyword rules.

    `mia_client` rather than a planner object because choosing a tool is a different call
    from planning operations - the planner's schema is about frame operations and has no
    room for this. Passing neither gives the rule path, which is what tests and an
    unconfigured deployment get.
    """
    if not question.strip():
        raise ValueError("question must not be empty")

    client = mia_client if mia_client is not None else getattr(planner, "_mia_client", None)
    if client is None:
        return rule_choice(question)

    try:
        response = client.get_client().chat.completions.create(
            model=client.chat_model,
            messages=[
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": question},
            ],
            max_tokens=200,
            extra_body={"chat_template_kwargs": {"enable_thinking": False}},
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": "tool_choice",
                    "schema": tool_choice_schema(),
                    "strict": True,
                },
            },
        )
        payload = json.loads(response.choices[0].message.content or "{}")
    except Exception:
        # The model being unreachable is not a reason to answer nothing. The rules are
        # weaker, and saying which one ran is how that stays visible.
        return rule_choice(question)

    tool = payload.get("tool")
    if tool is not None and tool not in TOOLS:
        # Unreachable under a strict enum, and cheap to refuse to trust anyway.
        return rule_choice(question)
    reason = str(payload.get("reason") or "").strip() or "model seçimi"
    return ToolChoice(tool, reason, "planner")
