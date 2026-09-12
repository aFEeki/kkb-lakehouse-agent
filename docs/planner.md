# Operation planner

`kkb_agent.agent.OperationPlanner` converts user intent plus already resolved context into a
tuple of validated `Operation` objects. It does not resolve catalog series, inspect or mutate
an `AnalysisFrame`, execute operations, calculate values or generate findings.

```python
planner = OperationPlanner(mia_client)
operations = planner.plan(
    user_intent="Rebase housing loans at January 2025.",
    resolved_context="column_key=loans; base_date=2025-01-01",
    current_version=2,
)
```

The caller supplies plain resolved context because series/catalog resolution belongs to a
separate task. The planner returns one or more operations in execution order. Their source
and resulting versions must form a contiguous chain starting at `current_version`, and their
operation IDs must be unique within the emitted plan.

## Structured output and validation

The MIA capability probe confirms that native `response_format.type="json_schema"` with
`strict=True` is enforced. The planner uses that path with thinking disabled and does not use
legacy `guided_json`, whose schema is not enforced on this deployment.

The response is a minimal `{ "operations": [...] }` envelope. Its item schema is generated
directly from `Operation.model_json_schema()`; there is no parallel operation/request model.
Every returned item is then validated locally with `Operation.model_validate(...)`. This
enforces the frozen vocabulary and the typed parameters for `add_column`, `deflate_column`,
`index_column` and `revert_to`. Extra arithmetic results, mutation instructions, unknown
kinds, mismatched parameters and extra fields fail validation.

## Retry boundary

Invalid JSON, an invalid envelope, Pydantic validation errors or a broken version chain cause
one correction request containing bounded validation context. A second invalid response
raises `PlannerValidationError`. No fallback operation is invented. Provider-call failures
raise `PlannerModelError` directly; planning never invokes `OperationExecutor`.
