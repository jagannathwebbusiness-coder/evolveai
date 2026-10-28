# EVOLVE: self-developing agentic AI for business intelligence

EVOLVE answers business questions by **planning, acting with tools, checking its own results, learning from failures and building new tools when a capability is missing**. It is not a chat-only assistant: every message runs the agent loop.

```
Execute → Evaluate → Identify → Develop → Validate → Improve
```

## How it works (hybrid ReAct + Reflexion)

| Stage | What happens | Where you see it |
| --- | --- | --- |
| **Plan** | The model writes a short plan with a result-based success check. | Plan card in the workflow |
| **ReAct** | Each step: *Thought* (brief public reasoning) → *Action* (a tool call with real arguments) → *Observation* (the real tool output). | Thought / Action rows |
| **Evaluate** | The backend verifies numbers against independent business oracles and rejects answers with no evidence. | Evaluate rows |
| **Reflexion** | Failures become short lessons stored in SQLite (`lessons` table) and are recalled before planning similar tasks. | Reflexion rows |
| **Identify** | If no registered tool fits, the gap is recorded. | Capability gap |
| **Develop / Validate** | A new tool is built from separate model calls (spec, independent tests, code), then checked: boundary cases, determinism, import allowlist, second-implementation cross-check. | Develop rows |
| **Improve** | The tool is trialled once, stored with its source, spec and tests, and offered to future tasks after **human approval**. | Self-Development page |

Fixed business contracts (revenue, profit, margin, break-even, ROI, median, selling price) are auto-generated, tested against a backend oracle and activated without approval. **General tools** (anything else) are model-tested only, labelled *not independently verified*, and need your approval before they are used in later tasks.

Evaluation metrics from the project deck (task success rate, tool accuracy, execution time, human intervention, tools built, lessons learned, regression rate) are on the Self-Development page and at `GET /metrics`.

## Run locally (Windows PowerShell)

```powershell
Set-Location 'D:\evolve ai'
.\start.ps1            # backend :8000 + Vite :5173, opens the browser
.\start.ps1 -Built     # production bundle on :4173
.\stop.ps1
```

First run creates the venv, installs packages and a `.env` from `.env.example`. Set `LLM_API_KEY` (free keys: Groq, Gemini or OpenRouter, see `.env.example`) and `DEMO_MODE=true` to enable tool building.

## Files, Excel and reports

Attach a `.csv` or `.xlsx` (formulas are never evaluated; macros, `.xls` and oversized files are rejected). The agent picks a sheet by name. It can also read workspace files and save reports. A sample is in `examples/profit_loss_small.xlsx`.

## Deploying (Render backend + Vercel frontend)

1. **Render:** New → Blueprint from this repo (`render.yaml`). Set `LLM_API_KEY` and `ACCESS_PASSWORD`. Build `pip install -r backend/requirements.txt`, start `cd backend && uvicorn app.main:app --host 0.0.0.0 --port $PORT --proxy-headers --forwarded-allow-ips "*"`.
2. **Vercel:** import the repo, Root Directory `frontend`. `frontend/vercel.json` forwards `/api/*` to the Render URL.
3. Open the Vercel URL and enter the access password.

The free Render disk is temporary, so chats, tools and lessons reset on restart. `DEMO_MODE=true` on Render lets the agent build tools there; generated code then runs in a restricted subprocess on that server (see the warning below), so keep `ACCESS_PASSWORD` set. Approving tools needs a localhost session.

## Safety

Generated code runs in a host Python subprocess with restricted builtins, an import allowlist (`math, statistics, decimal, json, re, collections, datetime`), timeout, output cap and, on Windows, a memory/child-process limit. **This is not a secure sandbox.** Tool output and file contents are treated as untrusted data. Review endpoints require a localhost origin and a per-process token.

## Layout

```
backend/app/agent/        planner + ReAct loop (core.py), policy, tool-call loop
backend/app/reflexion.py  lessons stored and recalled
backend/app/metrics.py    deck evaluation metrics
backend/app/improvement/  tool builders, validation, approval, subprocess runner
backend/app/tools/        calculator, CSV/Excel, workspace files, reports
frontend/src/             React UI (workflow timeline, Self-Development, metrics)
```

Tests: `backend\.venv\Scripts\python.exe -m pytest` from `backend/`. Frontend: `npm run build` in `frontend/`.
