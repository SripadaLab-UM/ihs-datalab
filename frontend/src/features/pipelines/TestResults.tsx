import { useQuery } from "@tanstack/react-query";
import clsx from "clsx";
import { useState } from "react";

import { pipelinesApi, type PipelineTest } from "@/api/pipelines";

import { testLine, when } from "./pipelines";

/** How the package's tests went on a change: counts, what failed, and the log. */
export function TestResults({ test }: { test: PipelineTest | null | undefined }) {
  const line = testLine(test);
  const [showLog, setShowLog] = useState(false);
  const log = useQuery({
    queryKey: ["pipeline-test-log", test?.id],
    queryFn: () => pipelinesApi.testLog(test!.id),
    enabled: showLog && Boolean(test) && test?.status !== "running",
  });
  return (
    <div className="flex flex-col gap-2" aria-live="polite">
      <p
        className={clsx(
          "font-sans text-[13.5px]",
          line.tone === "good" && "text-data",
          line.tone === "bad" && "text-danger",
          !line.tone && "text-muted",
        )}
      >
        {test?.status === "running" && <span className="dl-breathe mr-2 inline-block size-[6px] rounded-full bg-ink" />}
        {line.text}
      </p>
      {test && test.status !== "running" && (
        <p className="font-sans text-[12.5px] text-muted">
          testthat, in a container with no network{test.finished_at ? `, ${when(test.finished_at)}` : ""}.
          {test.tests > 0 && (
            <>
              {" "}
              {test.passed} passed, {test.failed} failed, {test.errors} errors, {test.skipped} skipped
              {test.warnings ? `, ${test.warnings} warnings` : ""}.
            </>
          )}
        </p>
      )}
      {test && test.failures.length > 0 && (
        <ul className="flex flex-col gap-0.5 font-mono text-[12.5px]">
          {test.failures.map((f, i) => (
            <li key={i} className="text-danger">
              {f.kind === "error" ? "error" : "failed"}: {f.file} › {f.test}
            </li>
          ))}
        </ul>
      )}
      {test && test.status !== "running" && (
        <div>
          <button
            type="button"
            aria-expanded={showLog}
            onClick={() => setShowLog(!showLog)}
            className="font-sans text-[12.5px] text-ink underline decoration-faint underline-offset-2 hover:decoration-ink"
          >
            {showLog ? "Hide the log" : "Show the log"}
          </button>
          {showLog && (
            <pre className="mt-2 max-h-80 overflow-auto rounded-[3px] border border-line bg-sunken p-2 font-mono text-[12px] whitespace-pre-wrap">
              {log.data
                ? (log.data.truncated ? "…\n" : "") + (log.data.text || "(empty)")
                : log.isError
                  ? "The log couldn't be read."
                  : "Loading…"}
            </pre>
          )}
        </div>
      )}
    </div>
  );
}
