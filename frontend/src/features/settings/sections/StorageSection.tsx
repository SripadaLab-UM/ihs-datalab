import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import {
  formatBytes,
  type RemovableKind,
  settingsApi,
  type StorageGroup,
  type StorageItem,
} from "@/api/settings";
import { Button, Chip, Icon, Modal } from "@/components/ui";

import { Section } from "./Section";

// Groups that list many items show this many until "Show all".
const SHOWN = 5;
const REMOVABLE: RemovableKind[] = ["playground-result", "run-files", "backup"];
const WHAT: Record<RemovableKind, string> = {
  "playground-result": "Playground result",
  "run-files": "run's files",
  backup: "backup",
};

/**
 * Storage: what uses disk in DataLab's data folder. Nothing is deleted
 * automatically; the person can remove the few items that are safe to go,
 * one at a time, each confirmed.
 */
export function StorageSection() {
  const storage = useQuery({ queryKey: ["settings-storage"], queryFn: settingsApi.storage });
  const [removing, setRemoving] = useState<StorageItem | null>(null);
  const [freed, setFreed] = useState<string | null>(null);
  const usage = storage.data;

  return (
    <Section
      title="Storage"
      actions={
        <Button variant="ghost" onClick={() => storage.refetch()} disabled={storage.isFetching}>
          {storage.isFetching ? "Measuring…" : "Measure again"}
        </Button>
      }
    >
      <p className="mt-1 text-sm text-muted">
        Nothing is deleted automatically. You can remove Playground results, old workflow runs' files and database
        backups here, one at a time. DataLab never removes its database, the audit log, or anything in use.
      </p>
      {storage.isError && <p className="mt-3 text-sm text-danger">{storage.error.message}</p>}
      {usage && (
        <p className="mt-4 font-serif text-[17px]">
          DataLab's data folder uses <span className="tabular">{formatBytes(usage.size_bytes)}</span>
          {usage.free_bytes !== null && (
            <span className="text-muted">
              {" "}
              · <span className="tabular">{formatBytes(usage.free_bytes)}</span> free on this disk
            </span>
          )}
          <span className="mt-0.5 block truncate font-mono text-xs text-muted" title={usage.data_dir}>
            {usage.data_dir}
          </span>
        </p>
      )}
      {freed && (
        <p className="mt-3 flex items-center gap-1.5 text-sm text-data" role="status">
          <Icon name="check" size={14} /> {freed}
        </p>
      )}
      {usage?.groups.map((group) => (
        <Group
          key={group.id}
          group={group}
          onRemove={(item) => {
            setFreed(null);
            setRemoving(item);
          }}
        />
      ))}
      {removing && (
        <ConfirmRemove
          item={removing}
          onClose={() => setRemoving(null)}
          onRemoved={(bytes) => {
            setRemoving(null);
            setFreed(`Removed. That freed ${formatBytes(bytes)}.`);
          }}
        />
      )}
    </Section>
  );
}

function Group({ group, onRemove }: { group: StorageGroup; onRemove: (item: StorageItem) => void }) {
  const [all, setAll] = useState(false);
  const items = all ? group.items : group.items.slice(0, SHOWN);
  return (
    <div className="mt-7" aria-labelledby={`storage-${group.id}`}>
      <div className="flex items-baseline justify-between gap-4 border-b border-ink pb-1">
        <h3 id={`storage-${group.id}`} className="dl-label">
          {group.title}
        </h3>
        <span className="tabular font-mono text-xs">{formatBytes(group.size_bytes)}</span>
      </div>
      <p className="mt-1.5 text-xs text-muted">{group.about}</p>
      {group.items.length === 0 ? (
        <p className="mt-2 text-sm text-muted">Nothing here yet.</p>
      ) : (
        <ul className="mt-1">
          {items.map((item) => (
            <Row key={`${item.kind}:${item.id}`} item={item} onRemove={() => onRemove(item)} />
          ))}
        </ul>
      )}
      {group.items.length > SHOWN && (
        <Button variant="ghost" className="mt-1 px-0 text-xs" onClick={() => setAll(!all)}>
          {all ? "Show fewer" : `Show all ${group.items.length}`}
        </Button>
      )}
    </div>
  );
}

function Row({ item, onRemove }: { item: StorageItem; onRemove: () => void }) {
  const removable = item.removable && REMOVABLE.includes(item.kind as RemovableKind);
  return (
    <li className="flex items-start gap-4 border-b border-line py-2 text-sm last:border-b-0">
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <span className="min-w-0 truncate" title={item.label}>
            {item.label}
          </span>
          {item.kind === "backup" && item.kept && (
            <Chip tone="attn" title={item.kept_because ?? undefined}>
              kept for good
            </Chip>
          )}
          {item.kind === "backup" && !item.kept && item.kept_because && (
            <Chip title={item.kept_because}>kept for now</Chip>
          )}
        </div>
        <div className="mt-0.5 text-xs text-muted">
          {item.detail}
          {item.modified_at && <span> · {when(item.modified_at)}</span>}
        </div>
        {item.kind === "backup" && item.kept_because && (
          <div className="mt-0.5 text-xs text-muted">{item.kept_because}</div>
        )}
        {!removable && item.not_removable_because && (
          <div className="mt-0.5 text-xs text-muted">{item.not_removable_because}</div>
        )}
      </div>
      <span className="tabular shrink-0 pt-px font-mono text-xs">{formatBytes(item.size_bytes)}</span>
      <span className="w-20 shrink-0 text-right">
        {removable ? (
          <Button variant="danger" className="px-2 py-0.5 text-xs" onClick={onRemove}>
            Remove…
          </Button>
        ) : (
          <span className="text-xs text-faint">kept</span>
        )}
      </span>
    </li>
  );
}

function ConfirmRemove({
  item,
  onClose,
  onRemoved,
}: {
  item: StorageItem;
  onClose: () => void;
  onRemoved: (freed: number) => void;
}) {
  const queryClient = useQueryClient();
  const kind = item.kind as RemovableKind;
  const [understood, setUnderstood] = useState(false);
  const remove = useMutation({
    mutationFn: () => settingsApi.removeStorageItem(kind, item.id, item.needs_confirmation && understood),
    onSuccess: (removed) => {
      queryClient.invalidateQueries({ queryKey: ["settings-storage"] });
      queryClient.invalidateQueries({ queryKey: ["settings-updates"] });
      onRemoved(removed.freed_bytes);
    },
  });
  return (
    <Modal title={`Remove this ${WHAT[kind]}?`} onClose={onClose}>
      <div className="flex flex-col gap-3 text-sm">
        <p className="font-serif text-[17px]">
          {item.label}
          <span className="block font-sans text-xs text-muted">
            {item.detail} · {formatBytes(item.size_bytes)}
          </span>
        </p>
        {item.removing_loses && <p>{item.removing_loses}</p>}
        <p className="text-muted">This can't be undone.</p>
        {item.needs_confirmation && (
          <label className="flex items-start gap-2 border-l-2 border-attn pl-3 text-attn">
            <input
              type="checkbox"
              className="mt-1"
              checked={understood}
              onChange={(e) => setUnderstood(e.target.checked)}
            />
            <span>I understand this may be the only copy of what the rollback dropped, and it will be gone.</span>
          </label>
        )}
        {remove.error && <p className="text-danger">{remove.error.message}</p>}
        <div className="flex justify-end gap-2">
          <Button onClick={onClose}>Cancel</Button>
          <Button
            variant="danger"
            onClick={() => remove.mutate()}
            disabled={remove.isPending || (item.needs_confirmation && !understood)}
          >
            <Icon name="trash" size={14} /> {remove.isPending ? "Removing…" : "Remove"}
          </Button>
        </div>
      </div>
    </Modal>
  );
}

function when(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleString([], { year: "numeric", month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
}
