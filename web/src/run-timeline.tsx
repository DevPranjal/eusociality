import { useState } from "react";
import { Clock3, FileText, History, ChevronLeft, ChevronRight, ArrowUpRight } from "lucide-react";
import { Markdown } from "@astryxdesign/core/Markdown";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Input } from "@/components/ui/input";
import { Separator } from "@/components/ui/separator";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import type { EventDetail, FileContent, HistoryPage, Json, PublishedFiles } from "./api";
import { terminal, url, useResource } from "./api";
import { FetchState } from "./shared";

function record(value: Json | undefined): { [key: string]: Json } {
  return value && typeof value === "object" && !Array.isArray(value) ? value : {};
}

function elapsed(timestamp: number, start: number | null) {
  if (start === null) return "—";
  const seconds = Math.max(0, Math.floor(timestamp - start));
  return `${Math.floor(seconds / 60).toString().padStart(2, "0")}:${(seconds % 60).toString().padStart(2, "0")}`;
}

export function RunTimeline({ run, at, selectEvent }: {
  run: string; at: number | null; selectEvent: (sequence: number | null) => void;
}) {
  const [offset, setOffset] = useState(0);
  const [actor, setActor] = useState("all");
  const [allEvents, setAllEvents] = useState(false);
  const [jump, setJump] = useState("");
  const [around, setAround] = useState<number | null>(at);
  const history = useResource<HistoryPage>(url("history", {
    run, offset, limit: 100, actor: actor === "all" ? null : actor,
    all_events: allEvents ? "true" : "false", around,
  }), at === null ? 5000 : 0);
  const page = history.data;
  const cursor = at ?? page?.latest ?? null;
  const detail = useResource<EventDetail>(
    at !== null ? url(`event/${at}`, { run }) : null,
  );
  const files = useResource<PublishedFiles>(url("files", { run, at }), at === null ? 5000 : 0);
  const visibleFiles = files.data && (at === null || files.data.cursor === at) ? files.data : undefined;
  const [chosen, setChosen] = useState("");
  const available = visibleFiles?.files.filter((file) =>
    file.path.startsWith("project/") || file.path.startsWith("operations/"),
  ) || [];
  const path = available.some((file) => file.path === chosen) ? chosen : "";
  const content = useResource<FileContent>(
    path && visibleFiles ? url("file", { run, at: visibleFiles.cursor, path }) : null,
  );
  const visibleContent = content.data?.revision === visibleFiles?.revision && content.data?.path === path
    ? content.data : undefined;
  const visibleDetail = detail.data?.seq === at ? detail.data : undefined;
  const live = page && !terminal(page.status);
  const body = record(visibleDetail?.body);
  const event = record(body.event);
  const data = record(event.data);
  const payload = record(body.payload);
  const result = record(body.result);
  const message = typeof data.content === "string" ? data.content
    : typeof payload.content === "string" ? payload.content : null;

  const movePage = (next: number) => { setOffset(next); setAround(null); };
  const focusEvent = (sequence: number) => {
    selectEvent(sequence);
  };
  return <section className="run-timeline-screen" aria-label="Run timeline">
    <header className="timeline-heading">
      <div><span className="eyebrow">Run replay</span><h1>{run.split("/").at(-1)}</h1></div>
      <Badge variant="outline" className={`timeline-status ${page?.status || ""}`}>
        {page?.status || "Loading"}
      </Badge>
      <Button variant="outline" size="sm" onClick={() => { selectEvent(null); setAround(null); }}>
        <History />{at === null ? (live ? "Following live" : "Final state") : "Back to final state"}
      </Button>
    </header>
    {page?.goal && <Card className="timeline-goal"><CardContent>
      <span className="eyebrow">Shared goal</span><p>{page.goal}</p>
    </CardContent></Card>}
    <div className="timeline-summary">
      <span><strong>{page?.total.toLocaleString() ?? "…"}</strong> {allEvents ? "recorded events" : "activity events"}</span>
      <span><strong>{page?.actors.filter((name) => name.startsWith("worker-")).length ?? "…"}</strong> agents</span>
      <span><strong>{cursor === null ? "…" : `#${cursor}`}</strong> selected state</span>
      <span><strong>{visibleFiles ? available.length : "…"}</strong> published {available.length === 1 ? "file" : "files"}</span>
    </div>
    <div className="timeline-columns">
      <Card className="timeline-event-panel">
        <CardHeader>
          <CardTitle className="timeline-card-title"><Clock3 /> Timeline</CardTitle>
          <CardDescription>Select an event to see the deliverables published by then.</CardDescription>
          <div className="timeline-toolbar">
            <Select value={actor} onValueChange={(value) => { setActor(value); setOffset(0); setAround(null); }}>
              <SelectTrigger aria-label="Filter events by agent"><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="all">Everyone</SelectItem>
                {page?.actors.map((name) => <SelectItem key={name} value={name}>{name}</SelectItem>)}
              </SelectContent>
            </Select>
            <Button variant={allEvents ? "secondary" : "outline"} size="sm"
              aria-pressed={allEvents} onClick={() => { setAllEvents(!allEvents); setOffset(0); setAround(null); }}>
              {allEvents ? "All events" : "Activity only"}
            </Button>
            <form className="event-jump" onSubmit={(submission) => {
              submission.preventDefault();
              const sequence = Number(jump);
              if (Number.isInteger(sequence) && sequence >= 1 && page && sequence <= page.latest) {
                focusEvent(sequence);
                setAround(sequence);
              }
            }}>
              <Input type="number" min="1" max={page?.latest} required disabled={!page} aria-label="Jump to event"
                placeholder="Event #" value={jump} onChange={(input) => setJump(input.target.value)} />
              <Button size="sm" variant="ghost" type="submit" disabled={!page} aria-label="Go to event"><ArrowUpRight /></Button>
            </form>
          </div>
        </CardHeader>
        <Separator />
        <ScrollArea className="timeline-event-scroll">
          <FetchState {...history} />
          {page?.items.map((item) => <button type="button" key={item.seq}
            data-timeline-event={item.seq} aria-pressed={at === item.seq}
            className={`timeline-event ${at === item.seq ? "selected" : ""} tone-${item.tone}`}
            onClick={() => focusEvent(item.seq)}>
            <span className="timeline-event-time">{elapsed(item.time, page.started_at)}</span>
            <span className="timeline-event-dot" aria-hidden="true" />
            <span className="timeline-event-text">
              <span className="timeline-event-meta"><strong>{item.actor}</strong>
                <Badge variant="secondary">{item.kind === "runtime" ? item.label.replace("tool.execution_", "tool ") : item.kind.replaceAll("_", " ")}</Badge>
                <span>#{item.seq}</span></span>
              <span className="timeline-event-description">{item.title}</span>
            </span>
          </button>)}
          {page && !page.items.length && <p className="timeline-empty">No events match this filter.</p>}
        </ScrollArea>
        <div className="timeline-pagination">
          <Button variant="ghost" size="sm" disabled={!page || page.offset === 0}
            onClick={() => movePage(Math.max(0, (page?.offset || 0) - 100))}><ChevronLeft /> Earlier</Button>
          <span>{page ? `${page.offset + (page.total ? 1 : 0)}–${page.offset + page.items.length} of ${page.total}` : "Loading…"}</span>
          <Button variant="ghost" size="sm" disabled={!page?.has_more}
            onClick={() => movePage((page?.offset || 0) + 100)}>Later <ChevronRight /></Button>
        </div>
      </Card>
      <div className="timeline-right">
        <Card className="timeline-deliverables">
          <CardHeader>
            <CardTitle className="timeline-card-title"><FileText /> Deliverables</CardTitle>
            <CardDescription>
              {at === null ? "Final published state" : `Published at event #${at}`}
              {visibleFiles?.revision && <span className="timeline-revision"> · {visibleFiles.revision.slice(0, 10)}</span>}
            </CardDescription>
          </CardHeader>
          <Separator />
          <FetchState {...files} />
          {visibleFiles?.historical_exact === false && <p className="notice">This legacy run has no exact historical publication record.</p>}
          <div className="timeline-file-grid">
            <ScrollArea className="timeline-file-list" aria-label="Deliverable files">
              {available.map((file) => <Button key={file.path} variant="ghost"
                className={`timeline-file ${path === file.path ? "selected" : ""}`}
                disabled={!file.readable} data-deliverable={file.path}
                aria-pressed={path === file.path} onClick={() => setChosen(file.path)}>
                <FileText /><span>{file.path.replace(/^(project|operations)\//, "")}</span>
              </Button>)}
              {visibleFiles && !available.length && <p className="timeline-empty">No deliverables had been published at this point.</p>}
            </ScrollArea>
            <div className="timeline-file-preview">
              <FetchState {...content} />
              {!path && <div className="timeline-empty"><FileText /><p>Select a deliverable to inspect its contents at this event.</p></div>}
              {visibleContent && <Tabs defaultValue="preview" key={path}>
                <div className="timeline-preview-heading"><strong>{path.split("/").at(-1)}</strong>
                  <TabsList><TabsTrigger value="preview">Preview</TabsTrigger><TabsTrigger value="source">Source</TabsTrigger></TabsList></div>
                <ScrollArea className="timeline-preview-scroll">
                  <TabsContent value="preview">
                    {visibleContent.language === "markdown" ? <Markdown density="compact" headingLevelStart={3}
                      components={{ image: ({ alt }: { alt?: string }) => <span>{alt || "Image not loaded"}</span> }}>
                      {visibleContent.content}</Markdown> : <pre>{visibleContent.content}</pre>}
                  </TabsContent>
                  <TabsContent value="source"><pre>{visibleContent.content}</pre></TabsContent>
                </ScrollArea>
              </Tabs>}
            </div>
          </div>
          <p className="timeline-file-note">Published files follow recorded Git revisions. Private edits between checkpoints cannot be reconstructed.</p>
        </Card>
        <Card className="timeline-detail">
          <CardHeader><CardTitle>{at === null ? "Event detail" : `Event #${at}`}</CardTitle>
            <CardDescription>{visibleDetail ? `${visibleDetail.actor || "system"} · ${visibleDetail.kind.replaceAll("_", " ")}` : "Select any timeline event."}</CardDescription></CardHeader>
          <CardContent>
            <FetchState {...detail} />
            {message && <div className="timeline-detail-message">{message}</div>}
            {result.reason && <p className="notice">{String(result.reason)}</p>}
            {visibleDetail && <details><summary>Recorded event data</summary>
              <ScrollArea className="timeline-detail-scroll"><pre>{JSON.stringify(visibleDetail.body, null, 2)}</pre></ScrollArea>
            </details>}
          </CardContent>
        </Card>
      </div>
    </div>
  </section>;
}
