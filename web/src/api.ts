import { useEffect, useState } from "react";

export type Json = string | number | boolean | null | Json[] | { [key: string]: Json };
export type Profile = { role: string; score: number; verified: number; level: string };
export type Usage = {
  observed_calls: number;
  input_tokens: number | null;
  output_tokens: number | null;
  ai_credits: number | null;
  credits_complete: boolean;
};
export type Ticket = {
  id: string;
  description: string;
  scope: string;
  status: string;
  owner: string | null;
  completed_by?: string;
  module?: string;
  dependencies: string[];
  files?: string[];
  acceptance?: string[];
  checks?: { argv: string[] }[];
  created_by?: string;
  revision_of?: string | null;
};
export type Proposal = {
  id: string;
  actor: string;
  ticket: string;
  status: string;
  artifact: string;
  diff?: string;
  evaluation?: Json;
};
export type Run = {
  id: string;
  name: string;
  status: string;
  population: number;
  verified: number;
  total: number;
  coordination?: string;
  enforcement?: string;
  started_at?: number;
  error?: string;
};
export type RunCatalog = { runs: Run[]; selected: string };
export type GraphElement = { data: { id: string; label: string; [key: string]: Json } };
export type Snapshot = {
  cursor: number;
  latest: number;
  historical_state_exact: boolean;
  state: {
    status: string;
    agents: Record<string, Profile>;
    tickets: Record<string, Ticket>;
    proposals: Record<string, Proposal>;
    head: string;
    interventions?: Json[];
    goal?: string | null;
    goal_status?: string;
    goal_proposals?: Record<string, {
      id: string; actor: string; evidence: string[]; status: string;
      reviews: Record<string, { approve: boolean; comment: string }>;
    }>;
  };
  usage: Record<string, Usage>;
  graph: { nodes: GraphElement[]; edges: GraphElement[] };
  timeline: { seq: number; actor: string; time: number; kind: string; label: string; summary: string }[];
  counts: Record<string, number>;
  rule_summary: { enforced_denials: number; advisory_violations: number };
};
export type Session = {
  seq: number;
  session_id: string;
  container_name?: string;
};
export type Workspace = { id: string; seq: number; label: string; file_count: number; errors: Json[] };
type BaseActivity = { seq: number; time: number; actor: string };
export type Activity =
  | (BaseActivity & { kind: "message"; sender: "assistant" | "system"; content: string; truncated: boolean })
  | (BaseActivity & {
      kind: "tool"; call_id: string; name: string; arguments: Json;
      status: "running" | "complete" | "error" | "unrecorded"; output: Json;
      error?: string; truncated?: boolean; duration_seconds?: number; start_unrecorded?: boolean;
    })
  | (BaseActivity & { kind: "milestone"; effects: Json; passed: boolean })
  | (BaseActivity & { kind: "system"; event: string; detail: Json });
export type Agent = {
  actor: string; profile: Profile; usage: Usage; observed_models: string[];
  sessions: Session[]; workspaces: Workspace[]; tickets: Ticket[]; proposals: Proposal[];
  items: Activity[]; total: number; has_more: boolean; archived: boolean;
};
export type Message = {
  seq: number; time: number; from: string; to?: string | null;
  channel?: string; content?: string; text?: string; message?: string;
};
export type FileEntry = { path: string; readable: boolean };
export type FileContent = { path: string; content: string; language: string; revision: string | null };
export type PublishedFiles = {
  files: FileEntry[]; revision: string | null; cursor: number; historical_exact: boolean;
};
export type HistoryEvent = {
  seq: number; time: number; actor: string; kind: string; label: string; title: string;
  tone: "neutral" | "success" | "warning" | "error";
};
export type HistoryPage = {
  items: HistoryEvent[]; latest: number; total: number; offset: number; limit: number;
  has_more: boolean; started_at: number | null; actors: string[];
  goal: string | null; status: string;
};
export type EventDetail = { seq: number; time: number; actor: string | null; kind: string; body: Json };
export type Configuration = {
  name: string; population: number; concurrency: number; coordination: string; enforcement: string;
  deadline_seconds: number; max_activations: number; recoveries: number;
  integration_mode?: "strict_base" | "disjoint_files";
  scheduler_mode?: "fair" | "workflow";
  prompt_repository: string; workload: string | null; model_overrides?: Record<string, Json>;
  runtime: {
    model: string; reasoning_effort: string; max_ai_credits: number | null; turn_timeout_seconds: number;
    worker_image: string; proxy_image: string;
  };
  rules: { plugin: string; enabled: boolean; parameters: Record<string, Json> }[];
  levels: { name: string; completions: number; max_active: number; scopes: string[]; delegate: boolean }[];
  goal?: string | null;
  initial_levels?: Record<string, string>;
  agent_roles?: Record<string, string>;
  agent_parameters?: Record<string, Json>;
  resources?: Record<string, Json>;
  research?: { enabled: boolean; allowed_hosts: string[] };
};
export type ConfigResponse = { configuration: Configuration; metadata: Record<string, Json> };
export type Systems = {
  agents: {
    actor: string; sessions: Session[]; container: string | null; status: string; detail?: string;
    live?: { image: string; running: boolean; memory_bytes: number; nano_cpus: number };
  }[];
  images: Record<string, Json>; versions: Record<string, Json>; network: string | null;
};

export function url(route: string, parameters: Record<string, string | number | null | undefined> = {}) {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(parameters)) {
    if (value !== null && value !== undefined && value !== "") query.set(key, String(value));
  }
  return `/api/${route}?${query}`;
}

async function request<T>(path: string, signal: AbortSignal): Promise<T> {
  const response = await fetch(path, { signal });
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(`${response.status}: ${detail}`);
  }
  return (await response.json()) as T;
}

export function useResource<T>(path: string | null, interval = 0) {
  const [value, setValue] = useState<{ data?: T; error?: string; loading: boolean }>({ loading: true });
  useEffect(() => {
    const controller = new AbortController();
    setValue({ loading: Boolean(path) });
    if (!path) return () => controller.abort();
    let busy = false;
    const load = async () => {
      if (busy) return;
      busy = true;
      try {
        const data = await request<T>(path, controller.signal);
        if (!controller.signal.aborted) setValue({ data, loading: false });
      } catch (error) {
        if (!controller.signal.aborted) {
          setValue((previous) => ({ ...previous, loading: false, error: String(error) }));
        }
      } finally {
        busy = false;
      }
    };
    void load();
    const timer = interval ? window.setInterval(() => void load(), interval) : undefined;
    return () => { controller.abort(); window.clearInterval(timer); };
  }, [path, interval]);
  return value;
}

export const text = (value: Json | undefined) => typeof value === "string" ? value : JSON.stringify(value, null, 2) ?? "";
export const number = (value: number | null | undefined, digits = 0) =>
  value === null || value === undefined ? "Not reported" : value.toLocaleString(undefined, { maximumFractionDigits: digits });
export const time = (value: number) => new Date(value * 1000).toLocaleTimeString();
export const terminal = (status: string) => !["created", "ready", "running", "draining", "paused"].includes(status);
