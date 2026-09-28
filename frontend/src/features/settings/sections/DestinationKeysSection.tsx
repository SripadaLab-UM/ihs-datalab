import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/api/client";
import { type DestinationKey, settingsApi } from "@/api/settings";
import { Chip } from "@/components/ui";

import { FixedOnPractice, Section } from "./Section";

/** Where other screens link to this section: `/settings/export-folders#destination-keys`. */
export const DESTINATION_KEYS_ANCHOR = "destination-keys";

/**
 * Workflow destinations: each workflow file says where its results go by a
 * key (`deliver: destination: lab-dropbox`), and each computer maps that key
 * to one of its own export folders. Real profile only: practice DataLab
 * delivers only to its practice folder.
 */
export function DestinationKeysSection({ practice }: { practice: boolean }) {
  const queryClient = useQueryClient();
  const keys = useQuery({ queryKey: ["destination-keys"], queryFn: settingsApi.destinationKeys, enabled: !practice });
  const folders = useQuery({ queryKey: ["destinations"], queryFn: api.destinations, enabled: !practice });
  const map = useMutation({
    mutationFn: ({ key, destinationId }: { key: string; destinationId: string }) =>
      settingsApi.setDestinationKey(key, destinationId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["destination-keys"] }),
  });

  return (
    <div id={DESTINATION_KEYS_ANCHOR} className="scroll-mt-6">
      <Section title="Workflow destinations" actions={practice ? <FixedOnPractice /> : undefined}>
        {practice ? (
          <p className="mt-1 text-sm text-muted">
            Practice DataLab delivers workflow results only to its own practice folder, so there's nothing to set
            here.
          </p>
        ) : (
          <>
            <p className="mt-1 text-sm text-muted">
              A workflow file names where its results go by a key, such as <code className="font-mono text-xs">lab-dropbox</code>.
              Choose which of your export folders each key means on this computer. A workflow delivers only once its
              key has a folder.
            </p>
            {keys.isError && <p className="mt-3 text-sm text-danger">{keys.error.message}</p>}
            {map.isError && <p className="mt-3 text-sm text-danger">{map.error.message}</p>}
            {keys.data?.length === 0 && (
              <p className="mt-3 text-sm text-muted">No workflow names a destination yet.</p>
            )}
            {keys.data && keys.data.length > 0 && folders.data?.length === 0 && (
              <p className="mt-3 text-sm text-attn">Add an export folder above first, then choose it here.</p>
            )}
            <ul className="mt-2">
              {keys.data?.map((entry) => (
                <KeyRow
                  key={entry.key}
                  entry={entry}
                  folders={folders.data ?? []}
                  busy={map.isPending}
                  onChoose={(destinationId) => map.mutate({ key: entry.key, destinationId })}
                />
              ))}
            </ul>
          </>
        )}
      </Section>
    </div>
  );
}

function KeyRow({
  entry,
  folders,
  busy,
  onChoose,
}: {
  entry: DestinationKey;
  folders: { id: string; name: string; path: string }[];
  busy: boolean;
  onChoose: (destinationId: string) => void;
}) {
  const used = entry.used_by.length;
  return (
    <li className="flex flex-wrap items-center gap-3 border-b border-line py-2 text-sm last:border-b-0">
      <span className="min-w-0 flex-1">
        <code className="font-mono text-xs">{entry.key}</code>
        <span className="block text-xs text-muted">
          {used
            ? `Named by ${entry.used_by.join(", ")}`
            : "No workflow names it now"}
        </span>
      </span>
      {entry.destination_id && !entry.available && <Chip tone="bad">folder not found</Chip>}
      {!entry.destination_id && <Chip tone="attn">no folder yet</Chip>}
      <select
        aria-label={`Export folder for ${entry.key}`}
        value={entry.destination_id ?? ""}
        disabled={busy || folders.length === 0}
        onChange={(e) => e.target.value && onChoose(e.target.value)}
        className="max-w-72 rounded-[3px] border border-line bg-field px-2 py-1 text-sm outline-none focus:border-ink"
      >
        <option value="" disabled>
          Choose a folder…
        </option>
        {folders.map((folder) => (
          <option key={folder.id} value={folder.id} title={folder.path}>
            {folder.name}
          </option>
        ))}
      </select>
    </li>
  );
}
