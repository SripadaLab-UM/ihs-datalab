import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useEffect, useRef, useState } from "react";

import { api, type Destination, type DestinationPlace, type DestinationPlaces, type FolderTest } from "@/api/client";
import { ApiError } from "@/api/http";
import { Button, Chip, FileGlyph, Icon } from "@/components/ui";
import { pickerOpenHint } from "@/lib/picker";

import { lightUp } from "../highlight";

import { FixedOnPractice, Part, Section } from "./Section";

const FIELD =
  "w-64 max-w-full rounded-[3px] border border-line bg-field px-2 py-1 text-sm outline-none focus:border-ink";

/** What each state is called, next to the folder. */
const STATUS_LABELS: Record<Destination["status"], string> = {
  ready: "Ready",
  missing: "Not found",
  not_a_folder: "Not a folder",
  not_writable: "Can't save here",
  refused: "Not allowed",
};

/**
 * Folders the person has chosen to export to, such as a folder in their
 * Dropbox. DataLab writes to the folder on this computer that the Dropbox app
 * keeps in sync: no Dropbox account or key. It can say a file was saved here,
 * never that it was uploaded.
 */
export function ExportDestinations({ practice }: { practice: boolean }) {
  const queryClient = useQueryClient();
  const destinations = useQuery({ queryKey: ["destinations"], queryFn: api.destinations });
  const places = useQuery({ queryKey: ["destination-places"], queryFn: api.destinationPlaces });
  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: ["destinations"] });
    void queryClient.invalidateQueries({ queryKey: ["destination-keys"] });
  };

  return (
    <Section id="export-folders" title="Export folders" actions={practice ? <FixedOnPractice /> : undefined}>
      <p className="mt-1 text-sm text-muted">
        {practice
          ? "Practice DataLab exports only to its own practice folder, so nothing from it ends up somewhere real."
          : "Results leave DataLab only when you export them, and only to folders you add here. Each export goes into its own dated folder with a note of what it is and where it came from."}
      </p>
      {destinations.isError && <p className="mt-2 text-sm text-danger">{destinations.error.message}</p>}
      <ul className="mt-3 flex flex-col gap-2" aria-label="Export folders">
        {destinations.data?.length === 0 && <li className="text-sm text-muted">No export folders yet.</li>}
        {destinations.data?.map((d) => (
          <FolderRow key={d.id} folder={d} onChanged={refresh} />
        ))}
      </ul>
      {!practice && destinations.data && destinations.data.length > 0 && (
        <p className="mt-2 text-xs text-muted">
          Removing a folder here only makes DataLab forget it. The folder, and everything already exported to it, stay
          as they are.
        </p>
      )}
      {practice ? <SyncFoldersOnPractice places={places.data} /> : <AddFolder places={places.data} onAdded={refresh} />}
    </Section>
  );
}

/** The element id of a folder's row, so a folder just added can be lit up there. */
export function folderRowId(id: string): string {
  return `export-folder-${id}`;
}

/** A folder just added, and the found sync folder the picker opened in, if one. */
interface Added {
  folder: Destination;
  place?: DestinationPlace;
}

/** Add a folder: a name, then the computer's own folder picker (opening in a found Dropbox, say). */
function AddFolder({ places, onAdded }: { places?: DestinationPlaces; onAdded: () => void }) {
  const [name, setName] = useState("");
  const [added, setAdded] = useState<Added | null>(null);
  const stopHighlight = useRef<() => void>(undefined);
  useEffect(() => () => stopHighlight.current?.(), []);
  const found = places?.places ?? [];
  const add = useMutation({
    mutationFn: (startIn?: string) => api.addDestination({ name: name.trim() || undefined, startIn }),
    onMutate: () => setAdded(null),
    onSuccess: (folder, startIn) => {
      setName("");
      setAdded({ folder, place: found.find((p) => p.id === startIn) });
      onAdded();
      showInList(folder.id);
    },
  });
  // Lights up its row in the list above once it's drawn, leaving focus on the confirmation.
  const showInList = (id: string) => {
    stopHighlight.current?.();
    stopHighlight.current = lightUp(folderRowId(id), () => {}, { focus: false, block: "nearest" });
  };
  const choosing = add.isPending;
  // Cancelling the picker isn't a problem: nothing was added, and that's all.
  const cancelled = add.error instanceof ApiError && add.error.status === 400;

  return (
    <Part title="Add a folder" className="mt-6">
      <p className="mt-1 text-sm text-muted">
        To export to Dropbox, choose a folder inside your Dropbox folder on this computer. DataLab saves files there
        and the Dropbox app uploads them, as it does for any file. There's no Dropbox account or key to set up.
      </p>
      <div className="mt-3 flex flex-col gap-3">
        <label className="flex flex-col gap-1 text-sm">
          <span className="dl-label">Name (optional)</span>
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="e.g. Lab Dropbox"
            maxLength={80}
            className={FIELD}
          />
          <span className="text-xs text-muted">What exports and workflows show. Otherwise, the folder's own name.</span>
        </label>
        <div className="flex flex-wrap items-center gap-2">
          {found.map((place) => (
            <Button key={place.id} onClick={() => add.mutate(place.id)} disabled={choosing} title={place.where}>
              <Icon name="folder" size={14} /> Choose in {place.label}…
            </Button>
          ))}
          <Button onClick={() => add.mutate(undefined)} disabled={choosing}>
            {found.length ? "Choose another folder…" : "Choose folder…"}
          </Button>
        </div>
        {choosing && (
          <p className="text-sm text-attn" role="status">
            {pickerOpenHint("folder")}
          </p>
        )}
        {add.error &&
          (cancelled ? (
            <p className="text-sm text-muted" role="status">
              No folder was chosen, so nothing was added.
            </p>
          ) : (
            <p className="border-l-2 border-danger pl-3 text-sm text-danger" role="alert">
              Not added: {add.error.message}
            </p>
          ))}
        {added && (
          <AddedFolder
            added={added}
            onShow={() => showInList(added.folder.id)}
            onDone={() => setAdded(null)}
            onTested={onAdded}
          />
        )}
        <p className="text-xs text-muted">
          {found.length
            ? `Found on this computer: ${found.map((p) => p.label).join(", ")}. `
            : "No Dropbox, OneDrive, Box or Google Drive folder was found in your home folder. If the Dropbox app isn't installed or signed in, its folder won't be here yet. "}
          The picker is your computer's own, so only you can choose where exports go. System folders, app data and
          DataLab's own data folder can't be chosen.
        </p>
      </div>
    </Part>
  );
}

/**
 * Right where the folder was chosen: that it was added, where it is, and
 * whether exports can go there now. A folder outside the sync folder the
 * picker opened in is added (it's a folder like any other), but its files
 * won't be uploaded by that app, so it says so.
 */
function AddedFolder({
  added: { folder, place },
  onShow,
  onDone,
  onTested,
}: {
  added: Added;
  onShow: () => void;
  onDone: () => void;
  onTested: () => void;
}) {
  const test = useMutation({ mutationFn: () => api.testDestination(folder.id), onSuccess: onTested });
  const ready = folder.status === "ready";
  const outside = place && folder.sync_provider !== place.provider;
  return (
    <div
      className={`rounded-lg border-l-2 px-3 py-2.5 text-sm ${ready ? "border-data bg-data-soft" : "border-attn bg-attn-soft"}`}
      role="status"
      aria-label="Folder added"
    >
      <p className="flex items-center gap-1.5 font-medium">
        {ready ? (
          <>
            <Icon name="check" size={14} className="text-data" /> Added: {folder.name} — exports can go here.
          </>
        ) : (
          <>Added: {folder.name}, but exports can't go here yet.</>
        )}
      </p>
      <p className="mt-1 min-w-0 truncate font-mono text-xs text-muted" title={folder.path}>
        {folder.where}
      </p>
      <p className="mt-0.5 text-xs text-muted">{folder.location_note}</p>
      {!ready && folder.status_message && <p className="mt-1 text-xs text-danger">{folder.status_message}</p>}
      {ready && folder.warning && <p className="mt-1 text-xs text-attn">{folder.warning}</p>}
      {outside && (
        <p className="mt-1 text-xs text-attn">
          This folder isn't inside {place.label}, so the {place.provider_name} app won't upload what's saved here. To
          export to {place.provider_name}, add a folder inside {place.where} instead.
        </p>
      )}
      <p className="mt-1 text-xs text-muted">
        It's now in the list above, and exports and workflow destinations offer it by this name.
      </p>
      <div className="mt-2 flex flex-wrap items-center gap-1">
        <Button className="px-2 py-0.5 text-xs" onClick={() => test.mutate()} disabled={test.isPending}>
          {test.isPending ? "Testing…" : "Test it now"}
        </Button>
        <Button variant="ghost" className="px-2 py-0.5 text-xs" onClick={onShow}>
          Show in list
        </Button>
        <Button variant="ghost" className="px-2 py-0.5 text-xs" onClick={onDone}>
          Done
        </Button>
      </div>
      {test.data && (
        <div className="mt-2">
          <TestOutcome result={test.data} />
        </div>
      )}
      {test.error && <p className="mt-2 text-xs text-danger">{test.error.message}</p>}
    </div>
  );
}

/** Practice: explain where Dropbox setup is, rather than hide it. */
function SyncFoldersOnPractice({ places }: { places?: DestinationPlaces }) {
  return (
    <Part
      title="Dropbox and other folders"
      className="mt-6"
      state={<FixedOnPractice>Available on the real DataLab</FixedOnPractice>}
    >
      <p className="mt-1 text-sm text-muted">
        {places?.why_not ??
          "Available on the real DataLab. Practice DataLab saves only to its own practice folder, so nothing from it can end up in a real Dropbox or shared folder."}{" "}
        On the real DataLab you choose a folder inside your Dropbox folder, give it a name, and test it.
      </p>
    </Part>
  );
}

function FolderRow({ folder, onChanged }: { folder: Destination; onChanged: () => void }) {
  const [renaming, setRenaming] = useState(false);
  const [name, setName] = useState(folder.name);
  const [confirming, setConfirming] = useState(false);
  const [tested, setTested] = useState<FolderTest | null>(null);
  const change = useMutation({
    mutationFn: (update: { name?: string; offered?: boolean }) => api.changeDestination(folder.id, update),
    onSuccess: () => {
      setRenaming(false);
      onChanged();
    },
  });
  const test = useMutation({
    mutationFn: () => api.testDestination(folder.id),
    onMutate: () => setTested(null),
    onSuccess: (result) => {
      setTested(result);
      onChanged();
    },
  });
  const remove = useMutation({ mutationFn: () => api.removeDestination(folder.id), onSuccess: onChanged });
  const ready = folder.status === "ready";
  const error = change.error ?? test.error ?? remove.error;

  const rename = (event: FormEvent) => {
    event.preventDefault();
    change.mutate({ name });
  };

  return (
    <li
      id={folderRowId(folder.id)}
      aria-label={folder.name}
      className="flex scroll-mt-6 flex-col gap-1.5 rounded-lg bg-canvas px-3 py-2.5 text-sm"
    >
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5">
        <FileGlyph kind="folder" size={28} />
        {renaming ? (
          <form onSubmit={rename} className="flex flex-wrap items-center gap-2">
            <input
              aria-label={`New name for ${folder.name}`}
              autoFocus
              maxLength={80}
              value={name}
              onChange={(e) => setName(e.target.value)}
              className={FIELD}
            />
            <Button type="submit" variant="primary" disabled={!name.trim() || change.isPending}>
              Save
            </Button>
            <Button
              type="button"
              onClick={() => {
                setRenaming(false);
                setName(folder.name);
                change.reset();
              }}
            >
              Cancel
            </Button>
          </form>
        ) : (
          <span className="font-medium">{folder.name}</span>
        )}
        <Chip>{kindLabel(folder)}</Chip>
        {!ready ? (
          <Chip tone="bad">{STATUS_LABELS[folder.status]}</Chip>
        ) : folder.offered ? (
          <Chip tone="good">Ready</Chip>
        ) : (
          <Chip tone="attn">Turned off</Chip>
        )}
        {ready && folder.warning && <Chip tone="attn">May be online-only</Chip>}
        {!folder.practice && !renaming && (
          <span className="ml-auto flex flex-wrap items-center gap-1">
            <Button variant="ghost" className="px-2 py-0.5 text-xs" onClick={() => test.mutate()} disabled={test.isPending}>
              {test.isPending ? "Testing…" : "Test folder"}
            </Button>
            <Button variant="ghost" className="px-2 py-0.5 text-xs" onClick={() => setRenaming(true)}>
              Rename
            </Button>
            <Button variant="ghost" className="px-2 py-0.5 text-xs" onClick={() => setConfirming(true)}>
              Remove
            </Button>
          </span>
        )}
        {folder.practice && (
          <Button
            variant="ghost"
            className="ml-auto px-2 py-0.5 text-xs"
            onClick={() => test.mutate()}
            disabled={test.isPending}
          >
            {test.isPending ? "Testing…" : "Test folder"}
          </Button>
        )}
      </div>
      <p className="min-w-0 truncate font-mono text-xs text-muted" title={folder.path}>
        {folder.where}
      </p>
      <p className="text-xs text-muted">{folder.location_note}</p>
      {!ready && folder.status_message && <p className="text-xs text-danger">{folder.status_message}</p>}
      {ready && folder.warning && <p className="text-xs text-attn">{folder.warning}</p>}
      {folder.workflow_keys.length > 0 && (
        <p className="text-xs text-muted">
          Workflows deliver here as{" "}
          {folder.workflow_keys.map((key) => (
            <code key={key} className="font-mono">
              {key}
            </code>
          ))}
          .
        </p>
      )}
      {!folder.practice && (
        <label className="flex items-center gap-2 text-xs">
          <input
            type="checkbox"
            checked={folder.offered}
            disabled={change.isPending}
            onChange={(e) => change.mutate({ offered: e.target.checked })}
          />
          Offer for exports and workflow deliveries
        </label>
      )}
      {tested && <TestOutcome result={tested} />}
      {confirming && (
        <div className="flex flex-wrap items-center gap-2 border-l-2 border-attn pl-3 text-xs" role="group" aria-label="Confirm remove">
          <span>
            Forget {folder.name}? DataLab stops offering it. The folder and its files aren't touched.
            {folder.workflow_keys.length > 0 && " Workflows that deliver here will need another folder."}
          </span>
          <Button variant="danger" className="px-2 py-0.5 text-xs" onClick={() => remove.mutate()} disabled={remove.isPending}>
            Forget folder
          </Button>
          <Button className="px-2 py-0.5 text-xs" onClick={() => setConfirming(false)}>
            Cancel
          </Button>
        </div>
      )}
      {error && (
        <p className="text-xs text-danger" role="alert">
          {error.message}
        </p>
      )}
    </li>
  );
}

/** A Test's outcome: saved locally, never "synced". */
function TestOutcome({ result }: { result: FolderTest }) {
  if (!result.ok) {
    return (
      <p className="text-xs text-danger" role="status">
        Test failed: {result.message}
      </p>
    );
  }
  return (
    <div className="text-xs" role="status">
      <p className="flex items-center gap-1.5 text-data">
        <Icon name="check" size={13} /> Saved locally ✓
        <span className="text-muted">
          · a synthetic test file ({result.test_file}) was saved here and{" "}
          {result.removed ? "removed again" : "couldn't be removed; it's safe to delete"}.
        </span>
      </p>
      {result.sync_note && <p className="mt-0.5 text-muted">{result.sync_note}</p>}
      {result.note && <p className="mt-0.5 text-attn">{result.note}</p>}
    </div>
  );
}

function kindLabel(folder: Destination): string {
  if (folder.practice) return "Practice folder";
  if (folder.location === "sync_folder") return `In ${folder.sync_provider_name ?? "a sync folder"}`;
  if (folder.location === "external_drive") return "Separate drive";
  return "On this computer";
}
