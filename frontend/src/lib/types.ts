export type RunStatus = "pending" | "running" | "completed" | "failed";

export type User = { id: string; email: string; name: string; avatar_color: string };

export type Role = "owner" | "editor" | "commenter" | "viewer";

export interface Project {
  id: string;
  title: string;
  description: string | null;
  topic_keywords: string[];
  my_role: Role;
  counts: {
    members: number;
    papers: number;
    chats: number;
  };
  created_at: string;
  updated_at: string;
}

export interface Member {
  user: User;
  role: Role;
}

export interface ProjectDetail {
  project: Project;
  members: Member[];
  my_role: Role;
}

export interface Plan {
  sub_queries: string[];
  rationale: string;
}

export interface SourceSummary {
  url: string;
  summary: string;
}

export interface Finding {
  query: string;
  summary: string;
  sources: SourceSummary[];
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
  project_id: string | null;
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
  | { type: "report_reset" }
  | { type: "critique"; critique: Critique }
  | { type: "error"; message: string };

export interface ChatConversation {
  id: string;
  project_id: string;
  title: string;
  created_by: string;
  created_at: string;
  updated_at: string;
}

export interface ChatCitation {
  n: number;
  paper_id: string;
  title: string;
  chunk_index: number;
  snippet: string;
}

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  citations: ChatCitation[];
  created_at: string;
}

export interface ChatConversationDetail extends ChatConversation {
  messages: ChatMessage[];
}

export type ChatEvent =
  | { type: "thinking" }
  | { type: "retrieving"; paper_count: number; history_hits: number }
  | { type: "delta"; text: string }
  | { type: "done"; citations: ChatCitation[] }
  | { type: "error"; message: string };

export type PaperSource = "upload" | "link" | "manual";

export interface Paper {
  id: string;
  project_id: string;
  title: string;
  abstract: string | null;
  body: string | null;
  pdf_url: string | null;
  source: PaperSource;
  created_at: string;
}
