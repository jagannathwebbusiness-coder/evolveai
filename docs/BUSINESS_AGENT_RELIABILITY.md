# EVOLVE business agent reliability — October 11, 2026

This is the current implementation guide. The original brief and earlier Phase 1–4/October 10 reports describe preserved project history. No architecture, dependencies, model or database schema was replaced. Existing local `qwen2.5:3b`, FastAPI, React/Vite/TypeScript, SQLite, memory, reports and registered tools remain.

## Inspection and root causes

The existing database records failed task `457c2dc3-c122-4645-8164-256b4354b2af`: a plan whose success check was merely outlining steps, a nonexistent CSV handle `sales_data`, empty workspace directory arguments, and an invented `sales_data.csv`. No tool succeeded. The code already performed basic Pydantic/schema validation; validation alone did not prevent irrelevant selection.

* `CoreAgent.decide` exposed every registered tool, regardless of attachments or task intent. Its restrictions on CSV/workspace use were primarily prompt instructions.
* Registry schema errors identified field names but did not provide constraints, expected schemas or correction details. Malformed/JSON-encoded native arguments could fail before recoverable tool feedback.
* Failed optional/irrelevant selections polluted the unresolved failure set. Replanning relied on the same broad catalog, and the six-decision budget could be exhausted without progress.
* Completion checked any successful tool call and unresolved failures, rather than all requested numeric outputs/report/file actions. Only median had an independent goal predicate.
* Generation, manifest tagging, gap persistence and evaluation were directly wired to median.

Inspection found an Intel i5-1135G7 (4 cores/8 threads), about 8 GB RAM and integrated Iris Xe graphics. Installed Ollama models include the required `qwen2.5:3b`; it is retained. No model replacement, model download or paid API was used.

A consistent backup is at `data/backups/before-business-reliability.sqlite3`; original workspace file hashes are at `data/before-business-files.json`. Tests and acceptance demonstrations allocate separate databases. The application database is never reset.

## Implemented plan and behavior

1. Add a deterministic task/data policy and enforce it before execution, including calls the model makes despite filtered definitions.
2. Validate registered names, JSON schemas and semantics; return bounded field-level error details and schemas. Decode valid JSON-encoded arguments; reject malformed/nonfinite decisions before execution.
3. Replan from actual failures and unmet, explicitly grounded requirements. An invalid name, irrelevant tool or premature answer can trigger a deterministic corrective action; its `replan` event explicitly says `decision_source=backend_requirement_guard`. This is never described as another model decision.
4. Generalize the existing generation/version/testing/activation path to a finite business contract catalog. Prefer matching existing built-in tools.
5. Require actual execution and independently verified outputs before numeric completion. Persist outcomes and reuse exact activated versions after app restart. Verify offline regressions and real local inference.

`backend/app/agent/policy.py` filters offered tools and checks attempted calls. CSV analysis requires both task relevance and an actual attached upload; its UUID must belong to the task. Workspace access requires an explicit file/directory task. A read path must come from the user or an actual successful directory listing. Inline business numbers never authorize an invented CSV/file. Reports require an explicit request and successful prerequisite calculations/analysis. Completed business capabilities are removed from subsequent tool definitions.

`tools/schema_validation.py` supports the registered schema subset with paths, required fields, types, enum and size/range errors; Pydantic remains authoritative for built-in formats/interfaces. Generated tools retain the restricted schema dialect and independent semantic input checks. Both kinds are checked before calling their implementations. `600` and `600.0` are equivalent in JSON-number fields; integer-only fields still reject floats, strings and booleans.

Identical failed calls stop; identical successful calculator calls are recognized even when numeric spelling changes. Recovery is bounded (three failures/recoveries by default), as are source revisions (two), tool requests (eight), decisions (six for ordinary tasks; up to twelve for multiple grounded business results) and total task time. Only genuine unresolved required actions block completion; irrelevant failures remain visible audit evidence. Invalid actual business inputs are blockers rather than invitations to change the user's numbers.

Plans, including model-written numeric guesses inside a plan, are not an oracle. For eligible numeric goals, actual outputs must match independent trusted calculations on the explicit task operands. Final verified values are rendered from executed outputs. Free-form model numeric narrative and execution claims are omitted. Requested qualitative recommendations are extracted from the full response, checked for count and obvious numeric/tool claims, and labeled **not independently verified**; that shape/provenance guard is not an advice-correctness proof. Missing recommendations require another bounded decision rather than silently completing. Actual created reports retain their backend download links.

## Supported business contracts

Inputs are finite bounded JSON numbers; booleans/numeric strings are rejected. Monetary values normally range from 0 to 1,000,000,000; the schemas in `business_contracts.py` are authoritative. Losses are allowed in profit/ROI results. No tax, financing, multi-product allocation or accounting assumptions are invented.

| Capability | Explicit inputs | Verified output and definition | Existing tool preference |
| --- | --- | --- | --- |
| `business_revenue` | `units` (integer 0–1,000,000), `unit_price` | `revenue = units × unit_price` | `calculate.multiply` |
| `business_profit` | `revenue`, `total_cost` | `profit = revenue − total_cost` | `calculate.profit` |
| `business_profit_margin` | positive `revenue` (minimum 0.01), `total_cost` | `profit_margin_percent = (revenue − total_cost) / revenue × 100` | `calculate.profit_margin` |
| `business_break_even` | `fixed_cost`, `unit_price`, `variable_cost` | Whole `break_even_units`, `break_even_revenue`, `contribution_per_unit`; positive contribution required | Generated if absent |
| `business_roi` | signed **net** `net_profit`, positive `investment` (minimum 0.01) | `roi_percent = net_profit / investment × 100` | Generated if absent |
| `business_selling_price` | `unit_cost`, `target_margin_percent` (0–99.99) | `selling_price = unit_cost / (1 − margin/100)`; margin, not markup | Generated if absent |

Break-even monetary inputs have at most two decimal places. Integer currency cents avoid a floating-point ceiling defect (e.g. fixed cost 1, price 0.30, variable cost 0.20 requires **10**, not 11, units). Break-even revenue is whole-unit sales revenue, not fractional-unit revenue. Other outputs retain unrounded numeric values in execution events; display uses compact numeric formatting. Business test comparisons use relative tolerance 1e-10/absolute tolerance 1e-8, with declared output schemas enforced. Median retains its existing exact-output comparison and 17-case contract.

Generation supports these six contracts plus median; **it does not invent arbitrary contracts**. Revenue/profit/margin currently resolve to built-ins, avoiding unnecessary generation. The same source-generation machinery supports their independently defined contracts if the matching built-in operation is genuinely unavailable. Automated subprocess contract tests cover all six; real-model acceptance exercises the actually missing break-even/ROI/selling-price tools.

Prefer explicit JSON operands:

```text
Calculate revenue using {"units":200,"unit_price":600}.
Calculate profit using {"revenue":120000,"total_cost":105000}.
Calculate profit margin using {"revenue":120000,"total_cost":105000}.
Calculate break-even using {"fixed_cost":45000,"unit_price":600,"variable_cost":300}.
Calculate ROI using {"net_profit":300,"investment":1000}.
Calculate selling price using {"unit_cost":80,"target_margin_percent":20}.
```

Named operands such as `revenue 12500 and cost 8750` also work for the supported contracts. The original unit-economics wording (`sells 200 units ... at Rs600 each`, variable cost 300, fixed costs 45000, sales increase 20%) grounds current and projected revenue/cost/profit requirements. Missing, conflicting or ambiguous required operands stop with schemas to supply, rather than model-extracted guesses. Arbitrary natural-language scenario parsing, multiple products/periods, mixed median/business requests, complex accounting, and all possible combinations of calculations are not claimed.

## Real source generation, testing and lifecycle

An eligible requested capability is resolved against installed tools. A real `unknown_tool` failure is required before recording a gap and invoking local Ollama. The backend pins the contract, input/output schemas, expected cases, version name and policy; the model supplies candidate source only. No source fallback or canned AI result exists in the application/real acceptance script.

The generated interface is `run(arguments) -> JSON object`. Syntax/AST checks, input/output schemas, normal/boundary results and invalid/malformed/injected input rejection are independently tested. Revenue/profit/margin/ROI/selling-price suites have 13 checks each; break-even has 16. Valid inputs invoke model-written code in an actual child process; invalid inputs are rejected at the trusted host boundary without feeding them to candidate code. PID, exit status, stdout/stderr, timeout, output limit and individual outcomes are persisted. Failure prevents activation and supplies actual findings for a bounded source revision. Expected outputs are never accepted from the model.

The model sometimes emits an otherwise valid dictionary on a line longer than the preserved 120-column validation limit. `source_formatting.py` can insert whitespace inside bracketed expressions; it accepts the result only when parsing both sources produces identical ASTs. The original model bytes and formatted source are separate immutable versions, connected by a real `candidate_formatted` event and hashes. This does not repair logic or fabricate test results. All normal static/executable gates still run against the exact version to activate. The earlier formatting-blocked run remains in [business-formatting-failure.json](business-formatting-failure.json).

Only an exact passing source/content/suite hash and execution runtime identity can be approved/activated under `automatic-demo-policy`. Activated generated names are separate from built-ins. Version sources, contracts, runs, decisions and activation audit remain immutable. Existing replacement/comparison/rollback gates are retained. Startup reloads active pointers from SQLite; each generated invocation rechecks its approved/tested executable version. Evaluation recomputes numeric evidence from original user inputs and actual tool events instead of trusting a success label. Stored successes/failures influence tool selection; no model weights change.

**Demo execution is not a secure sandbox.** It has a timeout/output bounds and static checks, but no OS filesystem/network/memory isolation. It requires both `DEMO_MODE=true` and the per-task `demo_tools=true` checkbox/API flag. Normal reviewed Docker candidates keep their existing policy and do not fall back to host execution. No Docker image or infrastructure is installed here.

## PowerShell reproduction

Use existing installations, with Ollama running and `qwen2.5:3b` installed. No dependency reinstall or model pull is needed.

```powershell
Set-Location 'D:\evolve ai'
ollama list
# Complete break-even generation, independent tests, activation, execution,
# then a second task in a fresh app instance using the SAME activated version:
.\backend\.venv\Scripts\python.exe backend/demo_business.py
# Also exercise ROI/selling-price generation and the original composite scenario:
.\backend\.venv\Scripts\python.exe backend/demo_business.py --all --scenario
# Existing median acceptance, preserving the historical report:
.\backend\.venv\Scripts\python.exe backend/demo_agentic.py --report docs/median-business-regression-results.json
```

Every default script run creates a new isolated database under `data/business-demo` or `data/agentic-demo`, preserving application data. Reusing an already activated demo directory intentionally fails the initial missing-capability assertion; do not reset your app database. Evidence includes actual task traces, candidate source/history, source hashes, independent test results and implementation hashes. The scripts explicitly opt into educational execution. CPU inference can take several minutes, especially for a multi-step task; waits/failures are not replaced with successful fake results.

For the website, keep the existing root `.env` and set `OLLAMA_BASE_URL=http://127.0.0.1:11434`, `DEMO_MODE=true`, `AGENT_TIMEOUT_SECONDS=600`, `OLLAMA_NUM_CTX=4096`, `OLLAMA_NUM_PREDICT=1000`; leave `SANDBOX_ENABLED=false` on this machine. The explicit loopback address avoids this Windows host's observed localhost connection delay (about 2 seconds versus 0.06 seconds on a direct local probe). Restart the single backend only after running tasks finish:

```powershell
Set-Location 'D:\evolve ai\backend'
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
# Separate terminal; retain existing node_modules:
Set-Location 'D:\evolve ai\frontend'
npm run dev
```

Open http://127.0.0.1:5173, select Agentic and enable the local DEMO checkbox for generation/reuse. Send the first break-even goal, then `Calculate break-even using {"fixed_cost":1000,"unit_price":90,"variable_cost":30}.` If already activated, the first also reuses; the isolated script is the clean missing-capability demonstration. The execution record polls actual persisted events. Self-Development shows source, exact versions, contracts/tests, automatic decisions, deactivation and eligible rollback. Isolated script candidates do not appear in the separate website database.

Offline tests and opt-in real integrations:

```powershell
Set-Location 'D:\evolve ai'
.\backend\.venv\Scripts\python.exe -m pytest backend/tests -q
$env:EVOLVE_RUN_BUSINESS_TEST = '1'
.\backend\.venv\Scripts\python.exe -m pytest backend/tests/test_business_ollama_integration.py -q
Remove-Item Env:EVOLVE_RUN_BUSINESS_TEST
Set-Location frontend
npm run build
```

## Actual verification

The final offline run passed **301 tests**, with **12 opt-in skips** and one existing Starlette/httpx TestClient deprecation warning, in 251.18 seconds. The skips were three business real-model wrappers, five legacy Ollama integrations and four reviewed Docker integrations. The real demonstrations below ran separately; skipped pytest items are not reported as passing. TypeScript/Vite build (22 modules), Black, Ruff, Prettier and `pip check` passed. Actual check metadata is in [business-check-results.json](business-check-results.json).

The fresh final command `backend/demo_business.py --scenario` passed using real `qwen2.5:3b` inference, without model/source fixtures. [business-acceptance-results.json](business-acceptance-results.json) includes complete traces, original model source, formatted immutable source, independent test results, child PIDs, exact active versions and implementation hashes. Those implementation hashes matched the code after the run.

| Actual task | Result | Time |
| --- | --- | --- |
| `661c2e94-bb3e-4667-b395-de53fce88ffe` | Actual missing break-even failure → local generation → **16/16** independent checks → activation → 150 units / 90000 revenue / 300 contribution | 71.656 s |
| `5000152b-ed70-4031-8dc3-13a73b9e4a9a` | New app instance, same SQLite and active version; 17 units / 1530 revenue / 60 contribution; **no regeneration** | 98.092 s |
| `494cde7d-e27b-41ca-b138-63b8057a94d5` | Original composite scenario: five independently verified tool outputs and three qualitative recommendations; reused break-even tool | 502.119 s |

The activated tool was `candidate_break_even_4e04c6dafd`, version `5932376c-e00e-4dd1-a846-c3f383feea5d`, source SHA256 `2ad2bac511fd09a1ce7893486a2206e73c2cae77bed812fa8042c6947b0ddda3`. Validation executed six valid/boundary cases in child PID 1228, with exit code 0 and no timeout/output-limit event; ten invalid/malformed/security cases were rejected at the host boundary. The original and AST-equivalent formatted source are both preserved.

The composite scenario returned revenue **120000**, total costs **105000**, profit **15000**, margin **12.5%**, break-even **150 units / 90000 revenue**, projected profit **27000** and projected costs **117000**. It recovered from one invalid plan response, one malformed native tool decision and a stale current-profit request when projected profit was still missing. A backend corrective action executed the actual projected operands. All numerical statements in the final response came from verified execution; the three extracted model recommendations remain explicitly unverified advice. It took eight decisions and nearly the 600-second timeout on this CPU; this is a demonstrated bounded run, not a guarantee of latency or every model decision succeeding.

The first real composite run exposed numeric-spelling grounding errors and stopped at the recovery limit. Its honest failed evidence is retained in [business-grounding-failure.json](business-grounding-failure.json); it is not counted as a passing run. A subsequent run stopped at static formatting validation without activating anything; that record is also retained.

[business-source-pipeline-results.json](business-source-pipeline-results.json) records actual local-model generation, independent tests, activation and second-task restart reuse for break-even (16 checks), ROI (13) and selling price (13). ROI and selling price each failed initial syntax validation before any executable cases ran, received real validation feedback, generated a new version, and then passed the executable independent suite before activation. ROI outputs were 30% then -20%; selling prices were 100 then approximately 142.8571429. This report predates the final answer guard: its composite scenario executed all five correct numeric outputs but the appended model narrative contradicted break-even and claimed unexecuted calculations. It is **source-pipeline evidence, not clean final-answer acceptance**. Original traces/source/version hashes remain available for inspection. Final composition was corrected, with regressions for false numeric claims, advice count, full-response extraction and actual report downloads, before a fresh acceptance run.

Secure Docker probes remain unavailable; arbitrary tool creation and prose correctness remain unsupported.

[business-process-reload-results.json](business-process-reload-results.json) records a separate Python process reloading the activated break-even, ROI and selling-price versions from their existing SQLite databases and successfully executing the second operands. That probe made no model request; it supplements the real-model task reuse records rather than pretending to be another agent task.

The website backend was restarted with the final code after confirming no application tasks were running. Backend health and Vite-proxied health returned HTTP 200; model status was `ready` for `qwen2.5:3b` at `127.0.0.1:11434`. [business-preservation-results.json](business-preservation-results.json) confirms every original row across 17 user/audit tables and all six original workspace file hashes remained unchanged. Schema version remains 3. Derived evaluations/gaps were recomputed from preserved evidence. No dependencies, models or user data were reset.

The existing real-model median demonstration also passed on the final code: task `33080dc9-57f4-4592-a664-0f19a86c9862` generated/tested/activated its tool, passed **17/17** independent cases and returned **5.5** (93.596 s). Task `1b320e1b-11c7-4953-8eae-8bebe20d67c7` reused that activated tool and returned **9** (61.079 s), after rejecting a wrong-operand selection and recovering from the actual request. [median-business-regression-results.json](median-business-regression-results.json) retains the actual complete traces. No test/task failure was silently converted into a success; the successful responses include their observed failures and recovery events.
