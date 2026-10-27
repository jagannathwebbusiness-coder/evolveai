import { useEffect, useState } from "react";
import type { ActivityEvent } from "../lib/api";

type Stage =
  | "setup"
  | "decide"
  | "tool"
  | "verify"
  | "recover"
  | "build"
  | "done"
  | "fail";

const STAGE: Record<Stage, { label: string; icon: string }> = {
  setup: { label: "Plan", icon: "◎" },
  decide: { label: "Model decision", icon: "◆" },
  tool: { label: "Tool", icon: "⚙" },
  verify: { label: "Verified", icon: "✓" },
  recover: { label: "Recovery", icon: "↻" },
  build: { label: "Tool builder", icon: "✦" },
  done: { label: "Done", icon: "●" },
  fail: { label: "Failed", icon: "✕" },
};

const KIND_STAGE: Record<string, Stage> = {
  processing_request: "setup",
  strategy_evidence: "setup",
  tool_catalog_filtered: "setup",
  plan_created: "setup",
  goal_validation_failed: "fail",
  needs_input: "setup",
  model_request: "decide",
  current_step: "decide",
  batch_deferred: "decide",
  capability_resolved: "tool",
  tool_requested: "tool",
  tool_validated: "tool",
  tool_executed: "tool",
  tool_result_received: "tool",
  tool_failed: "fail",
  result_verified: "verify",
  goal_verified: "verify",
  completion_check: "verify",
  final_text_filtered: "verify",
  reflection: "recover",
  replan: "recover",
  recovery_succeeded: "recover",
  retry: "recover",
  argument_decoding: "recover",
  tool_not_used: "recover",
  capability_gap: "build",
  candidate_generation: "build",
  candidate_formatted: "build",
  candidate_persisted: "build",
  candidate_tests: "build",
  candidate_activated: "build",
  candidate_failed: "fail",
  tool_spec_drafted: "build",
  candidate_determinism: "build",
  tests_adjudicated: "build",
  approval_required: "done",
  result_unverified_tool: "verify",
  strategy_saved: "done",
  final_answer: "done",
  failure: "fail",
  iteration_limit: "fail",
};

const stageOf = (event: ActivityEvent): Stage =>
  KIND_STAGE[event.kind] ?? "decide";

const title = (kind: string) =>
  kind.replaceAll("_", " ").replace(/^./, (c) => c.toUpperCase());

function useElapsed(live: boolean) {
  const [seconds, setSeconds] = useState(0);
  useEffect(() => {
    if (!live) return;
    setSeconds(0);
    const timer = window.setInterval(() => setSeconds((s) => s + 1), 1000);
    return () => window.clearInterval(timer);
  }, [live]);
  return seconds;
}

function planOf(events: ActivityEvent[]) {
  const plan = events.find((e) => e.kind === "plan_created")?.result as
    | { steps?: string[]; success_check?: string }
    | undefined;
  return Array.isArray(plan?.steps) ? plan : null;
}

export default function ActivityLog({
  events,
  live = false,
}: {
  events: ActivityEvent[];
  live?: boolean;
}) {
  const elapsed = useElapsed(live);
  if (!events.length && !live) return null;

  const count = (kind: string) => events.filter((e) => e.kind === kind).length;
  const modelCalls = count("model_request");
  const toolRuns = count("tool_executed");
  const toolFailures = count("tool_failed");
  const recoveries =
    count("reflection") + count("replan") + count("recovery_succeeded");
  const built = count("candidate_activated");
  const failed = events.some((e) =>
    ["failure", "iteration_limit"].includes(e.kind),
  );
  const finished = events.some((e) => e.kind === "final_answer");
  const plan = planOf(events);
  const rows = events.filter(
    (e) => !["model_request", "strategy_evidence"].includes(e.kind),
  );
  const state = failed
    ? "failed"
    : finished
      ? "complete"
      : live
        ? "running"
        : "partial";

  return (
    <section
      className={`workflow workflow-${state}`}
      aria-label="Agent workflow"
      aria-live={live ? "polite" : undefined}
    >
      <header className="workflow-head">
        <div>
          <strong>Agent workflow</strong>
          <span className={`workflow-state state-${state}`}>
            {state === "running" ? `running · ${elapsed}s` : state}
          </span>
        </div>
        <ul className="workflow-stats">
          <li>
            <b>{modelCalls}</b> model calls
          </li>
          <li>
            <b>{toolRuns}</b> tool runs
          </li>
          {toolFailures > 0 && (
            <li className="stat-warn">
              <b>{toolFailures}</b> rejected
            </li>
          )}
          {recoveries > 0 && (
            <li>
              <b>{recoveries}</b> recoveries
            </li>
          )}
          {built > 0 && (
            <li className="stat-build">
              <b>{built}</b> tool built
            </li>
          )}
        </ul>
      </header>

      {plan && (
        <div className="workflow-plan">
          <span className="workflow-plan-title">Plan from the model</span>
          <ol>
            {plan.steps!.map((step, index) => (
              <li key={index}>{step}</li>
            ))}
          </ol>
        </div>
      )}

      {live && !events.length && (
        <p className="workflow-wait">Waiting for the first model response…</p>
      )}

      <ol className="workflow-timeline">
        {rows.map((event) => {
          const stage = stageOf(event);
          const detail = event.arguments || event.result;
          return (
            <li key={event.sequence} className={`wf-row stage-${stage}`}>
              <span className="wf-dot" aria-hidden="true">
                {STAGE[stage].icon}
              </span>
              <div className="wf-body">
                <div className="wf-title">
                  <span className="wf-stage">{STAGE[stage].label}</span>
                  <strong>{title(event.kind)}</strong>
                  {event.tool && <code>{event.tool}</code>}
                  {event.iteration && <em>step {event.iteration}</em>}
                </div>
                <p>{event.message}</p>
                {detail && (
                  <details>
                    <summary>
                      {event.arguments ? "Inputs" : "Data"}
                      {event.arguments && event.result ? " & result" : ""}
                    </summary>
                    {event.arguments && (
                      <pre>{JSON.stringify(event.arguments, null, 2)}</pre>
                    )}
                    {event.result && (
                      <pre>{JSON.stringify(event.result, null, 2)}</pre>
                    )}
                  </details>
                )}
              </div>
            </li>
          );
        })}
        {live && (
          <li className="wf-row stage-live">
            <span className="wf-dot pulse" aria-hidden="true" />
            <div className="wf-body">
              <p>Working…</p>
            </div>
          </li>
        )}
      </ol>
      <p className="workflow-note">
        Real backend events only. Model reasoning is not shown. “Model decision”
        steps come from the model API; verification and recovery steps are
        enforced by the backend.
      </p>
    </section>
  );
}
