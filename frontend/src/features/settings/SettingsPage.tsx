import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";

import { api, type CheckResult, type SafetyReport } from "@/api/client";
import { Button, Icon } from "@/components/ui";

import { ExportDestinations } from "./ExportDestinations";

export function SettingsPage() {
  const health = useQuery({ queryKey: ["health"], queryFn: api.health });
  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto flex max-w-3xl flex-col gap-14 px-8 py-12">
        <SafetySection />
        <ExportDestinations practice={health.data?.profile === "practice"} />
        <section className="border-t border-ink pt-6">
          <h2 className="font-serif text-[28px] leading-tight">About this DataLab</h2>
          <dl className="mt-3 grid grid-cols-[10rem_1fr] gap-y-1.5 text-sm">
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
    <section className="border-t border-ink pt-6">
      <div className="flex items-start justify-between gap-4">
        <span className="inline-flex h-10 w-10 shrink-0 items-center justify-center rounded-full border border-line text-data">
          <Icon name="shield" size={20} />
        </span>
        <div className="flex-1">
          <h2 className="font-serif text-[28px] leading-tight">Safety check</h2>
          <p className="mt-1 text-sm text-muted">
            Live tests of DataLab's safety promises: it starts sealed test sessions and tries to break out of them.
          </p>
        </div>
        <Button variant="primary" className="shrink-0" onClick={() => run.mutate()} disabled={run.isPending}>
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
  const unverified = report.results.filter((r) => r.status === "skip" && r.required).length;
  return (
    <div className="mt-4">
      <div
        className={clsx(
          "flex items-center gap-2 rounded-xl px-4 py-3 text-sm font-medium",
          !report.passed ? "bg-danger-soft text-danger" : unverified ? "bg-research-soft text-research" : "bg-data-soft text-data",
        )}
      >
        <Icon name={!report.passed ? "close" : unverified ? "alert" : "check"} />
        {!report.passed
          ? `${failed} check${failed > 1 ? "s" : ""} failed`
          : unverified
            ? `No failures, but ${unverified} check${unverified > 1 ? "s" : ""} couldn't be verified`
            : "Every check passed"}
        <span className="ml-auto font-normal text-muted">{new Date(report.finished_at).toLocaleString()}</span>
      </div>
      {[...byPromise].map(([promise, results]) => (
        <div key={promise} className="mt-5">
          <h3 className="dl-label">{promise}</h3>
          <ul className="mt-2 flex flex-col gap-1.5">
            {results.map((r) => (
              <li key={r.id} className="flex gap-3 rounded-lg bg-canvas px-3 py-2 text-sm">
                <span
                  className={clsx(
                    "mt-0.5 inline-flex h-5 w-5 shrink-0 items-center justify-center rounded-full",
                    r.status === "pass" && "bg-data-soft text-data",
                    r.status === "fail" && "bg-danger-soft text-danger",
                    r.status === "skip" && (r.required ? "bg-research-soft text-research" : "bg-sunken text-muted"),
                  )}
                  role="img"
                  aria-label={r.status}
                >
                  <Icon name={r.status === "pass" ? "check" : r.status === "fail" ? "close" : "alert"} size={12} />
                </span>
                <span>
                  {r.label}
                  {r.detail && <span className="block text-xs text-muted">{r.detail}</span>}
                  {r.status === "skip" && r.required && (
                    <span className="block text-xs text-research">Not verified: this needs attention.</span>
                  )}
                </span>
              </li>
            ))}
          </ul>
        </div>
      ))}
    </div>
  );
}
