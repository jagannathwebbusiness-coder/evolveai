# EVOLVE Personal AI --- Project Brief and Codex Build Instructions

## 1. Project overview

**Project name:** EVOLVE Personal AI\
**Working tagline:** A local-first personal AI agent that uses tools,
automates everyday work, and improves its capabilities through validated
self-development.

EVOLVE is a small, practical personal assistant the user can run on
their own Windows laptop. The user interacts with it through a website
and gives it tasks such as: - Analyze CSV/Excel business data. - Perform
calculations and create reports. - Read user-selected documents. - Help
organize and automate repetitive work. - Remember user-approved
preferences and task history. - Propose, generate, test, and version
simple new Python tools when existing tools are insufficient.

This is an incremental student project for learning Agentic AI. It must
be genuinely functional, not a static UI or a demo full of mock outputs.

### Key distinction

EVOLVE should improve its **tools, memory, and execution strategies**
based on measurable outcomes. The initial project does **not** train or
change the underlying language model's neural-network weights. Do not
claim that it does. Fine-tuning is out of scope for the first version.

## 2. Current hardware and model constraints

-   OS: Windows (PowerShell).
-   RAM: 8 GB or less.
-   No dedicated NVIDIA GPU.
-   User wants to avoid paid model APIs and recurring costs.
-   Ollama is installed.
-   Models currently installed:
    -   `qwen2.5:3b` --- approximately 1.9 GB; **primary model to test
        first**.
    -   `tinyllama:latest` --- approximately 637 MB; fallback for simple
        tasks only.
-   Ollama local endpoint: `http://localhost:11434`.
-   The project must not require any paid hosted model API or API key.
-   Do not download another model automatically. First use `qwen2.5:3b`;
    make the model configurable so a different local Ollama model can be
    selected later.
-   The model may be weak at complex tool calling or code generation.
    Build deterministic backend validation and tests rather than
    trusting model output.

## 3. Technology stack

Use a lightweight monorepo:

### Frontend

-   React
-   Vite
-   TypeScript
-   Plain CSS or a lightweight styling approach; avoid large unnecessary
    UI dependencies.
-   A clean, modern, responsive personal AI workspace.

### Backend

-   Python with FastAPI
-   Ollama's local API for inference
-   Pydantic for request/response validation
-   SQLite for local persistence
-   pytest for tests
-   pandas and openpyxl for CSV/Excel analysis
-   Add document parsing libraries only when needed.

### Local model configuration

-   Default model: `qwen2.5:3b`
-   Default Ollama base URL: `http://localhost:11434`
-   Configure using environment variables such as `OLLAMA_BASE_URL` and
    `OLLAMA_MODEL`.
-   Never expose a remote model API key because the initial
    implementation must work without one.

### Avoid for the first release

-   PostgreSQL, Redis, Kubernetes, microservices, paid APIs, cloud
    dependencies, complex multi-agent frameworks, model fine-tuning, and
    elaborate infrastructure.
-   Do not add LangGraph unless a concrete need emerges. Begin with a
    clear, testable Python agent loop.
-   Do not build numerous agents just for appearance. A single
    orchestrator plus ordinary Python modules/tools is enough initially.

## 4. Architecture and behavior

Expected flow:

1.  User sends a task through the React chat UI.
2.  FastAPI receives and validates the request.
3.  Backend sends messages and tool definitions to Ollama.
4.  If the model requests a tool, the backend checks the tool against an
    explicit allowlist and validates arguments.
5.  Backend executes only approved tool functions.
6.  Tool results are sent back to the model.
7.  The agent loop continues until a final answer is returned, an
    iteration limit is reached, or a human decision is needed.
8.  UI displays the answer and available task activity/status.
9.  Conversation/task history is stored locally in SQLite.

The backend must own tool execution. The model must never be allowed to
execute arbitrary code directly.

## 5. Initial tools

Implement only a few real, testable tools first:

1.  `calculate`
    -   Perform safe arithmetic or explicitly supported business
        calculations.
    -   Do not use unrestricted `eval`.
    -   Validate inputs and handle division by zero.
2.  `analyze_csv`
    -   Accept a file selected/uploaded by the user.
    -   Validate file type and size.
    -   Use pandas to report columns, row count, missing values, numeric
        summaries, and useful simple statistics.
    -   Do not make unsupported causal claims about data.
3.  `read_workspace_file`
    -   Read files only from an explicitly configured EVOLVE workspace
        directory.
    -   Resolve paths and prevent path traversal or access outside the
        workspace.
    -   Restrict allowed file types.
4.  `create_report` (can be added after the first tools work)
    -   Create a report in the workspace from validated text/data.
    -   Avoid overwriting existing files without explicit permission.

All tools need clear schemas, argument validation, predictable outputs,
and unit tests.

## 6. Self-development: staged and controlled

Self-development is the project's differentiator, but do not attempt
unrestricted self-modification in the first coding pass.

### Stage A: memory and execution history

-   Store conversations, task outcomes, tool calls, errors, and
    user-approved preferences in SQLite.
-   Retrieve relevant history when appropriate.
-   Do not treat arbitrary model-generated claims as verified memory.
-   Let users inspect/delete stored history.

### Stage B: evaluation and reflection

-   Record task status, tool failures, retries, duration where feasible,
    and explicit evaluation results.
-   Start with deterministic checks and expected outputs.
-   A reflection step may propose likely causes and improvements, but
    the LLM's self-assessment alone is not proof of success.
-   Maintain a small regression-test suite for each tool.

### Stage C: candidate tool generation

-   The model may propose a Python tool in a separate candidate area.
-   Save each candidate with a version identifier and metadata.
-   Generate tests alongside the candidate.
-   Never overwrite the backend, core application code, existing
    approved tools, or security policies.
-   Never auto-install packages or allow unrestricted network access.
-   Do not expose secrets, credentials, the user's entire filesystem, or
    unrestricted shell access to generated code.
-   Do not run untrusted generated code in the main FastAPI process.
-   Run candidate code only in a restricted isolated environment with
    resource/time limits. If a suitable sandbox is not available on the
    user's machine, keep candidate code as a review-only artifact and
    clearly explain that it has not been safely executed.
-   Require tests, comparison against the current version, and explicit
    user approval before activating a new tool.
-   Version every approved tool and support rollback.

### Stage D: measurable improvement

-   Compare the candidate against the existing version on the same test
    cases.
-   Reject candidates that fail tests, cause regressions, or violate
    safety rules.
-   Store before/after metrics and the approval decision.
-   Do not claim self-improvement unless the result is demonstrated by
    tests/metrics.

## 7. Safety and reliability requirements

-   Local-first by default.
-   Never execute arbitrary model output as code.
-   Never use unrestricted `eval` or `exec` in the API process.
-   Never allow path traversal out of the workspace.
-   Limit tool-loop iterations and request size.
-   Add sensible timeouts and error handling.
-   Do not permit destructive file operations or external communications
    without explicit user confirmation.
-   Keep local API endpoints configurable.
-   Do not expose Ollama or FastAPI publicly by default.
-   Validate all API inputs and all tool arguments.
-   Handle Ollama unavailable/model missing errors with clear
    user-facing messages.
-   Keep `.env`, databases containing personal data, uploaded files,
    generated secrets, and virtual environments out of Git.
-   Add `.env.example`, `.gitignore`, and a README.
-   Be honest in the UI about which operations were actually executed
    versus merely proposed.

## 8. UI expectations

Build a practical interface with: - Sidebar for conversations or
tasks. - Main chat workspace. - Clear user/assistant messages. - A
visible indication that the model is local. - Task/tool activity status
(e.g., planning, using tool, completed, failed). - File upload for
CSV/Excel analysis. - A simple tools/capabilities page listing installed
tools and versions. - A simple history/performance page showing real
persisted records only. - Clear error states if Ollama is unavailable. -
Responsive layout and accessible controls.

Do not fill the UI with fake metrics, fake agent thoughts, fake task
progress, or mock tool execution. Show real backend state only. Do not
expose private chain-of-thought; show concise action/status summaries
instead.

## 9. Suggested repository structure

``` text
evolve-personal-ai/
  frontend/
    src/
      components/
      pages/
      lib/
    package.json
  backend/
    app/
      main.py
      config.py
      api/
      agent/
        loop.py
        prompts.py
      tools/
        registry.py
        calculator.py
        csv_analysis.py
        workspace_files.py
      storage/
        database.py
        repositories.py
      improvement/
        evaluator.py
        generator.py
        versions.py
    tests/
    requirements.txt
  workspace/
  generated_tools/
  data/
  .env.example
  .gitignore
  README.md
```

Adjust this structure only when it improves simplicity. Do not create
empty files/modules just to match the tree.

## 10. Implementation phases

### Phase 1 --- inspect and establish foundation

-   Inspect the existing repository before modifying it.
-   Preserve existing user files and conventions.
-   If the repository is empty, scaffold the monorepo.
-   Build a minimal FastAPI health endpoint and Ollama
    connectivity/model-status endpoint.
-   Build the Vite chat UI and connect it to the backend.
-   Add configuration, README, `.env.example`, `.gitignore`, and basic
    tests.
-   Ensure the project starts on Windows PowerShell.

### Phase 2 --- working tool-calling agent

-   Implement the Ollama chat/tool-calling loop using the local API.
-   Confirm the exact response schema for the installed Ollama
    version/model; handle missing or malformed tool calls.
-   Add calculator and CSV analysis tools.
-   Validate arguments, cap iterations, and return tool results to the
    model.
-   Add tests for success, malformed requests, unknown tools, errors,
    and iteration limits.
-   Demonstrate a real end-to-end request, not a mocked response.

### Phase 3 --- persistence and personal usefulness

-   Add SQLite conversation/task history.
-   Add explicit user preferences and history controls.
-   Add workspace-restricted file operations and report generation.
-   Display real task status and tool execution results.

### Phase 4 --- evaluation and self-development

-   Add tool performance records and deterministic regression tests.
-   Implement candidate tool generation and candidate metadata.
-   Implement safe sandbox execution only if it can be reliably
    supported; otherwise provide review-only candidates.
-   Add approval, version activation, before/after comparisons, and
    rollback.
-   Demonstrate one small, measurable tool improvement.

Do not try to implement all phases in a single unreviewed code dump.
Work phase by phase, run tests, and report what works and what remains.

## 11. Codex working instructions

You are the coding agent implementing EVOLVE. Follow these instructions:

1.  First inspect the current repository and report its contents and any
    existing app/framework.
2.  Do not delete or overwrite existing project files without a clear
    reason.
3.  Before coding, present a concise implementation plan for Phase 1.
4.  Implement Phase 1 only unless explicitly asked to proceed further.
5.  Keep changes small, readable, typed where practical, and modular.
6.  Run relevant tests and builds. Fix failures caused by your changes.
7.  Do not claim tests passed unless you actually ran them.
8.  If a command cannot be run in the environment, state that limitation
    and give the exact command the user should run locally.
9.  Do not invent credentials or use paid APIs.
10. Do not silently switch models or download models.
11. Ensure Windows PowerShell commands are documented.
12. After implementation, summarize:
    -   Files created/changed.
    -   Commands to install and run frontend/backend.
    -   Tests/builds actually executed and their results.
    -   Known limitations and next milestone.

## 12. Phase 1 acceptance criteria

Phase 1 is complete only when: - Backend starts with documented Windows
commands. - `GET /health` returns a real health response. - A
model-status endpoint can tell whether Ollama is reachable and whether
`qwen2.5:3b` is installed. - Frontend starts and can send a message to
the backend. - Backend can make a real local Ollama request and return
the response, or show a clear actionable error if Ollama/model is
unavailable. - No paid API is required. - Basic backend tests pass. -
README explains the setup and run commands. - No mock AI answer is
presented as a real model response.

## 13. Initial Windows commands (reference)

Check local models:

``` powershell
ollama list
```

Start/test the current model:

``` powershell
ollama run qwen2.5:3b
```

Create backend environment from the repository root (assuming Python
3.13 is installed):

``` powershell
cd backend
py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install fastapi "uvicorn[standard]" ollama pydantic pytest pandas openpyxl
```

Create the frontend from the repository root if it does not already
exist:

``` powershell
npm create vite@latest frontend -- --template react-ts
cd frontend
npm install
```

Do not repeat scaffold commands if the corresponding project already
exists.

## 14. Product definition

EVOLVE is a local-first personal AI assistant with: - A website
interface. - Local LLM inference through Ollama. - A controlled tool
registry. - Practical work automation. - Persistent user-approved
memory. - Evaluation and reflection. - Candidate tool generation,
testing, approval, versioning, and rollback.

The initial deliverable is a small, useful, working personal agent.
Self-development is added only after the basic tool-calling agent works
reliably.
