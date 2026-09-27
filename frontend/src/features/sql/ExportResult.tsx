import { useMutation, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router";

import { api } from "@/api/client";
import { sqlApi } from "@/api/sql";
import { Button, Modal } from "@/components/ui";

/** Export one result's file to an export folder, with a manifest saying what query made it. */
export function ExportResult({ queryId, practice, onClose }: { queryId: string; practice: boolean; onClose: () => void }) {
  const destinations = useQuery({ queryKey: ["destinations"], queryFn: api.destinations });
  const [chosen, setChosen] = useState("");
  const destinationId = chosen || destinations.data?.find((d) => d.available)?.id || "";
  const run = useMutation({ mutationFn: () => sqlApi.export(queryId, destinationId) });

  return (
    <Modal title="Export the result" onClose={onClose}>
      {run.data ? (
        <div className="flex flex-col gap-3 font-sans text-[13.5px]">
          <p>Exported the result, with a manifest of the query that made it, to:</p>
          <p className="rounded-[3px] bg-sunken px-3 py-2 font-mono text-[12px] break-all">{run.data.folder}</p>
          {!practice && <p className="text-research">This file contains study data. Keep it on approved storage.</p>}
          <div className="flex justify-end">
            <Button variant="primary" onClick={onClose}>
              Done
            </Button>
          </div>
        </div>
      ) : (
        <div className="flex flex-col gap-4 font-sans text-[13.5px]">
          <p className="text-muted">
            The whole result goes as a CSV file, in a new dated folder, with <code className="font-mono">datalab-export.json</code>{" "}
            saying which query made it and when. Nothing already in the folder is changed.
          </p>
          <label className="block">
            <span className="dl-label">To</span>
            {destinations.data?.length === 0 ? (
              <p className="mt-1 text-[12.5px] text-muted">
                No export folders yet. Add one in{" "}
                <Link to="/settings" className="text-ink underline">
                  Settings
                </Link>
                .
              </p>
            ) : (
              <select
                value={destinationId}
                onChange={(e) => setChosen(e.target.value)}
                className="mt-1.5 w-full rounded-[3px] border border-line bg-field px-3 py-2 outline-none focus:border-ink"
              >
                {destinations.data?.map((d) => (
                  <option key={d.id} value={d.id} disabled={!d.available}>
                    {d.name} — {d.path}
                  </option>
                ))}
              </select>
            )}
          </label>
          {!practice && <p className="text-[12.5px] text-research">What you export contains study data.</p>}
          {run.error && <p className="text-danger">{run.error.message}</p>}
          <div className="flex justify-end gap-2">
            <Button onClick={onClose}>Cancel</Button>
            <Button variant="primary" disabled={run.isPending || !destinationId} onClick={() => run.mutate()}>
              {run.isPending ? "Exporting…" : "Export"}
            </Button>
          </div>
        </div>
      )}
    </Modal>
  );
}
