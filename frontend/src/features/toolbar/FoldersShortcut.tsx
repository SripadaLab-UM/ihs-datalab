// The toolbar's Export folders: the folders set up for exports, whether each
// can be used now, and Manage folders (Settings → Export folders). DataLab
// has no "open this folder" route, so each shows where it is instead.
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router";

import { api, type Destination } from "@/api/client";
import { Icon } from "@/components/ui";
import { settingsLink } from "@/features/settings/highlight";

import { PanelHead, Shortcut } from "./Popover";

// The same query as Settings and the export dialogs, so a change there shows here at once.
export const DESTINATIONS = ["destinations"];

export function useFolders() {
  const health = useQuery({ queryKey: ["health"], queryFn: api.health });
  const practice = health.data?.profile === "practice";
  const destinations = useQuery({ queryKey: DESTINATIONS, queryFn: api.destinations });
  // Practice shows its own folder only.
  const folders = (destinations.data ?? []).filter((d) => !practice || d.practice);
  // A folder switched off for exports doesn't call for attention.
  const unavailable = folders.filter((d) => d.offered && !d.available).length;
  return { destinations, folders, unavailable, practice };
}

function foldersState(count: number, unavailable: number): string {
  if (count === 0) return "none set up";
  if (unavailable) return `${unavailable} of ${count} unavailable`;
  return count === 1 ? "1 folder, available" : `${count} folders, all available`;
}

/** The list, for the popover and for More's dialog on a narrow window. */
export function FoldersPanel({ onNavigate }: { onNavigate?: () => void }) {
  const { destinations, folders, unavailable } = useFolders();
  return (
    <>
      <PanelHead
        title="Export folders"
        state={destinations.data ? foldersState(folders.length, unavailable) : "checking…"}
        tone={unavailable ? "attn" : undefined}
      />
      {destinations.isError && <p className="mt-2 text-[12.5px] text-danger">{destinations.error.message}</p>}
      {destinations.data && folders.length === 0 && (
        <p className="mt-2 text-[12.5px] text-muted">
          No export folders yet. Add one (a Dropbox folder, say) to export results to.
        </p>
      )}
      {folders.length > 0 && (
        <ul className="mt-2 flex max-h-72 flex-col divide-y divide-line overflow-y-auto">
          {folders.map((folder) => (
            <Folder key={folder.id} folder={folder} />
          ))}
        </ul>
      )}
      <div className="mt-4">
        <Link
          {...settingsLink("export-folders", "export-folders")}
          onClick={onNavigate}
          className="inline-flex cursor-pointer items-center justify-center gap-1.5 rounded-[3px] border border-line px-3 py-1.5 font-sans text-[13.5px] font-medium text-ink transition-colors hover:border-ink"
        >
          Manage folders
        </Link>
      </div>
    </>
  );
}

function Folder({ folder }: { folder: Destination }) {
  const problem = !folder.available ? (folder.status_message ?? "Not available") : folder.warning;
  return (
    <li className="py-2 text-[12.5px]">
      <p className="flex items-baseline justify-between gap-2">
        <span className="min-w-0 truncate font-medium text-ink">{folder.name}</span>
        <span className={`flex shrink-0 items-center gap-1 ${folder.available ? "text-data" : "text-attn"}`}>
          <Icon name={folder.available ? "check" : "alert"} size={12} />
          {folder.available ? "available" : "unavailable"}
        </span>
      </p>
      <p className="truncate font-mono text-[11.5px] text-muted" title={folder.path}>
        {folder.where}
        {folder.sync_provider_name && <span className="font-sans"> · {folder.sync_provider_name}</span>}
      </p>
      {problem && <p className={folder.available ? "text-muted" : "text-attn"}>{problem}</p>}
    </li>
  );
}

export function FoldersShortcut({ className }: { className?: string }) {
  const { destinations, folders, unavailable } = useFolders();
  const state = destinations.data ? foldersState(folders.length, unavailable) : "checking…";
  return (
    <Shortcut
      icon="folder"
      label={`Export folders: ${state}`}
      title={`Export folders: ${state} · click to see them or manage folders`}
      attention={unavailable ? "Folder unavailable" : undefined}
      className={className}
    >
      {(close) => <FoldersPanel onNavigate={() => close(false)} />}
    </Shortcut>
  );
}
