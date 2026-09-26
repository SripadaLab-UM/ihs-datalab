import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";

import { api, type CheckResult, type SafetyReport } from "@/api/client";
import { Button } from "@/components/ui";

export function SettingsPage() {
  const health = useQuery({ queryKey: ["health"], queryFn: api.health });
  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto flex max-w-3xl flex-col gap-8 px-6 py-8">
        <SafetySection />
        <section>
          <h2 className="text-lg font-semibold">About this DataLab</h2>
          <dl className="mt-3 grid grid-cols-[10rem_1fr] gap-y-1 text-sm">
            <dt className="text-muted">Profile</dt>
            <dd>{health.data?.profile === "practice" ? "Practice (synthetic data only)" : "Real data"}</dd>
            <dt className="text-muted">Version</dt>
            <dd>{health.data?.version}</dd>
            <dt className="text-muted">Database</dt>
            <dd>{health.data?.database_configured ? "Configured" : "Not configured"}</dd>
            <dt className="text-muted">Catalog</dt>
            <dd>{health.data?.catalog_tables.toLocaleString()} tables and views</dd>
          </dl>
        </section>
      </div>
    </div>
  );
}

function SafetySection() {
  const queryClient = useQueryClient();
  const last = useQuery({ queryKey: ["safety"], queryFn: api.lastSafetyReport });
  const run = useMutation({
    mutationFn: api.runSafetyCheck,
    onSuccess: (report) => queryClient.setQueryData(["safety"], report),
  });
  const report = last.data;

  return (
    <section>
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-lg font-semibold">Safety check</h2>
          <p className="mt-1 text-sm text-muted">
            Live tests of DataLab's safety promises: it starts sealed test sessions and tries to break out of them.
          </p>
        </div>
        <Button variant="primary" onClick={() => run.mutate()} disabled={run.isPending}>
          {run.isPending ? "Checking… (about 20 s)" : "Run safety check"}
        </Button>
      </div>
      {run.error && <p className="mt-3 text-sm text-danger">{run.error.message}</p>}
      {report ? <Report report={report} /> : !run.isPending && <p className="mt-4 text-sm text-muted">Not run yet.</p>}
    </section>
  );
}

function Report({ report }: { report: SafetyReport }) {
  const byPromise = new Map<string, CheckResult[]>();
  for (const result of report.results) {
    byPromise.set(result.promise, [...(byPromise.get(result.promise) ?? []), result]);
  }
  const failed = report.results.filter((r) => r.status === "fail").length;
  return (
    <div className="mt-4">
      <div
        className={clsx(
          "rounded-xl px-4 py-3 text-sm font-medium",
          report.passed ? "bg-data-soft text-data" : "bg-sunken text-danger",
        )}
      >
        {report.passed ? "✓ Every check passed" : `✗ ${failed} check${failed > 1 ? "s" : ""} failed`}
        <span className="ml-2 font-normal text-muted">{new Date(report.finished_at).toLocaleString()}</span>
      </div>
      {[...byPromise].map(([promise, results]) => (
        <div key={promise} className="mt-5">
          <h3 className="text-sm font-semibold">{promise}</h3>
          <ul className="mt-2 flex flex-col gap-1.5">
            {results.map((r) => (
              <li key={r.id} className="flex gap-3 rounded-lg border border-line bg-surface px-3 py-2 text-sm">
                <span
                  className={clsx(
                    "w-4 shrink-0 text-center",
                    r.status === "pass" && "text-data",
                    r.status === "fail" && "text-danger",
                    r.status === "skip" && "text-muted",
                  )}
                  aria-label={r.status}
                >
                  {r.status === "pass" ? "✓" : r.status === "fail" ? "✗" : "–"}
                </span>
                <span>
                  {r.label}
                  {r.detail && <span className="block text-xs text-muted">{r.detail}</span>}
                </span>
              </li>
            ))}
          </ul>
        </div>
      ))}
    </div>
  );
}
