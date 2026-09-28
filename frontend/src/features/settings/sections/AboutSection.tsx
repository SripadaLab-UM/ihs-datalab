import { useQuery } from "@tanstack/react-query";

import { api, type Health } from "@/api/client";

import { Section } from "./Section";

/** Which DataLab this is: profile, version, database and catalog. */
export function AboutSection({ health }: { health?: Health }) {
  // Only while the catalog isn't ready: why it's empty, and what happens next.
  const empty = health?.catalog_state !== undefined && health.catalog_state !== "ready";
  const catalog = useQuery({
    queryKey: ["catalog-status", health?.catalog_state],
    queryFn: api.catalogStatus,
    enabled: empty,
  });
  const detail = empty ? catalog.data?.detail : null;
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
          {detail && <p className="mt-1 text-[13px] text-muted">{detail}</p>}
        </dd>
      </dl>
    </Section>
  );
}
