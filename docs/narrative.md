# Evidence-bounded narrative generation

`kkb_agent.agent.NarrativeGenerator` produces the final Turkish answer from an immutable
`AnalysisFrame`, its existing `Finding` objects and already-computed `ComputedToolResult`
records. It never executes an operation, calculates a value, resolves a series, queries a
database or contacts a source other than MIA.

```python
result = generator.generate(
    user_question="Konut kredisi hacmi ne oldu?",
    findings=frame.findings,
    tool_results=computed_results,
    frame_context=frame,
)
```

`ComputedToolResult` is the narrow narrative-boundary adapter for a result ID, producing tool,
already-computed Turkish summary and optional displayed values. A canonical cross-tool result
model does not yet exist elsewhere in the repository; this adapter performs no computation.

## Evidence and citations

MIA uses the confirmed native strict JSON Schema path with thinking disabled. It selects
claims by exact supplied text and references tool-result and/or finding IDs. Local validation
rejects unknown IDs, uncited claims, omitted active findings, paraphrased factual statements
and any number or assertion not present in the supplied evidence. One invalid selection gets
one correction attempt with bounded validation context; a second failure raises
`NarrativeValidationError`.

The final answer is rendered deterministically in Turkish. Each line includes a visible
`[Kaynak: tool_name:result_id]` or `[Kaynak: tool_name:finding:finding_id]` marker.
`NarrativeResult.citations` exposes the same mapping structurally.

Only effective active findings are presented; a later `supersedes` link removes its direct
predecessor from the current narrative without deleting history. Every effective finding is
required in the selection, so refusal statements remain visible. Their caveats are appended
under `Sınırlamalar:` and exposed through `surfaced_caveats`.

With no finding or tool evidence, the generator makes no model call and returns an explicit
Turkish insufficient-evidence response. Findings and the frame remain immutable throughout.
