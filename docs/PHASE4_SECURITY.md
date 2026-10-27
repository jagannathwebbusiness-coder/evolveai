# Phase 4 workflow, isolation and limits

## Scope and trust

Phase 4 generates **review-only** tool artifacts using the configured existing local Ollama model, default qwen2.5:3b. It does not train weights, change built-in Python files, install packages, download images/models, deploy, or approve its own proposals. The application remains single-user and loopback-only.

Generated code is untrusted. FastAPI never imports, evals, execs, or launches it with the host Python interpreter. Static validation uses `ast.parse` and bounded JSON validation only. `sandbox_runner.py` is an application-owned harness copied into a container fixture directory; its candidate import happens **only inside the isolated container**. Do not invoke that harness on Windows or use it as a subprocess fallback.

Current machine: Docker and Podman are absent. Ubuntu under WSL2 is present, but an ordinary WSL subprocess would have network access and access to Windows files. It is deliberately not used. `SANDBOX_ENABLED=false` is the safe default. Static findings and blocked validation runs remain useful; blocked does not mean tested, approved or improved.

## Evidence and evaluation

`evaluation.py` derives metrics from persisted task/events. It stores requested mode, status, call count, validation failures, duration, final-answer presence, explicit retry linkage, deterministic verification and optional helpful/unhelpful feedback. The website's Retry message control supplies the previous failed task ID when known; retries are not guessed from similar text. Legacy task modes/retries remain explicitly unknown.

Rules version `deterministic-v1` distinguishes:

- **Failure:** failed/interrupted/completed_with_errors task, absent final answer, or incorrect recomputed calculator tool data.
- **Verified success:** one successful calculator operation, independently recomputed matching tool data, and a final answer consisting entirely of the correct number with an appropriate optional percentage suffix.
- **Unverified completion:** a final answer exists without a failing task, but its correctness has not been established. A calculator result can be verified while the surrounding model interpretation remains unverified. CSV/report text is never automatically declared correct.

Helpfulness feedback is human-supplied evidence, not correctness verification. Dashboard telemetry contains task IDs, event sequence/kind/tool references and categories; it excludes prompt/answer/error-message text, arguments/results, files and preferences. The original task records retain those details in their existing local conversation views. Candidate source and explicit review reasons are intentional review data, separate from general telemetry. Generation sends only the selected gap category/count to the local model.

Gap occurrences count distinct tasks, not repeated events or model speculation. `IMPROVEMENT_MIN_EVIDENCE=3` confirms a gap; lower counts are tentative. Existing failure categories and unhelpful feedback drive grouping. A gap is an investigation proposal, not proof that a new tool will help. Deleting source conversations removes their evaluations; refreshing/restarting updates retained gaps to zero/no_current_evidence where appropriate. Gap records persist to keep candidate provenance inspectable; evidence refs can become unavailable after explicit history deletion. No task is invented for dashboard metrics.

## Candidate and contract lifecycle

Generation first reserves a stable candidate and proposed immutable version. A valid response appends a generated version, with description, input/output schemas, dependencies, permissions, source and two hashes. Thus the first generated source is normally v2; v1 is the retained proposal reservation. Failed valid-JSON/schema responses are retained as a **generation_failed raw artifact**, explicitly labelled unvalidated and never executable. Unavailable model attempts retain their failure record. Incomplete generation never becomes generated. Restart marks interrupted proposals/validations failed.

Normal lifecycle:

```text
proposed → generated → validating → awaiting_approval → approved → active
                     ↘ validation_failed / sandbox_blocked
active → inactive → active (explicit reactivation or rollback)
generated/failed/awaiting/approved → rejected or cancelled
new revision → previous nonactive version superseded
```

An executable `tested` result is stored as a passed immutable validation run; the version immediately enters awaiting_approval. It is never activated automatically. Rejected/cancelled versions cannot be approved directly; changes require a revision with new tests and approval.

The model's suggested tests are displayed but are not independent proof. A human must review/edit an acceptance suite, check the explicit review box and save it. The immutable suite requires distinct labels and all five categories: valid, boundary, invalid, malformed and security. Valid/boundary cases require exact expected output objects; other cases must genuinely fail the declared host input contract. Invalid inputs are rejected **before** reaching generated code. Static attack tests separately flag source attempts at filesystem, secrets, network, shell, package installation and introspection. Runtime OS probes are separate opt-in tests.

Validation checks manifest/dialect, finite JSON, code syntax, imports, prohibited names/attributes, entry signature, top-level side effects, decorators/defaults/annotations and bounded source formatting (200 lines / 120 columns). These structural/style checks neither execute code nor establish safety. No candidate-specific Black/Ruff dependency is installed; the application code itself is checked with the existing development tools.

Schema dialect: object, array, string, integer, number, boolean or null; enum, description, required/properties/additionalProperties=false, array items and min/maxItems, string min/maxLength, numeric minimum/maximum. Depth <=5, object properties <=20, arrays <=100 items, strings <=4000 characters. Type-specific keywords only; no external references, arbitrary validators, patterns or unions. JSON booleans are not numeric arguments; nonfinite values are rejected. Candidate tools accept/return objects. Dependencies are limited to math, statistics, decimal, json, re and collections, all from the trusted runtime's standard library. Only `pure_json` permission exists.

Validation persists source hash, artifact hash, suite hash, cases, actual outputs, captured execution data, policy/image and comparison. An exit code of zero alone cannot pass: all expected JSON results and schemas must match. Timeouts, crashes, output overflow and malformed output fail. Missing isolation/contract persists a blocked run. Runs and decisions are immutable SQL records.

Revisions append source/manifest versions. Source SHA-256 hashes exact UTF-8 source; artifact SHA-256 hashes canonical JSON containing source plus manifest. Changing only metadata/schema/dependencies also invalidates the old review for the new version. Old tested/approved records are historical and never transferred. An already active old version remains usable until explicitly replaced/deactivated, rather than breaking the working tool during revision.

## Approval and registry

The browser obtains a per-backend-process capability through a POST review session. Review requests require an exact local Origin (localhost/127.0.0.1, port 5173 or 8000), local Host, a custom intent header, the random session capability and bounded validated JSON. Cross-site pages cannot obtain it through CORS; cross-origin/missing/forged-capability requests fail in the backend. The model has no review HTTP/tool capability, and candidates have neither network nor database mounts.

**This is a local browser intent/CSRF boundary, not a login or proof of biological humanness.** A trusted same-user OS process can forge HTTP headers. That actor, the local Docker daemon/runtime image, and the Windows account are within the trust boundary. Do not expose the API or grant untrusted apps desktop/OS access. There is no multiuser authorization or cryptographic human attestation. Session capabilities are not persisted or supplied to Ollama/candidates; they expire on backend restart (reopen the page for a fresh session).

Approve, activate and rollback independently enforce exact candidate/version/source/artifact binding, a passing reviewed suite, current sandbox policy/image and prior explicit approval. Activation and rollback require UI confirmations displaying IDs/hashes. Reasons and actor `local-human` are audited. Agent-driven verification does not approve/activate a live candidate; automated tests use isolated fixture databases and a labelled nonexecuting test adapter.

`active_tools` is an atomic registry pointer. Its change, both version states, decision and activation audit commit in one transaction. An audit/pointer failure rolls everything back; the previous active tool remains registered. Registry entries distinguish fixed built-ins from active approved generated versions; all other candidate states are visible on Self-Development. Startup reads metadata only. Runtime rechecks approval/tests/active pointer and validates output, then calls the sandbox for that exact source.

Generated tools have a `candidate_` prefix and cannot overwrite/shadow any built-in. Built-in replacements are blocked because no independently reviewed comparable extension contract has been established for them. Supported replacements are new versions of an existing generated tool, with `replaces` equal to its name. They must retain every prior contract case unchanged and pass an extension case where the active baseline fails; both versions use the same reviewed inputs. Comparison records exact-output case counts and baseline ID. Performance claims based only on noisy timing are not accepted. If the baseline cannot execute, comparison is inconclusive and cannot authorize replacement.

Rollback targets a different previously active approved/tested version with recorded activation history and the same tested runtime identity. It can select an older inactive version despite a newer source revision. Deactivation removes only the generated pointer. Built-ins always remain present. Tests demonstrate transaction/gate/rollback behavior using **test doubles**, not a claim of live sandbox execution.

## Exact Docker adapter requirements

Enabling execution is a deliberate operator step, **not needed to run the website**. Nothing here installs Docker or downloads an image. First independently review the adapter/image and ensure sufficient RAM. Required:

1. Docker Desktop already installed with a running **Linux** engine, `desktop-linux` context using a local named-pipe endpoint, default seccomp enabled. Remote daemons, Windows containers and generic WSL subprocesses are rejected.
2. An already present, trusted, secret-free Linux image with Python and the standard library (`resource` included), no declared volumes, no unexpected image environment keys, no application credentials/data. Use a reviewed minimal Python runtime with no added packages. Pin its actual local `sha256:<64 hex>` image ID. The adapter uses `--pull=never`; tags are rejected.
3. Explicitly set SANDBOX_IMAGE and SANDBOX_ENABLED=true in the root .env, then restart FastAPI. With the flag off, no Docker inspection or candidate execution occurs.
4. Run the opt-in OS probes below successfully before using human-reviewed candidate contracts. Actual host isolation is **not validated on the current machine**.

Read-only prerequisite inspection (does not download/install anything):

```powershell
Get-Command docker
docker --context desktop-linux context inspect desktop-linux
docker --context desktop-linux info
docker --context desktop-linux image ls --no-trunc
```

Copy the ID of an image you already reviewed into `.env`; do not use a placeholder/tag:

```text
SANDBOX_ENABLED=true
SANDBOX_IMAGE=sha256:<actual-existing-reviewed-local-image-id>
SANDBOX_TIMEOUT_SECONDS=5
```

Isolation flags and behavior (see Docker's [run reference](https://docs.docker.com/reference/cli/docker/container/run/)):

- Docker Desktop Linux VM/container, unprivileged UID/GID 65534, all capabilities dropped, no-new-privileges, default seccomp, separate PID namespace, 32-process cap, one CPU, 256 MiB memory with no extra swap, 64 open descriptors, no IPC/shared memory, no Docker socket, device or privileged flags, no published ports and `--network=none`.
- Read-only root image and one newly prepared per-run fixture mount containing only source, application-owned harness and JSON inputs. No project/data/workspace directory is mounted; no SQLite, credentials, other candidates or expected outputs are exposed. Image environment is reviewed and restricted to standard Python runtime keys; host secrets/proxies/Docker environment overrides are not inherited by the candidate.
- Fresh 8 MiB noexec/nosuid/nodev tmpfs `/output` is its only general writable workspace; no output directory is mounted back to Windows. Per-run staging uses a random directory in the OS temp location and is removed afterwards.
- Harness imposes 3 CPU seconds, 240 MiB address-space limit, 1 MiB file limit, no core dumps and 8192-character candidate stdout/stderr capture. Host adapter caps each CLI output stream at 32 KiB and enforces the configured wall timeout. JSON stdout/stderr, exit status, elapsed time, timeout and overflow flags are saved with the run.
- Timeout/overflow kills the CLI **and explicitly removes the named container**. A failed cleanup blocks further adapters in this backend process; inspect/remove the recorded `evolve-candidate-*` container before restarting. Normal completed containers use `--rm`.

Limits: containers share the Linux VM kernel; this is not a dedicated VM per candidate, an audited malware sandbox, or protection against Docker/kernel vulnerabilities. Static restrictions can be bypassed by hostile Python; container isolation is the primary boundary. Candidate code can see its own image files and fixture inputs, and can write within its output tmpfs. A trusted image is essential. No generic fallback to Python-level restrictions exists. Docker command construction and error paths have offline coverage, but the actual Windows/Docker combination remains unverified here. Keep execution disabled until its OS probes and a review of local constraints succeed.

Optional OS probes, separate from inference:

```powershell
Set-Location 'D:\evolve ai\backend'
$env:EVOLVE_RUN_SANDBOX_TEST = '1'
.\.venv\Scripts\python.exe -m pytest -q -m sandbox
Remove-Item Env:EVOLVE_RUN_SANDBOX_TEST
```

They deliberately submit isolation probes directly to the OS adapter (bypassing production static checks) to check host-file/secrets/network denial, CPU deadline and memory limits. They never execute probe Python on Windows. Missing prerequisites **fail** an explicitly enabled probe run; the normal offline suite skips these tests.

## Persistence, retention and manual verification

Schema v3 appends 12 tables to the existing SQLite DB: task_evidence, evaluations, feedback, gaps, candidates, candidate_versions, candidate_states, contracts, validation_runs, decisions, active_tools and activation_audit. Existing v1/v2 tables are unchanged. Sources/contracts/runs/decisions/audit use immutable UPDATE/DELETE triggers; mutable lifecycle rows and active pointers are separate. Source/test/review history has no automatic deletion. This is plaintext local data; source and raw model-output records can contain deliberate user-supplied test data, so protect data/backups.

The migration-copy script verifies exact Phase 3 row hashes and SQLite integrity/foreign keys before touching the live installation. A consistent populated v2 backup remains in this installation's ignored data folder. App migrations are transactional/idempotent; future schema versions stop startup rather than resetting data. No candidate source file is written into backend/app/tools. Temporary sandbox directories are outside the repo, and data/, workspace/ and generated_tools/ remain ignored.

Browser checks:

1. Start Ollama/backend/frontend with the unchanged README PowerShell commands. Open http://127.0.0.1:5173.
2. Open the old conversations, preferences and Files & reports; verify saved report downloads/CSV handles remain available.
3. Open Self-Development. Confirm real evaluation counts, unknown legacy fields and the review-only banner. Refresh evidence; inspect task/event IDs and verification scope.
4. In a **new verification conversation**, request a calculator divide by zero and inspect the actual tool error. This deliberate validation failure creates a tentative one-occurrence gap; it is not a naturally recurring user failure or demonstrated improvement.
5. Generate a review candidate from its gap using real qwen2.5:3b. Inspect actual status/source/manifest/hashes. Failure records must remain visible. Validate it; absent a reviewed contract/sandbox, a blocked/failed record must appear and approval/activation must remain disabled.
6. Open Reject/Cancel confirmation and cancel it to verify dialogs without recording a human decision. Reload/restart and inspect the same saved candidate/run.
7. At a narrow/mobile viewport, verify navigation, wrapped hashes/source and lack of horizontal page overflow. Restore the default viewport afterwards.
8. Only after actual isolation validation, a human may review an acceptance contract, validate, approve and separately confirm activation. A candidate revision must require new tests/approval. Check measurable replacement cases, transactional failure and rollback using a disposable test installation before using those controls with personal workflows.

No next phase has been started. Full Phase 4 runtime acceptance (live isolated execution, activation/rollback and a demonstrated tool improvement) remains pending on this machine; review-only generation and static validation do not satisfy those runtime claims.
