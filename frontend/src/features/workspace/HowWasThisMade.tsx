import { Chip } from "@/components/ui";
import type { FileProvenance } from "@/components/chat/provenance";

/**
 * "How was this made?" for a workspace file: the turn that left it as it is,
 * that turn's commands (those naming the file first), the scripts they ran
 * as they were then, and the queries those read. DataLab says what it knows
 * and no more: it doesn't see which command writes a file, so the summary
 * says so. No query rows or command output are shown: a query opens in the
 * Queries tab, and a script in the file viewer.
 */
export function HowWasThisMade({
  provenance,
  openQuery,
  openScript,
}: {
  provenance: FileProvenance;
  openQuery?: (id: string) => void;
  openScript?: (path: string, checkpoint: number) => void;
}) {
  const { checkpoint } = provenance;
  return (
    <section aria-label="How was this made?" className="font-sans text-[13.5px] leading-relaxed">
      <h3 className="dl-label">How was this made?</h3>
      <p className="mt-1 max-w-[62ch]">{provenance.summary}</p>
      {provenance.found && (
        <>
          {provenance.commands.length > 0 && (
            <>
              <h4 className="dl-label mt-3">
                {provenance.turn_not_saved
                  ? `Commands in turn ${provenance.turn} and its rigor review`
                  : provenance.in_review
                    ? `Commands in turn ${provenance.turn}'s rigor review`
                    : `Commands in turn ${provenance.turn}`}
              </h4>
              <ul className="mt-1 flex flex-col gap-1.5">
                {provenance.commands.map((command) => (
                  <li key={command.id} className="flex flex-wrap items-baseline gap-2">
                    <code className="min-w-0 max-w-full truncate bg-sunken px-1.5 py-0.5 font-mono text-[12px]" title={command.command}>
                      {command.command}
                    </code>
                    {command.names_file && <Chip>names this file</Chip>}
                    {!command.names_file && command.via_script && <Chip>ran {command.via_script}, which names it</Chip>}
                    {command.seen_in_output && <Chip>printed its name</Chip>}
                    {command.exit_code !== null && command.exit_code !== 0 && <Chip tone="bad">failed ({command.exit_code})</Chip>}
                  </li>
                ))}
              </ul>
              {provenance.more_commands > 0 && <p className="mt-1 text-[12.5px] text-muted">And {provenance.more_commands} more.</p>}
            </>
          )}
          {provenance.scripts.length > 0 && (
            <>
              <h4 className="dl-label mt-3">Scripts, as they were then</h4>
              <ul className="mt-1 flex flex-col gap-1">
                {provenance.scripts.map((script) => (
                  <li key={script.path} className="flex flex-wrap items-baseline gap-2">
                    {openScript && checkpoint != null ? (
                      <button
                        type="button"
                        onClick={() => openScript(script.path, checkpoint)}
                        className="font-mono text-[12.5px] text-ink underline decoration-faint underline-offset-2 hover:decoration-ink"
                      >
                        {script.path}
                      </button>
                    ) : (
                      <span className="font-mono text-[12.5px]">{script.path}</span>
                    )}
                    {script.names_file && <Chip>names this file</Chip>}
                  </li>
                ))}
              </ul>
            </>
          )}
          {provenance.queries.length > 0 && (
            <>
              <h4 className="dl-label mt-3">Queries</h4>
              <ul className="mt-1 flex flex-col gap-1.5">
                {provenance.queries.map((query) => (
                  <li key={query.id}>
                    <span className="flex flex-wrap items-baseline gap-2">
                      {openQuery ? (
                        <button
                          type="button"
                          onClick={() => openQuery(query.id)}
                          className="font-mono text-[12.5px] text-ink underline decoration-faint underline-offset-2 hover:decoration-ink"
                        >
                          {query.id}
                        </button>
                      ) : (
                        <span className="font-mono text-[12.5px]">{query.id}</span>
                      )}
                      <span className="text-muted">
                        {query.tables.join(", ") || "no tables"}
                        {query.row_count != null && ` · ${query.row_count.toLocaleString()} rows`}
                      </span>
                    </span>
                    <span className="block text-[12.5px] text-muted">
                      {query.read_by.length
                        ? `Its result file is read by ${query.read_by
                            .map((r) => (r.kind === "script" ? r.ref : "a command in that turn"))
                            .join(", ")}.`
                        : "Run in that turn; nothing listed here names its result file."}
                    </span>
                  </li>
                ))}
              </ul>
              {provenance.more_queries > 0 && <p className="mt-1 text-[12.5px] text-muted">And {provenance.more_queries} more.</p>}
            </>
          )}
        </>
      )}
    </section>
  );
}
