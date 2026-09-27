// Builds the "Export conversation" report: one self-contained HTML page.
//
// It reuses the chat's own transcript and Markdown rendering, and draws each
// chart as inline SVG, so the report needs no scripts. DataLab adds a policy
// to the page that blocks every network request (backend exports.py).
import { renderToStaticMarkup } from "react-dom/server";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import type { Conversation, QueryRecord } from "@/api/client";
import { isV2, planChanges, planSections, planTypeLabel, type PlanComparison, type PlanV2 } from "@/components/chat/plan";
import { buildTranscript, finalAnswer, type ConversationEvent, type Turn } from "@/components/chat/transcript";

export interface ReportOptions {
  includeWork: boolean;
  /** Practice DataLab: the data is synthetic. */
  practice?: boolean;
}

/** Render every ```vega-lite block to SVG, keyed by its source. */
async function drawCharts(sources: string[]): Promise<Map<string, string>> {
  const drawn = new Map<string, string>();
  if (sources.length === 0) return drawn;
  const [vega, vegaLite, { expressionInterpreter }] = await Promise.all([
    import("vega"),
    import("vega-lite"),
    import("vega-interpreter"),
  ]);
  const refuse = () => Promise.reject(new Error("Charts can't load data from links."));
  for (const source of sources) {
    try {
      const spec = vegaLite.compile(JSON.parse(source)).spec;
      const view = new vega.View(vega.parse(spec, undefined, { ast: true }), {
        renderer: "none",
        expr: expressionInterpreter,
        loader: { load: refuse, sanitize: refuse } as never,
      });
      drawn.set(source, await view.toSVG());
      view.finalize();
    } catch (error) {
      drawn.set(source, `<p class="muted">Chart not included: ${escapeHtml(String(error))}</p>`);
    }
  }
  return drawn;
}

function chartSources(turns: Turn[]): string[] {
  const found: string[] = [];
  for (const turn of turns) {
    for (const match of finalAnswer(turn).matchAll(/```vega-lite\s*\n([\s\S]*?)```/g)) found.push(match[1].replace(/\n$/, ""));
  }
  return found;
}

function Answer({ text, charts }: { text: string; charts: Map<string, string> }) {
  return (
    <ReactMarkdown
      remarkPlugins={[remarkGfm]}
      components={{
        pre({ node, children }) {
          // A chart isn't code: don't draw it in a code box.
          const first = node?.children[0];
          const classes = first && "properties" in first ? String(first.properties?.className ?? "") : "";
          return classes.includes("language-vega-lite") ? <>{children}</> : <pre>{children}</pre>;
        },
        code({ className, children }) {
          const source = String(children).replace(/\n$/, "");
          if (/language-vega-lite/.test(className ?? "")) {
            return <span className="chart" dangerouslySetInnerHTML={{ __html: charts.get(source) ?? "" }} />;
          }
          return <code className={className}>{children}</code>;
        },
        img({ src, alt }) {
          return typeof src === "string" && src.startsWith("data:image/") ? <img src={src} alt={alt ?? ""} /> : <span className="muted">[image not included]</span>;
        },
        a({ href, children }) {
          // Plain text in the report: a link could carry data out when clicked.
          return (
            <span className="link" title={href}>
              {children}
            </span>
          );
        },
      }}
    >
      {text}
    </ReactMarkdown>
  );
}

function Report({
  conversation,
  turns,
  queries,
  charts,
  options,
}: {
  conversation: Conversation;
  turns: Turn[];
  queries: QueryRecord[];
  charts: Map<string, string>;
  options: ReportOptions;
}) {
  const data = conversation.kind === "data";
  return (
    <main>
      {data && !options.practice && (
        <p className="banner">
          Contains study data. Keep this file on approved storage and share it only with people approved for IHS data.
        </p>
      )}
      {options.practice && <p className="banner">Practice DataLab: synthetic data only.</p>}
      <h1>{conversation.title}</h1>
      <p className="muted">
        {data ? "Data session" : "Research session"} · {conversation.mode} · {conversation.model} · exported{" "}
        {new Date().toLocaleString()}
      </p>
      {turns.map((turn, index) => (
        <section key={index} className="turn">
          {turn.userText && <div className="question">{turn.userText}</div>}
          {turn.items
            .filter((item) => item.kind === "notice")
            .map((item, i) => (
              <p key={i} className="muted">
                {item.kind === "notice" ? item.text : null}
              </p>
            ))}
          {turn.items.map((item) =>
            item.kind === "approval" && item.approvalKind === "analysis_plan" && item.frozen ? (
              <div key={item.id} className="plan">
                <p>
                  <strong>{isV2(item.plan) && item.plan.revises ? "Approved revision of the analysis plan" : "Approved analysis plan"}</strong>{" "}
                  <span className="muted">
                    (frozen {new Date(item.frozen.at).toLocaleString()}, {item.frozen.sha256.slice(0, 12)})
                  </span>
                </p>
                {item.supersededBy && (
                  <p className="muted">
                    Replaced by a revision frozen {new Date(item.supersededBy.at).toLocaleString()}. Kept here as it was approved.
                  </p>
                )}
                {isV2(item.plan) && item.plan.revises && <Revision plan={item.plan} compareTo={item.compareTo} />}
                <ul>
                  {planTypeLabel(item.plan) && (
                    <li>
                      <em>Type</em>: {planTypeLabel(item.plan)}
                      {isV2(item.plan) && item.plan.rationale ? `. ${item.plan.rationale}` : ""}
                    </li>
                  )}
                  {planSections(item.plan).map(({ label, content }, i) => (
                    <li key={i}>
                      <em>{label}</em>: <span className="pre">{content}</span>
                    </li>
                  ))}
                </ul>
              </div>
            ) : item.kind === "approval" && item.approvalKind === "research_helper" ? (
              <p key={item.id} className="muted">
                Research helper question{" "}
                {{ approved: "sent", declined: "not sent (declined)", withdrawn: "withdrawn", pending: "not answered" }[item.state]}:{" "}
                <em>{item.sent ?? item.question}</em>
              </p>
            ) : null,
          )}
          {options.includeWork && <Work turn={turn} />}
          {finalAnswer(turn) && <Answer text={finalAnswer(turn)} charts={charts} />}
          {finalAnswer(turn) && turn.trace && turn.trace.untraced.length > 0 && (
            <p className="muted">
              Numbers not traced to a query result, command output, or output file: {turn.trace.untraced.join(", ")}
            </p>
          )}
          {turn.items.map((item, i) =>
            item.kind === "review" && item.text ? (
              <details key={`r${i}`} className="work">
                <summary>Rigor review</summary>
                <Answer text={item.text} charts={charts} />
              </details>
            ) : null,
          )}
        </section>
      ))}
      {queries.length > 0 && (
        <section>
          <h2>Queries run</h2>
          {queries.map((q) => (
            <div key={q.id} className="query">
              <p className="muted">
                {new Date(q.started_at).toLocaleString()} · {q.status}
                {q.row_count != null && ` · ${q.row_count.toLocaleString()} rows`} · {q.tables.join(", ")}
              </p>
              <pre>{q.sql_text}</pre>
            </div>
          ))}
        </section>
      )}
    </main>
  );
}

/** A revision's link to the plan it replaced, why, and what changed (the same comparison the chat shows). */
function Revision({ plan, compareTo }: { plan: PlanV2; compareTo?: PlanComparison }) {
  const diff = compareTo ? planChanges(compareTo.plan, plan) : undefined;
  const named = (status: string) =>
    diff?.changes.filter((c) => c.status === status).map((c) => c.label).join(", ");
  const at = compareTo?.approved_at ? `frozen ${new Date(compareTo.approved_at).toLocaleString()}, ` : "";
  return (
    <p className="muted">
      Revises the plan ({at}
      {plan.revises?.sha256.slice(0, 12)}). Why: {plan.revision_reason}
      {diff?.type && ` Type: ${diff.type.before} → ${diff.type.after}.`}
      {named("changed") && ` Changed: ${named("changed")}.`}
      {named("added") && ` Added: ${named("added")}.`}
      {named("removed") && ` Removed: ${named("removed")}.`}
    </p>
  );
}

function Work({ turn }: { turn: Turn }) {
  const answer = finalAnswer(turn);
  const items = turn.items.filter((i) => i.kind !== "notice" && !(i.kind === "message" && i.text === answer));
  if (items.length === 0) return null;
  return (
    <details className="work">
      <summary>How the agent worked</summary>
      {items.map((item, i) => {
        switch (item.kind) {
          case "message":
          case "reasoning":
            return <p key={i} className="muted">{item.text}</p>;
          case "command":
            return (
              // The command only, never its output: output can show rows of study data.
              <pre key={i}>$ {item.command}</pre>
            );
          case "tool":
            return <pre key={i}>{`${item.tool} ${JSON.stringify(item.arguments, null, 2)}`}</pre>;
          case "files":
            return <p key={i} className="muted">Edited {item.paths.join(", ")}</p>;
          case "approval":
          case "review":
            return null; // shown with the turn, work or not
          default:
            return null;
        }
      })}
    </details>
  );
}

export async function buildReport(
  conversation: Conversation,
  events: ConversationEvent[],
  queries: QueryRecord[],
  options: ReportOptions,
): Promise<string> {
  const turns = buildTranscript(events);
  const charts = await drawCharts(chartSources(turns));
  return renderToStaticMarkup(
    <Report conversation={conversation} turns={turns} queries={queries} charts={charts} options={options} />,
  );
}

export const REPORT_CSS = `
body { font: 15px/1.6 system-ui, -apple-system, "Segoe UI", sans-serif; color: #1b1b24; background: #fff; margin: 0; }
main { max-width: 52rem; margin: 0 auto; padding: 2rem 1.5rem 4rem; }
h1 { font-size: 1.6rem; margin: 0.5rem 0 0.25rem; }
h2 { font-size: 1.2rem; margin-top: 2.5rem; border-top: 1px solid #e3e3ea; padding-top: 1.5rem; }
.muted { color: #6b6b7b; font-size: 0.85rem; }
.banner { background: #fdf3e6; color: #8a4204; border: 1px solid #f3d9b5; border-radius: 8px; padding: 0.5rem 0.75rem; font-weight: 600; }
.turn { margin-top: 2rem; }
.question { background: #eef0ff; border-radius: 12px; padding: 0.6rem 0.9rem; white-space: pre-wrap; margin-left: 20%; }
pre { background: #f0f0f4; border-radius: 6px; padding: 0.6rem; overflow-x: auto; font-size: 0.8rem; white-space: pre-wrap; }
code { font-family: ui-monospace, Menlo, Consolas, monospace; font-size: 0.85em; }
table { border-collapse: collapse; margin: 0.75rem 0; font-size: 0.85rem; }
th, td { border: 1px solid #e3e3ea; padding: 0.3rem 0.6rem; text-align: left; }
th { background: #f7f7f9; }
.chart svg { max-width: 100%; height: auto; }
.work { margin: 0.75rem 0; border: 1px solid #e3e3ea; border-radius: 8px; padding: 0.5rem 0.75rem; }
.work summary { cursor: pointer; color: #6b6b7b; }
.link { text-decoration: underline dotted; }
.query { margin-top: 1rem; }
.plan { border: 1px solid #c7c9f9; border-radius: 8px; padding: 0.5rem 0.75rem; margin: 0.75rem 0; }
.plan .pre { white-space: pre-wrap; }
img { max-width: 100%; }
`;

function escapeHtml(text: string): string {
  return text.replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`);
}
