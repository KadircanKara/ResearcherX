export type RunStatus = "pending" | "running" | "completed" | "failed";

export interface Plan {
  sub_queries: string[];
  rationale: string;
}

export interface Finding {
  query: string;
  summary: string;
  sources: string[];
  attempts?: number;
  validated?: boolean;
  accepted_degraded?: boolean;
}

export interface Validation {
  query: string;
  verdict: "valid" | "invalid";
  reasons: string[];
  attempt: number;
}

export interface CritiqueIssue {
  claim: string;
  severity: "low" | "medium" | "high";
  note: string;
}

export interface Critique {
  issues: CritiqueIssue[];
  overall: "pass" | "revise";
}

export interface Step {
  id: string;
  kind: string;
  agent_name: string;
  input: Record<string, unknown>;
  output: Record<string, unknown>;
  created_at: string;
}

export interface Run {
  id: string;
  question: string;
  status: RunStatus;
  report: string | null;
  error: string | null;
  created_at: string;
  steps: Step[];
}

export type RunEvent =
  | { type: "status"; status: RunStatus }
  | { type: "agent_start"; agent: string; query?: string }
  | { type: "plan"; plan: Plan }
  | { type: "finding"; finding: Finding }
  | ({ type: "validation" } & Validation)
  | {
      type: "search_retry";
      old_query: string;
      new_query: string;
      attempt: number;
      max_attempts: number;
    }
  | { type: "report_delta"; text: string }
  | { type: "critique"; critique: Critique }
  | { type: "error"; message: string };
