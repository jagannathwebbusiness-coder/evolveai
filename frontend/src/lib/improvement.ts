import { request } from "./api";

export type Evaluation = {
  task_id: string;
  outcome: string;
  requested_capability: string;
  status: string;
  tool_calls: number;
  validation_failures: number;
  retry_count: number | null;
  duration_ms: number | null;
  error_category: string | null;
  final_answer_produced: boolean;
  feedback: string | null;
  verification: unknown[];
  event_references: unknown[];
};
export type Gap = {
  id: string;
  capability: string;
  error_category: string;
  occurrences: number;
  minimum_evidence: number;
  status: string;
  risk: string;
  proposed_improvement: string;
  expected_benefit: string;
  evidence: unknown[];
};
export type Version = {
  id: string;
  candidate_id: string;
  version: number;
  manifest: Record<string, unknown> | null;
  source: string;
  source_hash: string;
  content_hash: string;
  status: string;
  error: string | null;
  runs: {
    id: string;
    state: string;
    source_hash: string;
    created_at: string;
    payload: Record<string, unknown>;
  }[];
  decisions: unknown[];
  contract: { cases: unknown[]; suite_hash: string; actor: string } | null;
};
export type Candidate = { id: string; gap_id: string; versions: Version[] };
export type Improvement = {
  evaluations: Evaluation[];
  gaps: Gap[];
  candidates: {
    id: string;
    name: string | null;
    version: number;
    status: string;
  }[];
  active: {
    name: string;
    version_id: string;
    version: number;
    source_hash: string;
  }[];
  audit: unknown[];
  sandbox: {
    available: boolean;
    reason: string;
    policy: string;
    timeout_seconds: number;
  };
  demo: {
    available: boolean;
    enabled: boolean;
    reason: string;
    policy: string;
    timeout_seconds: number;
    secure_sandbox: false;
  };
  limitations: string[];
};
const headers = {
  "Content-Type": "application/json",
  "X-Evolve-Review": "human-review-v1",
};
export async function reviewSession() {
  return (
    await request<{ token: string }>("/improvement/review-session", {
      method: "POST",
      headers,
    })
  ).token;
}
export function reviewRequest<T>(
  path: string,
  token: string,
  body?: unknown,
  timeout = 30_000,
) {
  return request<T>(
    `/improvement${path}`,
    {
      method: "POST",
      headers: { ...headers, "X-Evolve-Review-Token": token },
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    },
    timeout,
  );
}
export function binding(version: Version) {
  return {
    version_id: version.id,
    source_hash: version.source_hash,
    content_hash: version.content_hash,
  };
}
