import { useState } from "react";
import { Button } from "@astryxdesign/core/Button";
import { ChatMessage, ChatMessageBubble, ChatToolCalls } from "@astryxdesign/core/Chat";
import { CodeBlock } from "@astryxdesign/core/CodeBlock";
import { Markdown } from "@astryxdesign/core/Markdown";
import { Selector } from "@astryxdesign/core/Selector";
import { Tab, TabList } from "@astryxdesign/core/TabList";
import { TreeList } from "@astryxdesign/core/TreeList";
import type { ReactNode } from "react";
import type { Activity, Agent, FileContent, FileEntry, Proposal, Snapshot, Workspace } from "./api";
import { number, text, time, url, useResource } from "./api";
import { DataCode, Empty, Facts, FetchState, IdentityMark, Status } from "./shared";

const safeMarkdown = {
  image: ({ alt }: { alt?: string }) => <span className="muted">[Image: {alt || "external image not loaded"}]</span>,
};

export function AgentWorkspace({ run, actor, at, live }: { run: string; actor: string; at: number | null; live: boolean }) {
  const [tab, setTab] = useState("conversation");
  const [offset, setOffset] = useState(0);
  const resource = useResource<Agent>(url("agent", { run, actor, at, limit: 100, offset }), live ? 5000 : 0);
  const agent = resource.data;
  return <>
    <FetchState {...resource} />
    {agent && <>
      <header className="agent-header">
        <IdentityMark actor={actor} />
        <div><h2>{actor}</h2><p className="muted">{agent.profile.role} / {agent.profile.level} / {agent.observed_models.join(", ") || "No model usage recorded"}</p></div>
        <div className="agent-stats"><strong>{agent.profile.verified}</strong><span>verified tickets</span></div>
        <div className="agent-stats"><strong>{number(agent.usage.ai_credits, 2)}</strong><span>AI credits{!agent.usage.credits_complete && " (partial)"}</span></div>
      </header>
      <TabList value={tab} onChange={setTab} hasDivider>
        <Tab value="conversation" label="Conversation & tools" />
        <Tab value="files" label="Agent files" />
        <Tab value="work" label="Work & proposals" />
        <Tab value="session" label="Session" />
      </TabList>
      {tab === "conversation" && <section className="conversation" aria-label={`${actor} conversation`}>
        <div className="toolbar"><span className="muted">{agent.total} recorded messages, tool calls & milestones</span>
          <Button size="sm" label="Older activity" isDisabled={!agent.has_more} onClick={() => setOffset(offset + 100)} />
          {offset > 0 && <Button size="sm" label="Latest activity" onClick={() => setOffset(0)} />}
        </div>
        {!agent.items.length && <Empty title="No activity recorded at this point" />}
        {agent.items.map((item) => <div className="activity-event" data-event-seq={item.seq} key={item.seq}><ActivityItem item={item} /></div>)}
      </section>}
      {tab === "files" && <FileExplorer run={run} actor={actor} at={at} workspaces={agent.workspaces} proposals={agent.proposals} initialSource={agent.workspaces.length ? `workspace:${agent.workspaces.at(-1)?.id}` : "project"} />}
      {tab === "work" && <div className="panel-stack">
        <h3>Assigned work</h3>
        <TicketTable tickets={agent.tickets} />
        <h3>Submitted proposals</h3>
        {!agent.proposals.length && <Empty title="No proposals submitted" />}
        {agent.proposals.map((proposal) => <ProposalCard key={proposal.id} proposal={proposal} run={run} at={at} />)}
      </div>}
      {tab === "session" && <div className="panel-stack">
        <Facts items={[
          ["Configured role", agent.profile.role], ["Institutional level", agent.profile.level],
          ["Score", agent.profile.score], ["Observed API calls", agent.usage.observed_calls],
          ["Input tokens", number(agent.usage.input_tokens)], ["Output tokens", number(agent.usage.output_tokens)],
          ["Observed models", agent.observed_models.join(", ") || "Not reported"],
          ["SDK session IDs", agent.sessions.map((session) => session.session_id).join(", ") || "Not recorded"],
          ["Workspace checkpoints", agent.workspaces.length],
        ]} />
        <p className="muted">Recorded SDK output only. Hidden reasoning is not exposed; a level is an imposed institution, not evidence of emergent leadership.</p>
        <details><summary>Recorded session events</summary>{agent.sessions.map((session) => <DataCode key={session.seq} title={`Session event #${session.seq}`} value={session} />)}</details>
      </div>}
    </>}
  </>;
}

function ActivityItem({ item }: { item: Activity }) {
  const metadata = `${time(item.time)} / event #${item.seq}`;
  if (item.kind === "message") {
    return <ChatMessage sender={item.sender} name={item.sender === "assistant" ? item.actor : "Harness input"} metadata={metadata}>
      <ChatMessageBubble variant={item.sender === "assistant" ? "ghost" : "filled"} width="100%">
        <Markdown density="compact" headingLevelStart={3} components={safeMarkdown}>{item.content}</Markdown>
        {item.truncated && <p className="muted">Display truncated at 100,000 characters; the original event remains recorded.</p>}
      </ChatMessageBubble>
    </ChatMessage>;
  }
  if (item.kind === "tool") {
    const args = item.arguments;
    const target = args && typeof args === "object" && !Array.isArray(args)
      ? args.description || args.command || args.path || args.operation : args;
    return <ChatMessage sender="assistant" metadata={metadata}>
      <ChatToolCalls calls={[{
        key: item.call_id,
        name: item.name,
        target: typeof target === "string" ? target.slice(0, 160) : undefined,
        status: item.status === "unrecorded" ? "error" : item.status,
        duration: item.duration_seconds === undefined ? undefined : `${number(item.duration_seconds, 1)}s`,
        resultDetail: <div className="tool-result">
          <DataCode title="Actual tool arguments" value={item.arguments} />
          {item.output !== null && item.output !== undefined ? <DataCode title="Actual tool output" value={item.output} />
            : <p className="muted">{item.status === "unrecorded" ? "Completion was not recorded. This is not a successful tool call." : "No result available at this replay cursor."}</p>}
          {item.error && <p role="alert" className="error">{item.error}</p>}
          {item.start_unrecorded && <p className="muted">Tool start was not present in this recording.</p>}
          {item.truncated && <p className="muted">Output display truncated; original remains in the event journal.</p>}
        </div>,
      }]} />
    </ChatMessage>;
  }
  return <div className="milestone"><span className="muted">{metadata}</span>
    <strong>{item.kind === "milestone" ? "Integration effects" : item.event.replaceAll("_", " ")}</strong>
    <details><summary>Recorded detail</summary><DataCode title="Recorded effect" value={item.kind === "milestone" ? item.effects : item.detail} /></details>
  </div>;
}

type TreeItem = { id: string; label: ReactNode; children?: TreeItem[]; isExpanded?: boolean };
function fileTree(entries: FileEntry[], selected: string, choose: (path: string) => void): TreeItem[] {
  const root: TreeItem[] = [];
  for (const entry of entries) {
    let siblings = root;
    let prefix = "";
    const parts = entry.path.split("/");
    parts.forEach((part, index) => {
      prefix += `${prefix ? "/" : ""}${part}`;
      const existing = siblings.find((item) => item.id === prefix);
      if (index < parts.length - 1) {
        const folder = existing ?? { id: prefix, label: part, children: [], isExpanded: true };
        if (!existing) siblings.push(folder);
        siblings = folder.children!;
      } else {
        siblings.push({ id: prefix, label: <button className={`file-link ${selected === entry.path ? "selected" : ""}`} disabled={!entry.readable} onClick={() => choose(entry.path)}>{part}</button> });
      }
    });
  }
  return root;
}

export function FileExplorer({ run, at, actor, workspaces = [], proposals = [], initialSource = "project" }: {
  run: string; at: number | null; actor?: string; workspaces?: Workspace[]; proposals?: Proposal[]; initialSource?: string;
}) {
  const [selection, setSelection] = useState(initialSource);
  const [path, setPath] = useState("");
  const [source, identifier] = selection.split(":");
  const parameters = { run, at, actor, source, snapshot: source === "workspace" ? identifier : undefined, proposal: source === "snapshot" ? identifier : undefined };
  const listing = useResource<{ files: FileEntry[]; revision: string | null; source: string }>(url("files", parameters));
  const file = useResource<FileContent>(path ? url("file", { ...parameters, path }) : null);
  const sourceOptions = [
    ...workspaces.slice().reverse().map((workspace) => ({ value: `workspace:${workspace.id}`, label: `${workspace.label} / ${workspace.file_count} files` })),
    { value: "project", label: "Published project (recorded Git revision)" },
    ...proposals.map((proposal) => ({ value: `snapshot:${proposal.id}`, label: `Proposal ${proposal.id.slice(0, 8)} / ${proposal.ticket}` })),
    { value: "prompts", label: "Rendered agent prompts" },
    { value: "prompt-source", label: "Frozen prompt templates" },
    { value: "workload", label: "Frozen workload inputs" },
    { value: "sources", label: "Recorded public research sources" },
  ];
  const checkpoint = workspaces.find((workspace) => workspace.id === identifier);
  return <section className="file-explorer" aria-label="File explorer">
    <div className="file-controls">
      <Selector label="File source" options={sourceOptions} value={selection} onChange={(value) => { setSelection(value); setPath(""); }} />
      <span className="muted">{listing.data?.files.length ?? 0} files{listing.data?.revision && ` / ${listing.data.revision.slice(0, 12)}`}</span>
    </div>
    {actor && !workspaces.length && <p className="notice">This older run has no private workspace captures. Published code, proposal snapshots and recorded tool output remain available.</p>}
    {checkpoint && <p className="muted">Read-only checkpoint captured at event #{checkpoint.seq}; not a live filesystem.{checkpoint.errors.length > 0 && ` ${checkpoint.errors.length} capture exclusions/errors.`}</p>}
    {checkpoint && checkpoint.errors.length > 0 && <details><summary>Capture exclusions/errors</summary><DataCode title="Capture details" value={checkpoint.errors} /></details>}
    <FetchState {...listing} />
    {listing.data && <div className="file-layout">
      <aside className="file-tree">
        <TreeList density="compact" variant="noGuides" items={fileTree(listing.data.files, path, setPath)} />
        {!listing.data.files.length && <Empty title="No retained files in this source" />}
      </aside>
      <div className="file-content">
        <FetchState {...file} />
        {!path && <Empty title="Select a file"><span>Browse actual retained files, not generated summaries.</span></Empty>}
        {file.data && <CodeBlock key={`${selection}:${path}`} title={file.data.path} code={file.data.content} language={file.data.language} hasLineNumbers width="100%" maxHeight="calc(100vh - 320px)" />}
      </div>
    </div>}
  </section>;
}

export function TicketTable({ tickets }: { tickets: Snapshot["state"]["tickets"] | Agent["tickets"] }) {
  const rows = Array.isArray(tickets) ? tickets : Object.values(tickets);
  if (!rows.length) return <Empty title="No assigned work at this point" />;
  return <div className="table-scroll"><table>
    <thead><tr><th>Ticket</th><th>Scope</th><th>Owner / completed by</th><th>State</th></tr></thead>
    <tbody>{rows.map((ticket) => <tr key={ticket.id}><td><strong>{ticket.id}</strong><p className="muted">{ticket.description}</p>
      {ticket.revision_of && <p className="muted">Revision of {ticket.revision_of}</p>}
      {ticket.acceptance && <details><summary>Acceptance & output files</summary>
        <ul>{ticket.acceptance.map((criterion, index) => <li key={index}>{criterion}</li>)}</ul>
        <p className="muted">{ticket.files?.join(", ")}</p>
        {ticket.checks?.map((check, index) => <code key={index}>{check.argv.join(" ")}</code>)}
      </details>}
    </td><td>{ticket.scope}</td><td>{ticket.completed_by || ticket.owner || "Unclaimed"}</td><td><Status value={ticket.status} /></td></tr>)}</tbody>
  </table></div>;
}

function ProposalCard({ proposal, run, at }: { proposal: Proposal; run: string; at: number | null }) {
  return <details className="proposal"><summary><strong>{proposal.ticket}</strong> <Status value={proposal.status} /> <span className="muted">{proposal.id.slice(0, 10)}</span></summary>
    {proposal.diff !== undefined && <CodeBlock title="Submitted diff" code={proposal.diff} isDiff language="diff" width="100%" maxHeight={400} />}
    {proposal.evaluation && <details><summary>Protected evaluation result</summary><DataCode title="Actual evaluator output" value={proposal.evaluation} /></details>}
    <FileExplorer run={run} at={at} proposals={[proposal]} initialSource={`snapshot:${proposal.id}`} />
  </details>;
}
