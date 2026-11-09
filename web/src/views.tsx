import { useEffect, useRef, useState } from "react";
import cytoscape from "cytoscape";
import { Avatar } from "@astryxdesign/core/Avatar";
import { Button } from "@astryxdesign/core/Button";
import { CodeBlock } from "@astryxdesign/core/CodeBlock";
import { Markdown } from "@astryxdesign/core/Markdown";
import { Selector } from "@astryxdesign/core/Selector";
import type { ConfigResponse, Configuration, Message, Snapshot, Systems } from "./api";
import { text, time, url, useResource } from "./api";
import { DataCode, Empty, Facts, FetchState, Status } from "./shared";

export function Communications({ run, at, live, actors, openAgent }: {
  run: string; at: number | null; live: boolean; actors: string[]; openAgent: (actor: string) => void;
}) {
  const [actor, setActor] = useState("");
  const resource = useResource<{ messages: Message[] }>(url("communications", { run, at, actor }), live ? 5000 : 0);
  return <section className="panel-stack">
    <div className="toolbar"><h2>Communications</h2>
      <Selector label="Participant" value={actor} onChange={setActor} options={[{ value: "", label: "Everyone" }, ...actors]} />
    </div>
    <FetchState {...resource} />
    {resource.data?.messages.length === 0 && <Empty title="No messages delivered at this point">Only real accepted messages are shown.</Empty>}
    {resource.data?.messages.map((message) => <article className="message-card" key={message.seq}>
      <header><Avatar name={message.from} size="sm" /><button className="text-button" onClick={() => openAgent(message.from)}>{message.from}</button>
        <span className="muted">to</span><strong>{message.to || message.channel || "public"}</strong>
        <span className="muted trailing">{time(message.time)} / #{message.seq}</span>
      </header>
      <Markdown density="compact" headingLevelStart={3} components={{ image: () => <span>[External image not loaded]</span> }}>{message.content || message.text || message.message || "No text field recorded"}</Markdown>
    </article>)}
  </section>;
}

export function Network({ snapshot, openAgent, mode }: { snapshot: Snapshot; openAgent: (actor: string) => void; mode: "light" | "dark" }) {
  const container = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!container.current) return;
    const css = getComputedStyle(document.documentElement);
    const color = (name: string) => css.getPropertyValue(name).trim();
    const graph = cytoscape({
      container: container.current,
      elements: [...snapshot.graph.nodes, ...snapshot.graph.edges],
      layout: { name: "cose", animate: false, padding: 48 },
      style: [
        { selector: "node", style: {
          label: "data(label)", "background-color": color("--cp-surface"), "border-color": color("--cp-accent"), "border-width": 2,
          color: color("--cp-text"), width: 64, height: 64, "text-valign": "center", "text-wrap": "wrap", "font-size": 11, "font-family": "DM Sans Variable",
        } },
        { selector: 'node[role = "channel"]', style: { shape: "round-rectangle", "border-color": color("--cp-border-strong") } },
        { selector: "edge", style: {
          label: "data(label)", width: 1.5, color: color("--cp-text-muted"), "line-color": color("--cp-border-strong"),
          "target-arrow-color": color("--cp-border-strong"), "target-arrow-shape": "triangle", "curve-style": "bezier", "font-size": 10,
          "text-background-color": color("--cp-bg"), "text-background-opacity": 1, "text-background-padding": "3px",
        } },
      ],
    });
    graph.on("tap", "node", (event) => {
      const actor = event.target.id();
      if (actor in snapshot.state.agents) openAgent(actor);
    });
    return () => graph.destroy();
  }, [snapshot.graph, snapshot.state.agents, openAgent, mode]);
  return <section className="panel-stack"><div className="toolbar"><h2>Observed network</h2><span className="muted">{snapshot.graph.edges.length} recorded interaction edges</span></div>
    <div className="network" ref={container} aria-label="Recorded agent communication and work transfer network" />
    <p className="muted">Messages, transfers, approvals and code synchronization only. Click an agent to open its workspace.</p>
    <div className="table-scroll"><table><thead><tr><th>From</th><th>To</th><th>Interaction</th><th>Count</th></tr></thead>
      <tbody>{snapshot.graph.edges.map(({ data }) => <tr key={data.id}><td>{text(data.source)}</td><td>{text(data.target)}</td><td>{text(data.kind)}</td><td>{text(data.count)}</td></tr>)}</tbody></table></div>
  </section>;
}

export function ConfigurationPanel({ run }: { run: string }) {
  const resolved = useResource<ConfigResponse>(url("config", { run }));
  const catalog = useResource<{ files: string[] }>(url("configs"));
  const [template, setTemplate] = useState("");
  const selected = useResource<{ yaml: string; configuration: Configuration }>(template ? url("config-file", { path: template }) : null);
  const [copyError, setCopyError] = useState("");
  const [copied, setCopied] = useState(false);
  const configuration = template ? selected.data?.configuration : resolved.data?.configuration;
  const code = (template ? selected.data?.yaml : resolved.data ? JSON.stringify(resolved.data.configuration, null, 2) : "") ?? "";
  const download = () => {
    const object = URL.createObjectURL(new Blob([code], { type: "text/plain" }));
    const anchor = document.createElement("a");
    anchor.href = object;
    anchor.download = template.split("/").at(-1) || `${run.replaceAll("/", "-")}-resolved.json`;
    anchor.click();
    URL.revokeObjectURL(object);
  };
  return <section className="panel-stack">
    <div className="toolbar"><h2>Configuration</h2>
      <Selector label="Configuration source" value={template} onChange={(value) => { setTemplate(value); setCopied(false); }} options={[
        { value: "", label: "This run / frozen resolved configuration" },
        ...(catalog.data?.files || []).map((path) => ({ value: path, label: `Template / ${path}` })),
      ]} hasSearch />
    </div>
    <FetchState {...(template ? selected : resolved)} /><FetchState error={catalog.error} />
    {configuration && <>
      {configuration.goal && <div className="notice"><strong>Society goal</strong><p>{configuration.goal}</p></div>}
      {template && <p className="notice">Previewing an actual YAML template. This does not edit this run or change its rules.</p>}
      <Facts items={[
        ["Population", configuration.population], ["Concurrent turns", configuration.concurrency],
        ["Allocation", configuration.coordination], ["Enforcement", configuration.enforcement],
        ["Integration freshness", configuration.integration_mode ?? "strict_base"],
        ["Scheduler", configuration.scheduler_mode ?? "fair"],
        ["Default model", configuration.runtime.model], ["Reasoning", configuration.runtime.reasoning_effort],
        ["Deadline", `${configuration.deadline_seconds}s`], ["Activation limit", configuration.max_activations],
        ["Per-session credit ceiling", configuration.runtime.max_ai_credits === null ? "No harness cap (provider limits apply)" : configuration.runtime.max_ai_credits], ["Recoveries", configuration.recoveries],
      ]} />
      <h3>Institutional rules</h3>
      <div className="rule-grid">{configuration.rules.map((rule) => <article className={`rule-card ${!rule.enabled ? "rule-disabled" : ""}`} key={rule.plugin}>
        <header><strong>{rule.plugin}</strong><Status value={rule.enabled ? "enabled" : "disabled"} /></header>
        {Object.keys(rule.parameters).length ? <Facts items={Object.entries(rule.parameters).map(([key, value]) => [key, text(value)])} /> : <span className="muted">No parameter overrides</span>}
      </article>)}</div>
      <h3>Levels & capabilities</h3>
      <div className="table-scroll"><table><thead><tr><th>Level</th><th>Verified completions</th><th>Active tasks</th><th>Scopes</th><th>Delegation</th></tr></thead>
        <tbody>{configuration.levels.map((level) => <tr key={level.name}><td>{level.name}</td><td>{level.completions}</td><td>{level.max_active}</td><td>{level.scopes.join(", ")}</td><td>{level.delegate ? "Allowed" : "Not granted"}</td></tr>)}</tbody></table></div>
      {configuration.initial_levels && Object.keys(configuration.initial_levels).length > 0 &&
        <Facts items={Object.entries(configuration.initial_levels).map(([actor, level]) => [`${actor} / starting level`, level])} />}
      {configuration.research && <Facts items={[
        ["Public research", "Available without website approval"],
        ["Source access", "Public HTTP(S) resources; recorded read-only requests"],
        ["Task creation", configuration.workload ? "Fixed baseline workload" : "Society agents during execution"],
      ]} />}
      {configuration.model_overrides && Object.keys(configuration.model_overrides).length > 0 && <DataCode title="Per-agent model overrides" value={configuration.model_overrides} />}
      <details><summary>Exact {template ? "YAML template" : "resolved JSON"} & download</summary>
        <div className="toolbar">
          <Button label={copied ? "Copied" : "Copy configuration"} size="sm" onClick={async () => {
            try { await navigator.clipboard.writeText(code); setCopied(true); setCopyError(""); }
            catch (error) { setCopyError(String(error)); }
          }} />
          <Button label="Download configuration" size="sm" onClick={download} />
        </div>
        {copyError && <p role="alert" className="error">{copyError}</p>}
        <CodeBlock title={template || "resolved.json"} code={code} language={template ? "yaml" : "json"} width="100%" maxHeight={480} />
      </details>
      <Facts items={[["Prompt repository", configuration.prompt_repository], ["Workload", configuration.workload]]} />
    </>}
  </section>;
}

export function SystemPanel({ run, live }: { run: string; live: boolean }) {
  const resource = useResource<Systems>(url("system", { run }), live ? 10000 : 0);
  return <section className="panel-stack">
    <h2>Runtime & systems</h2>
    <FetchState {...resource} />
    {resource.data && <>
      <Facts items={[["Recorded network", resource.data.network || "Not recorded"], ...Object.entries(resource.data.versions).map(([key, value]): [string, string] => [key, text(value)])]} />
      <div className="system-grid">{resource.data.agents.map((agent) => <article className="system-card" key={agent.actor}>
        <header><strong>{agent.actor}</strong><Status value={agent.status} /></header>
        <Facts items={[
          ["SDK session", agent.sessions.at(-1)?.session_id || "Not recorded"],
          ["Recorded container", agent.container || "Not recorded"],
          ["Live image", agent.live?.image || "Not available"],
          ["Live memory limit", agent.live ? `${agent.live.memory_bytes / 1024 / 1024} MiB` : "Not available"],
          ["Live CPU limit", agent.live ? agent.live.nano_cpus / 1e9 : "Not available"],
        ]} />
        {agent.detail && <p className="muted">{agent.detail}</p>}
      </article>)}</div>
      <p className="muted">Docker status is current, not replayed. Archived containers may have been removed; their SDK sessions and frozen image identities remain recorded.</p>
      <details><summary>Frozen image identities</summary><DataCode title="Recorded image identities" value={resource.data.images} /></details>
    </>}
  </section>;
}
