import { useEffect, useState } from "react";
import { request } from "../lib/api";

type Metrics = {
  tasks: number;
  task_success_rate: number | null;
  recovered_tasks: number;
  tool_accuracy: number | null;
  avg_execution_seconds: number | null;
  human_intervention_rate: number | null;
  tools_built: number;
  tools_active: number;
  lessons_learned: number;
  lessons_recalled: number;
  regression_rate: number | null;
  tools_improved: number;
  average_review_score: number | null;
  improvement: {
    before_tasks: number;
    before_success: number | null;
    after_tasks: number;
    after_success: number | null;
  } | null;
};

const pct = (v: number | null) => (v === null ? "—" : `${Math.round(v * 100)}%`);

export default function Metrics() {
  const [m, setM] = useState<Metrics | null>(null);
  useEffect(() => {
    request<Metrics>("/metrics")
      .then(setM)
      .catch(() => setM(null));
  }, []);
  if (!m) return null;
  const items: [string, string][] = [
    ["Task success rate", pct(m.task_success_rate)],
    ["Tool accuracy", pct(m.tool_accuracy)],
    ["Avg execution", m.avg_execution_seconds === null ? "—" : `${m.avg_execution_seconds}s`],
    ["Human intervention", pct(m.human_intervention_rate)],
    ["Tools built / active", `${m.tools_built} / ${m.tools_active}`],
    ["Lessons learned", `${m.lessons_learned} (recalled ${m.lessons_recalled}×)`],
    ["Regression rate", pct(m.regression_rate)],
    ["Tools improved", String(m.tools_improved)],
    ["Reviewer score", m.average_review_score === null ? "—" : `${m.average_review_score}/5`],
    [
      "Success before → after 1st tool",
      m.improvement
        ? `${pct(m.improvement.before_success)} → ${pct(m.improvement.after_success)}`
        : "—",
    ],
    ["Tasks recorded", String(m.tasks)],
  ];
  return (
    <section className="metrics" aria-label="Evaluation metrics">
      <h3>Evaluation metrics</h3>
      <ul>
        {items.map(([label, value]) => (
          <li key={label}>
            <span>{label}</span>
            <b>{value}</b>
          </li>
        ))}
      </ul>
    </section>
  );
}
