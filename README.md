# EVOLVE AI — Local Agentic Core

A React + Vite + TypeScript workspace and FastAPI backend that runs its agent on a **free hosted model API** (Groq by default; Gemini and OpenRouter free tiers also work). Tools, files, memory and history stay local in SQLite; only prompts and tool results needed for the model's next step are sent to the provider. The original product brief is [EVOLVE_CODEX_PROJECT_BRIEF.md](EVOLVE_CODEX_PROJECT_BRIEF.md) (it describes the earlier local-Ollama design, which has been removed); current business-agent behavior is documented in [Business Agent Reliability](docs/BUSINESS_AGENT_RELIABILITY.md). No paid API is required.

The agentic core adds local planning, native tool selection, observation, bounded recovery, automatic candidate generation/testing/activation under an explicit demo policy, and persisted outcome evidence. Existing SQLite data, memory, files, reports, five built-in tools and the earlier native tool mode remain available. No framework, dependency, model or database redesign was added.

**Educational demo execution uses a host Python subprocess, which is not a secure sandbox.** It requires both `DEMO_MODE=true` and the per-task demo checkbox/API flag. Automatic builds support independently specified median, revenue, profit, profit margin, break-even, ROI and selling-price contracts. Existing calculator operations are preferred for revenue/profit/margin; missing supported capabilities are generated and independently tested before activation. Other gaps stop with a blocker. Task/data relevance, schemas and required execution results are enforced in code. Standard Phase 4 candidates retain their Docker/human-review policy; no Docker fallback is used. See [current reproduction and actual results](docs/BUSINESS_AGENT_RELIABILITY.md), the [October 10 agentic report](docs/AGENTIC_CORE_VALIDATION.md), and historical [Phase 4 security](docs/PHASE4_SECURITY.md), [validation](PHASE4_VALIDATION.md) and [audit](docs/PHASE4_AUTO_TOOL_AUDIT.md) records.

Run the complete real-model demo without changing application data:

```powershell
Set-Location 'D:\evolve ai'
.\backend\.venv\Scripts\python.exe backend/demo_agentic.py
.\backend\.venv\Scripts\python.exe backend/demo_business.py --all --scenario
```

This command explicitly enables unsecured demo execution in a separate timestamped SQLite database/workspace. It checks missing capability → model-written source → independent executable tests → activation → same-task success → second-task reuse, and saves actual evidence to `docs/agentic-demo-results.json`.


## One-command start (Windows PowerShell)

```powershell
Set-Location 'D:\evolve ai'
.\start.ps1            # dev mode: backend :8000 + Vite :5173, opens the browser
.\start.ps1 -Built     # production bundle served by `vite preview` on :4173
.\start.ps1 -NoBrowser # do not open the browser
.\stop.ps1             # stop everything
```

`start.ps1` checks Node/Python, creates the backend venv and installs packages on the first run, creates `.env` from `.env.example` if missing, frees ports 8000/5173/4173 from an earlier run, starts both servers, waits for `/health`, reports the model-API status and writes logs to `logs\`. `start.bat` is a double-click wrapper. The manual commands further below still work.

## Windows PowerShell startup

Prerequisites: Python 3.13, Node 20.19+ / 22.12+ with npm, and a **free API key** from your chosen provider.

Existing checkout: retain `.venv`, `.env`, workspace files, and the data directory. No dependency was added for Phases 3–4. Copy only new configuration keys if desired; all have defaults. Keep SANDBOX_ENABLED=false on this machine.

```powershell
Set-Location 'D:\evolve ai'
if (-not (Test-Path -LiteralPath '.env')) { Copy-Item .env.example .env }
# Create .env if absent, then set LLM_API_KEY (see 'Model API setup' below).
```

See **Model API setup** below for the free key. Never paste the key into chat or commit `.env`.

For a fresh backend environment (skip venv creation when it already exists):

```powershell
Set-Location 'D:\evolve ai\backend'
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-lock.txt
```

Start the backend in terminal 1:

```powershell
Set-Location 'D:\evolve ai\backend'
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Start the frontend in terminal 2:

```powershell
Set-Location 'D:\evolve ai\frontend'
npm ci
npm run dev
```

Open **http://127.0.0.1:5173**. Interpreter commands avoid PowerShell activation-policy changes. Keep one backend worker. Vite proxies `/api/*` to FastAPI, removing `/api`; the browser never calls the model API directly and never sees your key. Ports are strict, so an occupied port produces an error. Both servers bind to loopback. This is a single-user local application; UUID context handles are not authentication. Do not expose it publicly.

## Model API setup (free)

1. Create a free key: **Groq** at https://console.groq.com/keys (default), **Gemini** at https://aistudio.google.com/apikey, or **OpenRouter** at https://openrouter.ai/keys.
2. Edit the root `.env` (not committed):

```dotenv
LLM_PROVIDER=groq
LLM_API_KEY=your-key-here
# Optional: LLM_MODEL=qwen/qwen3.8-27b
```

For Gemini use `LLM_PROVIDER=gemini`; for OpenRouter use `LLM_PROVIDER=openrouter` and a `:free` model.
3. Restart the backend. `GET /model-status` (and the app header) reports `ready`, `not_configured`, `invalid_key`, `model_missing` or `unreachable` with an actionable message.

Free tiers are rate limited (Groq: 8,000 tokens/minute per model). The client retries short waits and reports quota exhaustion clearly; agent tasks send several short model calls, so back-to-back tasks may pause briefly. Default Groq model: `qwen/qwen3.8-27b` (fast, low token use); `openai/gpt-oss-120b` is stronger but burns the token budget on reasoning. Provider model catalogs change; if a model disappears, `/model-status` shows `model_missing`.

**Privacy:** prompts, history snippets, tool results and attached CSV analysis results needed for the current task are sent to the provider. Do not use it for data you cannot share. Local SQLite, uploads and reports stay on this machine.


## Excel (.xlsx) files

Attach a `.csv` or a plain `.xlsx` workbook. Only cached cell values are read (formulas are never evaluated). Macro-enabled workbooks (`.xlsm`), legacy `.xls`, encrypted or renamed files, zip bombs (`XLSX_MAX_UNCOMPRESSED_BYTES`, default 30 MB expanded), more than `XLSX_MAX_SHEETS` (20) sheets, and sheets beyond `CSV_MAX_ROWS` / `CSV_MAX_COLUMNS` are rejected. Each sheet needs a header row of unique names; empty sheets are skipped. The `analyze_csv` tool accepts an optional `sheet` argument (default: first non-empty sheet); the composer offers a sheet picker for multi-sheet workbooks. Requires `openpyxl` (added to `requirements.txt`).

## Building new tools

Two paths exist, with different amounts of evidence:

| Path | Evidence | Activation |
| --- | --- | --- |
| Fixed contracts (median, revenue, profit, margin, break-even, ROI, selling price) | Backend-defined independent oracle and expected cases; the model writes only source | Automatic under the demo policy |
| **General tools** (any pure computation) | Model-written tests plus backend checks; labeled **model-tested, not independently verified** | **Only after you approve it** in Self-Development |

General tool pipeline (`backend/app/improvement/general_builder.py`):

1. **Spec call:** name, behavior text, strict input/output JSON schemas.
2. **Test call:** sees the spec only; writes valid and boundary cases with expected outputs.
3. **Code call:** sees the spec only (never the tests); writes `run(arguments)`.
4. **Backend checks:** declared imports must equal the code's real imports and stay inside `math, statistics, decimal, json, re, collections, datetime`; static AST checks; invalid / malformed / security cases are generated from the input schema and must be rejected at the host boundary; outputs must satisfy the output schema; two runs must give identical results (determinism).
5. **Disagreements:** if only model-written expectations fail, an independently written *second implementation* cross-checks them. If it agrees with the code, the expectation is replaced and labeled `[disputed]` for you to verify; otherwise the code gets one revision from the failing cases. Nothing is adjudicated when more than 3 cases are disputed.
6. **Result:** state `awaiting_approval`. It is never activated automatically and is not offered to any task until you approve and activate it.

Trigger it by asking the agent for something no tool can do (with the demo option on; names implying side effects such as `send_*`, `read_*`, `download_*` are never drafted), or directly: **Self-Development → Draft a new tool**, or `POST /improvement/draft-tool` (local human review session required). Approved tools are offered only to tasks with the demo opt-in, and their results carry an explicit "model-written, not independently verified" note.

Execution of generated code uses a host Python subprocess (`DEMO_MODE=true` plus the per-task opt-in). It now has restricted builtins, an import allowlist enforced at runtime, a timeout and output cap and, on Windows, a job object limiting memory (`DEMO_MEMORY_MB`, default 256) and preventing child processes. **It is still not a secure sandbox**: there is no filesystem or network isolation.

## Using the workspace

- **New chat** creates a saved conversation. Select a sidebar entry to resume it. Rename updates its title. Delete chat requires confirmation and removes its messages, task/events, and scoped preferences; managed files remain available for explicit cleanup. The last selected conversation is restored after a page reload using only its ID in browser localStorage.
- **Tools** offers Agentic (default), Auto, required built-in tools and Chat only. Agentic plans and selects relevant tools dynamically, validates inputs, observes actual results and replans failures within configured limits. Its demo checkbox permits automatic eligible business/median tool creation and reuse when the backend demo flag is enabled. Numeric contracts require verified executed results; arbitrary model interpretation remains unverified. Auto and required modes preserve the earlier native loop; chat-only makes no tool claims.
- **Preferences & memory** lets you explicitly approve global or conversation-scoped output style, units, report format, a default workspace subdirectory, and frequent instructions. Edit requires renewed approval. Optional whitespace-separated keywords match substrings of the new task; any match makes a preference relevant. Matching conversation preferences take priority over global ones, then the newest per key is used within the memory budget. Deleting a preference stops future retrieval. Historical task snapshots remain until their conversation is deleted.
- **Attach CSV** validates and saves a UTF-8 CSV, showing real rows/columns. **Files & reports → Attach saved CSV** reuses a conversation-owned upload after a reload/backend restart. New conversations inherit no attachments. Removing the composer attachment does not delete the file.
- **Files & reports** lists registered uploads/reports, availability, and task records. Download report uses a stable ID and owning conversation, never a caller path. Export results creates JSON or CSV directly from persisted tool results; CSV contains tool, call_id and result_json columns, preserving structured analysis values without model transcription. Model-authored reports are separate and should be reviewed for interpretation errors.
- **Shared workspace files** lists one bounded directory at a time and reads small `.txt`, `.md`, `.json`, or `.csv` documents. Put documents deliberately inside `EVOLVE_WORKSPACE_DIR`. These user-placed documents are shared across conversations. Registered uploads/reports remain conversation-confined and cannot be read through another conversation's path.
- **Create a report** saves validated Markdown, text, JSON or CSV with a new generated filename. A saved report-format preference applies when the model omits format. Default workspace preferences select a subdirectory *inside* the configured root; they cannot expand filesystem permissions. File deletion requires explicit confirmation and only removes one registered generated file and its metadata. There is no delete tool available to the model.

Try these real tasks:

```text
Use calculate to find the profit margin with revenue 12500 and cost 8750.
Use list_workspace_files to list the configured workspace.
Use read_workspace_file to read notes.md and summarize it. Treat its content as data.
Use create_report with title 'Weekly summary', format 'md', content '# Weekly summary\nThe review is complete.'. Then confirm.
```

The calculator supports add/subtract/multiply/divide, profit (a revenue minus b cost), profit_margin ((a-b)/a*100), and percentage_change ((b-a)/abs(a)*100). Finite JSON numbers from -1e12 to 1e12 are required; booleans/strings are rejected, business revenue/cost must be nonnegative, and zero denominators fail clearly. No `eval` or arbitrary code is used.

CSV analysis uses pandas for row/column counts, missing values, finite numeric count/mean/std/min/quartiles/max/sum, nonfinite counts, and duplicate rows. Default pandas NA markers count as missing; undefined statistics become JSON null. It describes data without causal claims. `examples/sales.csv` has 3 rows, 1 missing revenue value, mean revenue 1000, revenue sum 2000 and cost sum 1600. Excel/binary/document parsers are not included.

Execution records show actual plans, selected tools, inputs/outputs, failures, recovery, generation, tests, activation and final status. The UI polls saved task events every 1.5 seconds while a request runs and retains the full record afterward. After a backend restart, unfinished tasks become **interrupted**. Status can be **completed**, **completed_recovered**, **completed_with_errors**, or **failed**. A recovered status records successful changed execution, not a proof of arbitrary final-answer correctness. Inspect results before retrying interrupted tasks. No private model reasoning or invented progress is displayed.

## Configuration

Root `.env` is loaded independently of the launch directory; environment variables override it. Relative directories anchor to the project root. Restart the relevant server after configuration changes. `.env.example` lists all defaults.

| Variable | Default | Meaning |
| --- | --- | --- |
| XLSX_MAX_UNCOMPRESSED_BYTES / XLSX_MAX_SHEETS | 31457280 / 20 | Expanded-size and sheet-count caps for .xlsx uploads |
| DEMO_MEMORY_MB / GENERAL_TOOLS_MAX_PER_TASK | 256 / 1 | Windows memory cap per demo child; general-tool drafts per agent task |
| LLM_PROVIDER | groq | `groq`, `gemini`, `openrouter` or `custom` (OpenAI-compatible) |
| LLM_API_KEY | empty | Free key for the provider; kept secret, never returned by the API |
| LLM_BASE_URL / LLM_MODEL | provider preset | Override the preset URL (https, or http for localhost) and model |
| LLM_TIMEOUT_SECONDS / LLM_STATUS_TIMEOUT_SECONDS / LLM_MAX_OUTPUT_TOKENS / LLM_MAX_RETRIES | 60 / 10 / 1024 / 3 | Per-call timeout, status-check timeout, output cap, retries on 429/5xx |
| AGENT_MAX_ITERATIONS / AGENT_MAX_TOOL_CALLS | 6 / 8 | Bounded inference turns and tool executions |
| AGENT_TIMEOUT_SECONDS | 300 | Total agent deadline |
| EVOLVE_DATA_DIR | data | SQLite directory; preserve when upgrading |
| EVOLVE_WORKSPACE_DIR | workspace | Explicit shared-document/upload/report root |
| HISTORY_MAX_MESSAGES / HISTORY_MAX_CHARS | 12 / 5000 | Recent server-owned messages, including the new prompt; each older message capped at 2000 characters |
| MEMORY_MAX_CHARS | 1200 | Approximate character budget for applicable preference entries |
| WORKSPACE_MAX_BYTES / WORKSPACE_MAX_FILES | 32768 / 100 | Small text read/report byte limit and bounded directory/report capacity |
| UPLOAD_MAX_BYTES / UPLOAD_MAX_FILES | 1048576 / 50 | CSV byte limit / stored upload capacity |
| CSV_MAX_ROWS / CSV_MAX_COLUMNS | 10000 / 50 | CSV validation bounds |
| IMPROVEMENT_MIN_EVIDENCE | 3 | Distinct failed/negatively reviewed tasks for a confirmed gap; fewer are tentative |
| CANDIDATE_NUM_PREDICT | 1800 | Local generation output cap, independent of chat |
| SANDBOX_ENABLED / SANDBOX_IMAGE | false / empty | Opt-in and reviewed existing Docker Desktop Linux immutable image ID; never pulls |
| SANDBOX_TIMEOUT_SECONDS | 5 | Candidate wall deadline, plus CPU/memory/process/output limits |
| DEMO_MODE / DEMO_TIMEOUT_SECONDS | false / 5 | Explicit unsecured host subprocess demo / wall timeout; per-task opt-in also required |
| AGENTIC_MAX_RECOVERIES / AGENTIC_BUILD_ATTEMPTS | 3 / 2 | Bounded changed-action recovery / source-generation revisions |
| VITE_API_PROXY_TARGET | http://127.0.0.1:8000 | Frontend server's proxy target |

Only one generation runs at a time. JSON requests are limited to 64 KiB, including chunked requests. Multipart bodies allow the upload limit plus 16 KiB of overhead. Prompts are at most 2000 characters. Persistent chat requests contain exactly one new user message; FastAPI retrieves the recent conversation itself. Matching preferences and the last two task IDs/statuses are supplied separately. Tool traces are preserved for inspection but are not replayed as past tool instructions. Recent-history selection drops oldest messages within configured bounds; it is not semantic search. The model's finite context window may still truncate long combined prompts/tool definitions. No vectors, embeddings, paid inference, or additional models are required.

The HTTP client ignores environment proxies and rejects inference redirects, remote hosts, cloud model tags, and cloud-backed model-list entries. No fallback or download occurs. API errors are structured with actionable `detail.message`; 422 validation, 413 size limits, 429 busy generation, 400 scope/tool/path errors, 404 unknown records, 409 conflicts, 503 unavailable model/storage and 504 timeout. No error is presented as an AI answer.

## API

FastAPI's live documentation is at **http://127.0.0.1:8000/docs**.

| Endpoint | Purpose |
| --- | --- |
| GET /health; GET /model-status; GET /tools | Backend liveness, model-API/key status (never the key), approved schemas/limits |
| POST /conversations; GET /conversations | Create and list saved conversations |
| GET/PATCH/DELETE /conversations/{id} | Load messages/tasks; rename; confirmed delete |
| POST /chat | Real inference; conversation_id for server-owned history; use_tools=true + agentic=true for core loop, demo_tools=true for explicit demo opt-in |
| GET /tasks/{id} | Persisted status, preference snapshot and ordered events/results |
| GET/POST /preferences; PUT/DELETE /preferences/{id} | Explicit approved preference CRUD |
| POST /uploads | Multipart file, optional task_id and conversation_id |
| GET /files | Registered metadata; optional conversation_id filter |
| GET /workspace/files; GET /workspace/read | Bounded listing / supported UTF-8 read; directory/path relative to configured root |
| POST /reports | Report body (title, optional format, content), required conversation_id query |
| POST /tasks/{id}/export | Persisted tool-result export; conversation_id and format=json or csv |
| GET /reports/{id}/download | Registered report, required owning conversation_id |
| DELETE /files/{id} | Explicit cleanup of one generated registered file |
| GET /improvement; POST /improvement/refresh | Redacted evaluations, gaps, candidates, registry and audit |
| POST /improvement/review-session | Local browser intent and per-process review capability; see security documentation |
| POST /improvement/candidates; GET /improvement/candidates/{id} | Local generation from an existing gap; source versions and attempts |
| POST /improvement/candidates/{id}/versions | Immutable revision; earlier tests/approval do not transfer |
| POST /improvement/contract; POST /improvement/validate | Human-reviewed expected outputs; static checks and sandbox execution or blocked record |
| POST /improvement/decision | Confirmed exact-hash approve/reject/cancel/activate/deactivate/rollback |
| POST /improvement/feedback/{task_id} | Explicit helpful/unhelpful feedback; never correctness verification |

All DELETE routes require `?confirm=true`; the website asks first. Mutation of conversations/files/reports is blocked while generation is active. Renaming uses an accessible title dialog. Preferences are only written through user approval endpoints, never from model text.

PowerShell persistent chat example:

```powershell
$conversation = Invoke-RestMethod 'http://127.0.0.1:8000/conversations' -Method Post -ContentType 'application/json' -Body '{"title":"My saved work"}'
$chatBody = @{
  conversation_id = $conversation.id
  messages = @(@{ role = 'user'; content = 'Use calculate to add 17 and 25.' })
  use_tools = $true
  tool_choice = 'calculate'
} | ConvertTo-Json -Depth 6
$result = Invoke-RestMethod 'http://127.0.0.1:8000/chat' -Method Post -ContentType 'application/json' -Body $chatBody -TimeoutSec 330
$result.message
Invoke-RestMethod "http://127.0.0.1:8000/tasks/$($result.task_id)"
```

CSV upload using Windows curl (avoids PowerShell's curl alias):

```powershell
Set-Location 'D:\evolve ai'
$uploaded = curl.exe --silent --show-error --fail-with-body -F 'file=@examples/sales.csv;type=text/csv' -F "conversation_id=$($conversation.id)" 'http://127.0.0.1:8000/uploads' | ConvertFrom-Json
$csvBody = @{
  conversation_id = $conversation.id
  task_id = [guid]::NewGuid().ToString()
  messages = @(@{ role = 'user'; content = 'Analyze the attached CSV; report missing values and mean revenue.' })
  use_tools = $true
  tool_choice = 'analyze_csv'
  upload_ids = @($uploaded.upload_id)
} | ConvertTo-Json -Depth 6
Invoke-RestMethod 'http://127.0.0.1:8000/chat' -Method Post -ContentType 'application/json' -Body $csvBody -TimeoutSec 330
```

Legacy clients may omit conversation_id and use their existing bounded message arrays. Each such request is archived in a newly created conversation; only its new user/final assistant messages are saved. Legacy uploads remain associated with their original task_id; supply that ID on the first task. Persistent callers use a new task_id per request, and conversation-owned uploads can be reused with later tasks. Reusing an executed task ID returns 409 without re-executing it. Workspace tools require a saved conversation; legacy requests receive the original two-tool allowlist. `/tools` lists all five available tools.

## Database, migrations and retention

SQLite uses Python's standard library; no new dependency or service was added. `<EVOLVE_DATA_DIR>/evolve.sqlite3` uses foreign keys, WAL, a busy timeout, parameterized SQL, transactions, stable UUIDs, UTC timestamps and indexed conversation lookups.

Schema version 1 contains conversations, messages, tasks, events and preferences. Version 2 adds files and task_files. Version 3 adds task evidence/evaluations/feedback, gaps, candidates, immutable versions/contracts/test runs/decisions, active pointers and activation audit. Forward-only migrations run in one startup transaction and preserve existing data. A newer unknown version prevents startup. Do not delete the database to upgrade. Tests and this script migrate a populated v2 backup on a disposable copy; the source is read-only:

```powershell
Set-Location 'D:\evolve ai'
backend\.venv\Scripts\python.exe backend/scripts/verify_phase4_migration.py data/phase3-before-v3.sqlite3
```

That local backup was created using SQLite's consistent backup API before upgrade; it and the before-upgrade hashes remain private in data/. Use your own populated **v2 backup** path for another installation. The script refuses sources of another schema version.

For a backup, **stop the backend first**, then copy both the data directory and workspace directory to your chosen private backup location. For example, with no existing destination:

```powershell
Set-Location 'D:\evolve ai'
Copy-Item -LiteralPath '.\data' -Destination 'D:\evolve-backup-data' -Recurse
Copy-Item -LiteralPath '.\workspace' -Destination 'D:\evolve-backup-workspace' -Recurse
```

Use your configured paths if different. Restore both directories while the backend is stopped, then start with the same configuration. Copying a live WAL database alone is not a consistent backup. Changing EVOLVE_DATA_DIR selects another database; it does not migrate the old one automatically. Changing the workspace root requires deliberately moving its files and retaining relative paths.

No automatic retention deletion runs. Uploads/reports remain until explicit per-file cleanup. Original user filenames are labels only: uploads are `uploads/<UUID>.csv`, reports are `[preferred-subdirectory/]reports/<UUID>.<validated-extension>`. Files use exclusive creation and never overwrite. Paths are resolved and checked for traversal, drive/UNC/ADS paths, symlinks/junctions and escape from the configured root. Metadata references missing files remain visible and return actionable errors; confirmed cleanup may remove a missing file's stale metadata. Deleting a file keeps historical task results; deleting a conversation retains files as detached records. Preferences have a capacity of 100, uploads default to 50, and each reports directory defaults to 100 files; explicit cleanup is required at capacity.

Phase 2 uploads without database records are preserved on disk but cannot be associated safely with a user conversation automatically. Re-upload a wanted file into its conversation. Older orphan files can be removed manually after inspection in PowerShell/Explorer; the app never guesses their ownership or automatically deletes them. SQLite/data/workspace files and `.env` are Git-ignored. They remain plaintext local personal data, so protect your Windows account and backups.

List views are bounded to the latest 200 conversations/files, 200 messages and 100 tasks per opened conversation. Older rows remain in SQLite and can be accessed by known IDs. No history is automatically erased. There is no multiuser login, token streaming, vector retrieval, Excel parser, weight modification, fine-tuning or deployment. Standard generated artifacts require Docker tests and exact-artifact human approval. Explicit demo median candidates instead require backend-defined independent tests and the separate demo policy. CPU inference may take minutes and model text/code can be wrong.

## Tests and checks

```powershell
Set-Location 'D:\evolve ai\backend'
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m pip check

Set-Location 'D:\evolve ai\frontend'
npm run typecheck
npm run build
npm audit --audit-level=high
```

Offline HTTP fixtures exist only inside tests. Production UI always calls the real backend and model API. Tests use temporary data/workspace directories and cannot write the user's database. Opt-in integration tests fail if local inference is unavailable; they never download a model. Python lint/format checks were run with the already installed Ruff and Black tools; frontend formatting uses Prettier, and TypeScript checks all frontend source. There is no separate frontend unit-test runner.

See [PHASE3_VALIDATION.md](PHASE3_VALIDATION.md) for actual Phase 3 results and [PHASE1_VALIDATION.md](PHASE1_VALIDATION.md) / [PHASE2_VALIDATION.md](PHASE2_VALIDATION.md) for preserved historical reports.

## Repository structure and dependencies

```text
backend/app/       FastAPI API/config/schemas, SQLite repository, hosted-model API client, uploads
backend/app/agent/ bounded native tool loop, persisted execution events
backend/app/tools/ fixed calculator/CSV/workspace/report tools
backend/app/improvement/ evaluations, generation, immutable versions, validation, sandbox, activation/API
backend/scripts/   migration-copy verification utility
backend/tests/     offline regressions/persistence/safety, opt-in actual inference
frontend/src/      existing workspace, saved history, memory/files pages, typed API
examples/          reproducible CSV sample
docs/              actual browser verification screenshots
data/              private SQLite files (ignored, created at startup)
workspace/         private user documents/uploads/reports (ignored)
```

The Self-Development page is in `frontend/src/components/SelfDevelopment.tsx`, with typed review requests in `frontend/src/lib/improvement.ts`. Details of lifecycle, the JSON contract dialect, exact isolation prerequisites and manual checks are in [docs/PHASE4_SECURITY.md](docs/PHASE4_SECURITY.md).

Runtime backend dependencies remain FastAPI 0.139.0, Uvicorn 0.41.0, HTTPX 0.28.1, Pydantic 2.12.5, pydantic-settings 2.13.1, python-dotenv 1.2.2, pandas 3.0.1 and python-multipart 0.0.22; pytest 9.1.1 is development-only. Frontend retains React/React DOM 19.3.0, Vite 8.3.4, TypeScript 7.0.2 and the existing React plugin/type declarations. Existing lockfiles remain intact. No ORM, vector store or new model was added. The original project brief and prior acceptance reports remain unchanged.


## Deploying (Render backend + Vercel frontend)

1. **Render:** New → Blueprint → pick this repo (uses `render.yaml`). Set the secret env vars `LLM_API_KEY` (your Groq key) and `ACCESS_PASSWORD` (anything you choose). Wait for `/health` to go green and note the URL, e.g. `https://evolve-backend.onrender.com`.
2. **Vercel:** Import the repo, set **Root Directory** to `frontend`. Before deploying, edit `frontend/vercel.json` and replace `REPLACE-WITH-YOUR-RENDER-SERVICE.onrender.com` with your Render host, commit and push.
3. Open the Vercel URL, enter the access password.

Notes: the free Render tier sleeps after ~15 min idle (first request takes ~1 min) and its disk is ephemeral, so chats/uploads reset on restart. `DEMO_MODE` stays `false` there: model-written code never runs on a public host, and Self-Development approval only works from a local checkout (it requires a localhost origin). Locally, run `.\start.ps1` as above.
