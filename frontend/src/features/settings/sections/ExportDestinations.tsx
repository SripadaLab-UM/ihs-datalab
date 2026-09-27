import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/api/client";
import { Button, FileGlyph } from "@/components/ui";

/** Folders the person has chosen to export to, such as a local Dropbox folder. */
export function ExportDestinations({ practice }: { practice: boolean }) {
  const queryClient = useQueryClient();
  const destinations = useQuery({ queryKey: ["destinations"], queryFn: api.destinations });
  const refresh = () => queryClient.invalidateQueries({ queryKey: ["destinations"] });
  const add = useMutation({ mutationFn: api.addDestination, onSuccess: refresh });
  const remove = useMutation({ mutationFn: api.removeDestination, onSuccess: refresh });

  return (
    <section className="border-t border-ink pt-6">
      <div className="flex items-center justify-between">
        <h2 className="font-serif text-[28px] leading-tight">Export folders</h2>
        {!practice && (
          <Button onClick={() => add.mutate()} disabled={add.isPending}>
            {add.isPending ? "Choose in the window that opened…" : "Add a folder…"}
          </Button>
        )}
      </div>
      <p className="mt-1 text-sm text-muted">
        {practice
          ? "Practice DataLab exports only to its own practice folder, so nothing from it ends up somewhere real."
          : "Results leave DataLab only when you export them, and only to folders you add here. Each export goes into its own dated folder with a note of what it is and where it came from."}
      </p>
      {add.error && <p className="mt-2 text-sm text-danger">{add.error.message}</p>}
      {remove.error && <p className="mt-2 text-sm text-danger">{remove.error.message}</p>}
      <ul className="mt-3 flex flex-col gap-2">
        {destinations.data?.length === 0 && <li className="text-sm text-muted">No export folders yet.</li>}
        {destinations.data?.map((d) => (
          <li key={d.id} className="flex items-center gap-3 rounded-lg bg-canvas px-3 py-2 text-sm">
            <FileGlyph kind="folder" size={28} />
            <span className="font-medium">{d.name}</span>
            <span className="min-w-0 flex-1 truncate font-mono text-xs text-muted" title={d.path}>
              {d.path}
            </span>
            {!d.available && <span className="text-xs text-danger">not found</span>}
            {!practice && (
              <Button variant="ghost" className="px-2 py-0.5 text-xs" onClick={() => remove.mutate(d.id)}>
                Remove
              </Button>
            )}
          </li>
        ))}
      </ul>
      {!practice && (
        <p className="mt-2 text-xs text-muted">Removing a folder here doesn't touch anything already exported to it.</p>
      )}
    </section>
  );
}
