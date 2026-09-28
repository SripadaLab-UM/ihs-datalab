import type { Health } from "@/api/client";

import { Section } from "./Section";

/** Which DataLab this is: profile, version, database and catalog. */
export function AboutSection({ health }: { health?: Health }) {
  return (
    <Section title="About this DataLab">
      <dl className="mt-3 grid grid-cols-[10rem_1fr] gap-y-1.5 text-sm">
        <dt className="text-muted">Profile</dt>
        <dd>{health?.profile === "practice" ? "Practice (synthetic data only)" : "Real data"}</dd>
        <dt className="text-muted">Version</dt>
        <dd>{health?.version}</dd>
        <dt className="text-muted">Database</dt>
        <dd>{health?.database_configured ? "Configured" : "Not configured"}</dd>
        <dt className="text-muted">Catalog</dt>
        <dd>
          {health?.catalog_tables.toLocaleString()} tables and views
          {health?.catalog_problem && <p className="mt-1 text-[13px] text-muted">{health.catalog_problem}</p>}
        </dd>
      </dl>
    </Section>
  );
}
