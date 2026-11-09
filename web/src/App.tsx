import { useCallback, useEffect, useState } from "react";
import { AppShell } from "@astryxdesign/core/AppShell";
import { Button } from "@astryxdesign/core/Button";
import { Selector } from "@astryxdesign/core/Selector";
import { SideNav, SideNavItem } from "@astryxdesign/core/SideNav";
import type { RunCatalog, Snapshot } from "./api";
import { number, terminal, url, useResource } from "./api";
import { Empty, FetchState, IdentityMark, Status } from "./shared";
import { AgentWorkspace, FileExplorer, TicketTable } from "./workspace";
import { Communications, ConfigurationPanel, Network, SystemPanel } from "./views";
import { RunTimeline } from "./run-timeline";

const pages = [
  ["timeline", "Timeline", "M12 3v18M5 6h3M16 12h3M5 18h3M9 6a3 3 0 1 0 6 0M9 12a3 3 0 1 0 6 0M9 18a3 3 0 1 0 6 0"],
  ["overview", "Overview", "M3 3h7v7H3zM14 3h7v7h-7zM3 14h7v7H3zM14 14h7v7h-7z"],
  ["agents", "Agents", "M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2M9 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8ZM22 21v-2a4 4 0 0 0-3-3.87M16 3.13a4 4 0 0 1 0 7.75"],
  ["files", "Project files", "M3 7V4h6l3 3h9v13H3ZM3 10h18"],
  ["communications", "Communications", "M21 15a3 3 0 0 1-3 3H8l-5 4V5a3 3 0 0 1 3-3h12a3 3 0 0 1 3 3ZM7 7h10M7 12h7"],
  ["network", "Network", "M12 8V5M5 18v-5h14v5M12 13V8M9 2h6v6H9zM2 18h6v4H2zM16 18h6v4h-6z"],
  ["configuration", "Configuration", "M4 5h16M4 12h16M4 19h16M8 2v6M16 9v6M9 16v6"],
  ["systems", "Runtime & systems", "M3 3h18v7H3zM3 14h18v7H3zM7 6.5h.01M7 17.5h.01M11 6.5h6M11 17.5h6"],
] as const;

export function App({ mode, changeMode }: { mode: "light" | "dark"; changeMode: () => void }) {
  const parameters = new URLSearchParams(window.location.search);
  const [run, setRun] = useState(parameters.get("run") || "");
  const [page, setPage] = useState(parameters.get("view") || "timeline");
  const [actor, setActor] = useState(parameters.get("actor") || "");
  const requestedAt = parameters.get("at");
  const [at, setAt] = useState<number | null>(
    requestedAt !== null && /^\d+$/.test(requestedAt) ? Number(requestedAt) : null,
  );
  const catalog = useResource<RunCatalog>(url("runs"), 15000);
  const view = useResource<Snapshot>(run && page !== "timeline" ? url("view", { run, at }) : null, at === null ? 5000 : 0);
  const snapshot = view.data;
  const live = Boolean(snapshot && !terminal(snapshot.state.status) && at === null);
  useEffect(() => { if (!run && catalog.data) setRun(catalog.data.selected); }, [run, catalog.data]);
  useEffect(() => {
    const query = new URLSearchParams(window.location.search);
    if (run) query.set("run", run);
    query.set("view", page);
    if (actor) query.set("actor", actor); else query.delete("actor");
    if (at !== null) query.set("at", String(at)); else query.delete("at");
    window.history.replaceState(null, "", `?${query}`);
  }, [run, page, actor, at]);
  const openAgent = useCallback((id: string) => { setActor(id); setPage("agents"); }, []);
  const current = catalog.data?.runs.find((item) => item.id === run);
  const agents = Object.entries(snapshot?.state.agents || {});
  const selectedActor = actor in (snapshot?.state.agents || {}) ? actor : agents[0]?.[0] || "";
  const selectRun = (id: string) => { setRun(id); setAt(null); setActor(""); };
  const topNav = <header className="top-nav">
    <div className="brand">
      <svg className="brand-mark" viewBox="0 0 40 40" fill="none" aria-hidden="true">
        <path d="M20 5 33 12.5v15L20 35 7 27.5v-15L20 5Z" stroke="currentColor" strokeWidth="1.5" />
        <path d="m7 12.5 13 8 13-8M20 20.5V35" stroke="currentColor" strokeWidth="1.5" />
        <circle cx="20" cy="5" r="3" fill="currentColor" />
        <circle cx="7" cy="27.5" r="3" fill="currentColor" />
        <circle cx="33" cy="27.5" r="3" fill="currentColor" />
        <circle cx="20" cy="20.5" r="3.5" fill="currentColor" />
      </svg>
      <strong>eusociality<span className="brand-period">.</span></strong>
    </div>
    <div className="run-picker"><Selector label="Run" value={run} onChange={selectRun} hasSearch options={(catalog.data?.runs || []).map((item) => ({
      value: item.id, label: item.id, description: `${item.status} / ${item.verified ?? 0}/${item.total ?? 0} verified / ${item.population ?? 0} agents`,
    }))} /></div>
    <Button label={`Switch to ${mode === "dark" ? "light" : "dark"} theme`} variant="ghost" isIconOnly
      icon={<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true">
        {mode === "dark" ? <><circle cx="12" cy="12" r="4" /><path d="M12 2v2m0 16v2M2 12h2m16 0h2M5 5l1.5 1.5m11 11L19 19M5 19l1.5-1.5m11-11L19 5" /></> : <path d="M20.5 14A9 9 0 0 1 10 3.5 9 9 0 1 0 20.5 14Z" />}
      </svg>} onClick={changeMode} />
  </header>;
  const sideNav = <SideNav header={<div className="nav-heading">Experiment</div>}>
    {pages.map(([id, label, path]) => <SideNavItem key={id} label={label} isSelected={page === id} onClick={() => setPage(id)}
      icon={<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={path} /></svg>} />)}
    {agents.length > 0 && <div className="nav-heading">Organization <span className="nav-count">{agents.length}</span></div>}
    {agents.map(([id, profile]) => <SideNavItem key={id} label={id} isSelected={page === "agents" && selectedActor === id}
      onClick={() => openAgent(id)} endContent={<span className="level-label">{profile.level}</span>} />)}
  </SideNav>;
  return <div className="lab"><AppShell topNav={topNav} sideNav={sideNav} height="fill" contentPadding={0} variant="wash">
    <div className="workspace-content">
      <FetchState error={catalog.error} /><FetchState {...view} />
      {page === "timeline" && run && <RunTimeline key={run} run={run} at={at} selectEvent={setAt} />}
      {snapshot && <>
        <header className="run-header"><div><span className="eyebrow">{current ? `${current.coordination || "Allocation not recorded"} / ${current.enforcement || "Enforcement not recorded"}` : "Loading run metadata..."}</span>
          <h1>{run.split("/").at(-1)}</h1></div>
          <Status value={snapshot.state.status} />
          {snapshot.state.interventions?.length ? <span className="notice-inline">Intervened run</span> : null}
          <div className="replay-controls">
            <label htmlFor="replay">Replay / #{snapshot.cursor}</label>
            <input id="replay" aria-label="Replay event" type="range" min={1} max={Math.max(1, snapshot.latest)} value={snapshot.cursor} onChange={(event) => setAt(Number(event.target.value))} />
            <Button label={at === null ? "Latest" : "Back to latest"} variant="ghost" size="sm" onClick={() => setAt(null)} />
          </div>
        </header>
        {!snapshot.historical_state_exact && <p className="notice">Legacy recording: historical state is not exact. Do not infer time-local behavior from this replay.</p>}
        {page === "overview" && <Overview snapshot={snapshot} openAgent={openAgent} showFiles={() => setPage("files")} />}
        {page === "agents" && (selectedActor ? <AgentWorkspace key={`${run}:${selectedActor}`} run={run} actor={selectedActor} at={at} live={live} /> : <Empty title="No initialized agents at this replay point" />)}
        {page === "files" && <><h2>Project files</h2><FileExplorer key={run} run={run} at={at} /></>}
        {page === "communications" && <Communications key={run} run={run} at={at} live={live} actors={agents.map(([id]) => id)} openAgent={openAgent} />}
        {page === "network" && <Network snapshot={snapshot} openAgent={openAgent} mode={mode} />}
        {page === "configuration" && <ConfigurationPanel key={run} run={run} />}
        {page === "systems" && <SystemPanel run={run} live={live} />}
      </>}
    </div>
  </AppShell></div>;
}

function Overview({ snapshot, openAgent, showFiles }: { snapshot: Snapshot; openAgent: (actor: string) => void; showFiles: () => void }) {
  const tickets = Object.values(snapshot.state.tickets || {}).filter((task) => task.status !== "withdrawn");
  const complete = tickets.filter((ticket) => ticket.status === "complete").length;
  const usage = Object.values(snapshot.usage);
  const reported = usage.some((item) => item.ai_credits !== null);
  const credits = usage.reduce((total, item) => total + (item.ai_credits || 0), 0);
  const partial = usage.some((item) => item.ai_credits === null || !item.credits_complete);
  return <div className="panel-stack">
    {snapshot.state.goal && <section className="notice">
      <div className="toolbar"><h2>Shared goal</h2><Status value={snapshot.state.goal_status || "open"} /></div>
      <p>{snapshot.state.goal}</p>
      {Object.values(snapshot.state.goal_proposals || {}).map((proposal) => <details key={proposal.id}>
        <summary>{proposal.actor} / goal assessment / {proposal.status}</summary>
        <ul>{proposal.evidence.map((item, index) => <li key={index}>{item}</li>)}</ul>
        {Object.entries(proposal.reviews).map(([actor, review]) => <p key={actor}>
          <strong>{actor}: {review.approve ? "approved" : "rejected"}</strong> {review.comment}
        </p>)}
      </details>)}
    </section>}
    <div className="metric-grid">
      <article className="metric"><span>Verified completion</span><strong>{complete}<small> / {tickets.length}</small></strong><progress value={complete} max={Math.max(tickets.length, 1)} />      <span>{complete === tickets.length && tickets.length ? "All tasks verified" : tickets.length ? "Work in progress" : "Agents are defining work"}</span></article>
      <article className="metric"><span>Agent identities</span><strong>{Object.keys(snapshot.state.agents || {}).length}</strong><span>{Object.values(snapshot.state.agents || {}).filter((agent) => agent.role === "coordinator").length} coordinators included</span></article>
      <article className="metric"><span>Reported AI credits</span><strong>{reported ? number(credits, 2) : "-"}</strong><span>{reported ? partial ? "Partial accounting" : "SDK-reported usage" : "Not reported"}</span></article>
      <article className="metric"><span>Institutional denials</span><strong>{snapshot.rule_summary.enforced_denials}</strong><span>{snapshot.rule_summary.advisory_violations} advisory violations</span></article>
    </div>
    <div className="section-title"><h2>Organization</h2><span className="muted">{Object.keys(snapshot.state.agents || {}).length} agents</span></div>
    <div className="agent-grid">{Object.entries(snapshot.state.agents || {}).map(([id, profile]) => <button className="agent-card" key={id} onClick={() => openAgent(id)}>
      <IdentityMark actor={id} /><div><strong>{id}</strong><span>{profile.role} / {profile.level}</span></div>
      <div className="card-value"><strong>{profile.verified}</strong><span>verified</span></div>
      <div className="agent-card-footer"><span>{number(snapshot.usage[id]?.ai_credits, 2)} credits</span><span>View agent &rarr;</span></div>
    </button>)}</div>
    <div className="section-title"><h2>Work board</h2><Button label="Browse project files" size="sm" onClick={showFiles} /></div>
    <TicketTable tickets={tickets} />
    <details className="advanced"><summary>Advanced / event journal</summary>
      <div className="table-scroll"><table><thead><tr><th>Event</th><th>Actor</th><th>Type</th><th>Recorded summary</th></tr></thead>
        <tbody>{snapshot.timeline.map((event) => <tr key={event.seq}><td>#{event.seq}</td><td>{event.actor}</td><td>{event.label}</td><td>{event.summary}</td></tr>)}</tbody></table></div>
    </details>
  </div>;
}
