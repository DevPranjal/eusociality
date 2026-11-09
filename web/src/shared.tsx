import type { ReactNode } from "react";
import { Badge } from "@astryxdesign/core/Badge";
import { CodeBlock } from "@astryxdesign/core/CodeBlock";
import type { Json } from "./api";
import { text } from "./api";

export function IdentityMark({ actor }: { actor: string }) {
  const identifier = actor.match(/(\d+)$/)?.[1] || actor.slice(0, 2).toUpperCase();
  return <span className="identity-mark" aria-hidden="true">{identifier}</span>;
}

export function Status({ value }: { value: string }) {
  const variant = ["completed", "complete", "integrated"].includes(value) ? "success"
    : ["failed", "error", "identity_mismatch", "inspection_error"].includes(value) ? "error"
    : ["running", "ready"].includes(value) ? "info"
    : ["stalled", "deadline", "activation_limit", "unrecorded"].includes(value) ? "warning" : "neutral";
  return <Badge label={value.replaceAll("_", " ")} variant={variant} />;
}

export function Empty({ title, children }: { title: string; children?: ReactNode }) {
  return <div className="empty"><strong>{title}</strong>{children && <p>{children}</p>}</div>;
}

export function FetchState({ error, loading }: { error?: string; loading?: boolean }) {
  if (error) return <div role="alert" className="error"><strong>Could not load this evidence</strong><p>{error}</p></div>;
  if (loading) return <p className="muted" role="status">Loading recorded evidence...</p>;
  return null;
}

export function DataCode({ title, value }: { title: string; value: Json | undefined }) {
  let code = text(value);
  let language = typeof value === "string" ? "plaintext" : "json";
  if (typeof value === "string" && /^[{[]/.test(value.trim())) {
    try {
      code = JSON.stringify(JSON.parse(value), null, 2);
      language = "json";
    } catch (error) {
      if (!(error instanceof SyntaxError)) throw error;
    }
  }
  return <CodeBlock title={title} code={code} language={language} width="100%" maxHeight={360} />;
}

export function Facts({ items }: { items: [string, ReactNode][] }) {
  return <dl className="facts">{items.map(([label, value]) =>
    <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl>;
}
