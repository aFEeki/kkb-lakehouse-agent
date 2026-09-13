# Analysis table

`web/src/components/analysis-table.tsx` renders the table-facing subset of an
`AnalysisFrame`. The development page reads the `result` event from SCRUM-68's committed
successful replay fixture; it does not contact an SSE endpoint.

The spine is always the first displayed column. Data columns are mapped directly in their
received order without sorting or regrouping. A header displays the unit symbol and scale as
`Birim: <symbol> · ölçek: <tr-TR formatted scale>`; absent unit metadata is shown explicitly
as em dashes.

Only JSON `null` is treated as missing and rendered as a visible dashed gap with an accessible
label. Numeric zero passes through the ordinary value path and renders as `0`. The table has
intrinsic width, while its bounded wrapper owns horizontal overflow, so wide results scroll
inside the component rather than widening the page.
