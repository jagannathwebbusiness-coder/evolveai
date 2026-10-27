# EVOLVE local agentic core — implementation and reproduction

Historical October 10 snapshot. The October 11 [business reliability implementation](BUSINESS_AGENT_RELIABILITY.md) extends its median-only generation scope and orchestration checks. Results below describe the original recorded runs.

This implements the October 10 agentic-core request in the existing application. It supersedes the earlier prohibition on host candidate execution **only for explicitly enabled educational demo tasks**. Earlier Phase 1–4 reports remain historical records. A demo subprocess is **not a secure sandbox**: it has no OS isolation of the user's filesystem, network or memory.

## Preserved application and inspection

The existing repository contains `backend/app` (FastAPI, configuration, Ollama client, SQLite, uploads, tools, agent and improvement modules), `backend/tests`, `frontend/src` (React/Vite/TypeScript and existing workspace pages), `examples`, documentation, private `data` and `workspace`. It has no Git repository. The five built-ins are calculate, analyze_csv, list_workspace_files, read_workspace_file and create_report. Existing generated candidates, source hashes, review gates and schema-v3 tables were reused.

No dependency or lockfile changed. Installed versions: Python 3.13.5; Node 24.4.1/npm 11.4.2; React/React DOM 19.3.0; Vite 8.3.4; TypeScript 7.0.2; FastAPI 0.139.0; Uvicorn 0.41.0; HTTPX 0.28.1; Pydantic 2.12.5; pydantic-settings 2.13.1; pandas 3.0.1; existing python-dotenv/python-multipart and pytest. No model, paid API, Docker image, framework or infrastructure was installed.

Before the application restart, a consistent SQLite backup was saved at `data/backups/before-agentic-core.sqlite3`. Original row counts: four conversations, five tasks, nine messages, one preference, three registered files, one candidate and two immutable versions. Five existing workspace files were fingerprinted. The separate real-model demo never uses this database. Verification results are recorded below after execution.

## Concise implemented plan

1. Preserve the legacy mode; add a bounded planning/native-act/observe/recover loop.
2. Add an explicitly enabled local subprocess adapter with time/output bounds and clear demo labels.
3. Recognize an actual missing capability; generate source locally against a backend-defined independent contract, execute tests, activate only a passed exact version and resume the task.
4. Persist version/decision/outcome evidence and reuse tested tools; retain rollback gates.
5. Poll actual backend events in the existing UI and verify regressions, build and real-model acceptance.

## Implementation

- `backend/app/agent/core.py`: Ollama-generated plan, native tool decisions, schema validation, real execution, observation, classified failures, changed-action recovery and completion guards. Ordinary failures return to the model with actual error information. Identical failed calls stop. Repeated successful calls are suppressed and corrected within the recovery budget. No unresolved tool failure can be presented as completed. Limits: six decisions, eight tool calls, three recoveries, two source-build attempts and a total task deadline by default.
- `demo_contracts.py`: pre-existing backend contract/oracle for `median_numbers`, 1–20 integers in [-1000000,1000000], output `{median: float}`. The oracle uses Python's trusted statistics module only to define/check results; **user answers come from the model-written candidate's actual subprocess output**. The model cannot change schemas, oracle, expected cases or activation policy. Inputs are grounded in the user's explicit JSON list.
- `demo_builder.py`: source-only local Ollama generation, immutable source/manifest versions, real failure feedback for bounded revisions, independent validation, exact hash-bound automatic decisions labeled `automatic-demo-policy`. The existing manifest's suggested-test slot contains a backend suite excerpt with `test_provenance=backend_independent_demo`; the full independent suite is stored separately as an immutable contract. Model-suggested tests for ordinary candidates remain suggestions.
- `demo_subprocess.py` / `demo_runner.py`: static checks before launch, temporary work directory, host Python `-I -B`, no shell invocation, filtered environment, bounded output and timeout, actual child PID/exit/results. Candidate code is imported only by the child. These controls do not make hostile code safe.
- Existing repository/validator/activation machinery persists source SHA-256, combined artifact hash, suite hash, test execution policy/runtime identity, runs, decisions and activation pointers. Built-in names cannot be replaced. Changes/revisions need their own tests/approval; replacement requires retained regressions and measured case improvement. Rollback selects an exact previously active approved/tested version. Standard candidates never silently use the host adapter.
- Evaluation independently recomputes calculator results and median task inputs/output/final values, distinguishes recovered execution from correctness, and counts retries, generated candidates, activations and reuse. Median gaps remain linked to their original failure even after resolution. Persisted verified strategies and tool failures inform model selection; capability resolution ranks active matching tools using observed successes/failures. No model weights change. A tiny demo is not evidence of general learning or benchmark improvement.
- Existing UI gains Agentic mode, an unchecked demo opt-in and live polling of persisted events. Plans, current step, inputs/outputs, failures, candidate/test/activation events and final results come from actual backend events. Self-Development displays demo status, source, provenance, immutable tests, decisions, active versions and manual rollback/deactivation controls.

## Reproduce the complete real-model demo

Use the existing installations. This command explicitly opts into unsecured demo execution and allocates an independent timestamped database and workspace:

```powershell
Set-Location 'D:\evolve ai'
ollama list
.\backend\.venv\Scripts\python.exe backend/demo_agentic.py
```

It uses actual HTTP requests to local Ollama (no MockTransport or canned model/source response) and actual host subprocess execution. It asserts two goals in order:

1. `Find the median of [9, 1, 4, 7]. Use the median capability and verify the result.` Expected final: `The median is 5.5.` Initially the real registry rejects missing `median_numbers`. That actual failure creates the gap, source generation/test/activation follows, and the agent retries using the changed registry.
2. `Find the median of [18, 2, 11, 6, 9]. Use the median capability and verify the result.` Expected final: `The median is 9.` The agent executes the activated tool with new inputs and no new generation.

The script asserts plans, actual tool execution, independent goal verification, first-task failure/reflection/build/tests/activation/retry and second-task reuse. It writes `docs/agentic-demo-results.json` containing actual ordered events, task IDs, source hashes, test outputs, child PID/exit status, durations and evaluation evidence. Source/history remain in the separate database at the `data_dir` recorded in the report. Each default run starts with a new database; pointing `--data-dir` at an already activated database will deliberately fail the first-task missing-capability assertion. Do not reset your application database to repeat a demo.

The 17 acceptance checks include seven subprocess executions (unsorted odd/even, negatives, duplicates, singleton, range endpoints and maximum length) and ten host schema rejection tests (empty/oversized lists, range/type errors, missing/malformed object, injected command/path fields). Security-category cases exercise input rejection, **not secure code isolation**. Output schema and exact JSON numeric expectations must also pass. Syntax/AST failures, crashes, nonzero exit, timeout, output overflow or incorrect results prevent activation. Original failed attempts are retained; `agentic-demo-initial-failure.json`, `agentic-demo-repeat-failure.json` and `agentic-demo-json-loop-failure.json` document development failures rather than fabricated successes.

## Website demonstration and PowerShell setup

For existing installations, retain the venv, node_modules, data and workspace. No dependency reinstall is needed. Keep Ollama running with the already installed `qwen2.5:3b`; do not pull another model.

Create `.env` from `.env.example` only if absent. For this explicitly requested educational demo, set these root `.env` values (edit existing keys; environment variables override the file):

```dotenv
OLLAMA_MODEL=qwen2.5:3b
DEMO_MODE=true
DEMO_TIMEOUT_SECONDS=5
AGENT_TIMEOUT_SECONDS=600
OLLAMA_NUM_CTX=4096
OLLAMA_NUM_PREDICT=1000
SANDBOX_ENABLED=false
```

Start/restart a single backend worker after currently running tasks finish:

```powershell
Set-Location 'D:\evolve ai\backend'
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

In another PowerShell terminal:

```powershell
Set-Location 'D:\evolve ai\frontend'
npm run dev
```

Open http://127.0.0.1:5173, create a new chat, select **Agentic · plan and recover**, check **Enable local DEMO tool generation and execution for this task**, and send the first goal above. Watch the open Execution record. Send the second goal with the opt-in still enabled. If a median tool already exists, both tasks reuse it; use the separate demo script for a clean initial-gap run. Ordinary missing capabilities without an independent build contract stop rather than activate unverified code.

In Self-Development, inspect the active candidate source, version hashes, independent contract actor, all test outcomes and automatic decisions. Deactivation keeps source/history and removes the active pointer. Rollback is offered only when another previously active approved version of the same tool exists; creating an improved replacement requires all prior regression cases plus a demonstrated improvement. Merely generating revisions does not make them eligible.

Optional PowerShell API equivalent:

```powershell
$body = @{
  messages = @(@{ role = 'user'; content = 'Find the median of [9, 1, 4, 7]. Use the median capability and verify the result.' })
  use_tools = $true
  agentic = $true
  demo_tools = $true
} | ConvertTo-Json -Depth 8
Invoke-RestMethod -Method Post -Uri 'http://127.0.0.1:8000/chat' -ContentType 'application/json' -Body $body -TimeoutSec 610
```

Set `DEMO_MODE=false` and restart to disable host execution. Generated demo sources/history remain stored; they are omitted from non-opted-in execution registries. The checkbox resets when opening another conversation/new chat. Manual review endpoints retain their local-origin/session protection.

## Checks and actual results

Baseline preserved suite: 243 passed, nine opt-in integrations skipped, one existing Starlette/HTTPX TestClient deprecation warning. New agentic regression coverage uses explicit offline model-decision fixtures and **real** child processes; these fixtures are never used by the application or the real demo script. Coverage includes independent-test failures, revision, no premature activation, opt-ins, unsupported gaps, dynamic calculation/report chaining, changed arguments, loop bounds, actual timeout/error/output overflow, immutable hashes, restart reuse and actual subprocess replacement/comparison/rollback.

Actual completed checks on October 10, 2026:

| Check | Actual result |
| --- | --- |
| Full backend suite: `.\backend\.venv\Scripts\python.exe -m pytest backend/tests -q` | **264 passed, 9 skipped**, 71.95 seconds; one existing Starlette/HTTPX deprecation warning |
| Explicit real-Ollama integration suite: `EVOLVE_RUN_OLLAMA_TEST=1`, same interpreter, `-m pytest backend/tests/test_ollama_integration.py -q --tb=short` | **5 passed**, 240.34 seconds; the same warning |
| `npm run build` in `frontend` | TypeScript and Vite production build passed |
| `python -m black --check backend/app backend/tests backend/demo_agentic.py` | Passed, 43 files unchanged; uses the existing global Python formatter |
| `python -m ruff check backend/app backend/tests backend/demo_agentic.py` | Passed; uses the existing global Python linter |
| Existing Prettier, six changed frontend source files | Passed |
| Backend venv `python -m pip check` | No broken requirements |
| Real agentic acceptance script | **Passed**; actual local model, generated source, executed tests, activation and reuse |
| Live website tool chaining | **Passed**; calculation returned 42 and a real Markdown report was saved |
| Original database rows and workspace bytes | **Preserved**; see preservation report |

The nine default skips comprise five opt-in Ollama tests (separately run and passed above) and four Docker integration probes. Docker probes remain unexecuted because this machine has no Docker runtime/image. No dependencies were installed to run these checks. Black/Ruff are available through global Python, not the backend venv.

### Real acceptance evidence

[agentic-demo-results.json](agentic-demo-results.json) records `acceptance_passed: true` and the full actual execution trace:

| Task | Observed result | Missing-tool failures | Tool requests | Candidate builds | Reuse |
| --- | --- | --- | --- | --- | --- |
| `470c9ab6-f23f-4d02-a8b8-fb8b87e142a2` | Median **5.5**, independently verified; 82.007 seconds | 1 | 2 | 1 | 0 |
| `38ff7c17-e36e-4963-aee5-8dcd7e36318d` | Median **9**, independently verified; 86.301 seconds | 0 | 1 | 0 | 1 |

The first task's actual unknown-tool error led to reflection, local source generation, persistence, 17/17 independent checks, automatic activation, successful execution and independent completion. The model-written active tool is `candidate_median_f0dcc525bc`, version 2, source SHA-256 `c4e220032954918fdb00f5cde0b4086f4f49027128a7da3d045dbf7ccafb0059`. Version 1 is the preserved initial proposal; this successful run used one source-generation attempt. The validation child actually ran (PID 9888, exit 0, 202 ms, no timeout/output overflow). The unedited generated source and manifest are exported as [agentic-generated-median.py](agentic-generated-median.py) and [agentic-generated-median-manifest.json](agentic-generated-median-manifest.json). Source and lifecycle history remain in the isolated database named by the report.

For the supported median-only goal template, a backend completion predicate independently compares the **executed candidate result** to the expected result and terminates when satisfied. Its `completion_check` event explicitly identifies `backend_independent_oracle`; it is not presented as another model decision. Compound and other goals continue through model-selected actions. General final answers have no independent proof of correctness.

Measured improvement here means fewer missing-capability failures and tool requests on the second task, with no second source build. It does **not** establish faster inference (the second task was slower), improved model weights, or generalization to arbitrary capabilities. Failed development runs remain recorded in the separate failure reports and their isolated databases; they were not counted as successful acceptance runs.

### Browser and preservation evidence

[agentic-browser-results.json](agentic-browser-results.json) records website task `cbc92ab7-e1bd-4a9e-ac71-1ba60b41cac1`: the model selected `calculate`, observed actual output 42, then selected `create_report` using that result. Both calls succeeded, and registered report `1c53bb60-77e7-467a-947c-038d8cc700aa` contains the computed expression (with Markdown asterisk escaping preserved). Live persisted events were displayed during execution; screenshots are [agentic-live-execution.png](agentic-live-execution.png) and [agentic-browser-completed.png](agentic-browser-completed.png). This separate browser check exercised built-in chaining; generated-tool activation/reuse was checked by the real-model script above. Its generated candidate belongs to the isolated demonstration database, not the main website database.

[agentic-preservation-results.json](agentic-preservation-results.json) compares all original rows in 17 user/audit tables against the consistent backup and checks all five original workspace file hashes. All match. The two derived tables, `evaluations` and `gaps`, were recomputed as intended; new verification tasks/reports append records. Existing conversations, memories, files, old candidate versions and audit events were retained. SQLite remains schema version 3.

Automatic source generation currently supports the independently defined **median contract only**. Unsupported capability gaps stop with an explicit blocker. There is no secure sandbox, general-purpose automatic tool-contract invention, arbitrary-task correctness proof, model-weight learning, general benchmark claim, production deployment or additional personal-assistant feature in this implementation. Version replacement/comparison/rollback and changed-argument recovery are covered by automated regression tests with real subprocesses; they are not claimed as additional real-model browser demonstrations.
