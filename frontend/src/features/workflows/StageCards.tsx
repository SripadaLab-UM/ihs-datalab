import clsx from "clsx";
import { type ReactNode, useId, useState } from "react";
import { Link } from "react-router";

import type { DestinationChoice, ProcessItem, StageEdits, Stages } from "@/api/workflows";
import { Button, Chip, Icon } from "@/components/ui";

/** An edit to the draft, made by the backend from the workflow model (never by editing the YAML here).
 *  `chosen` is the export folder the Deliver card picked, whose key is mapped only when the draft is saved. */
export type Edit = (edits: StageEdits, chosen?: DestinationChoice) => void;

const FIELD =
  "w-full rounded-[3px] border border-line bg-field px-2.5 py-1.5 font-sans text-[13px] text-ink outline-none focus:border-ink";
const MONO = "font-mono text-[12.5px]";

/** A draft as the three stages a workflow has: Extract → Process & QC → Deliver, each with plain fields. */
export function StageCards({
  stages,
  destinations,
  practice,
  busy,
  onEdit,
}: {
  stages: Stages;
  destinations: DestinationChoice[];
  practice: boolean;
  busy: boolean;
  onEdit: Edit;
}) {
  return (
    <ol aria-label="Stages" className="flex flex-col gap-4">
      <Card number={1} title="Extract" subtitle="What it pulls from the database, and the values you give each run.">
        <ExtractStage stages={stages} busy={busy} onEdit={onEdit} />
      </Card>
      <Card number={2} title="Process & QC" subtitle="What it does to the data, then what it checks before anything is delivered.">
        <ProcessStage stages={stages} busy={busy} onEdit={onEdit} />
      </Card>
      <Card number={3} title="Deliver" subtitle="Where the files go once every check has passed.">
        <DeliverStage stages={stages} destinations={destinations} practice={practice} busy={busy} onEdit={onEdit} />
      </Card>
    </ol>
  );
}

function Card({ number, title, subtitle, children }: { number: number; title: string; subtitle: string; children: ReactNode }) {
  const id = useId();
  return (
    <li aria-labelledby={id} className="rounded-[4px] border border-line bg-surface">
      <header className="flex items-baseline gap-3 border-b border-line px-4 py-2.5">
        <span aria-hidden className="grid size-6 shrink-0 place-items-center rounded-full bg-ink font-sans text-[11.5px] font-semibold text-surface">
          {number}
        </span>
        <div className="min-w-0">
          <h3 id={id} className="font-serif text-[18px] leading-tight">
            {title}
          </h3>
          <p className="font-sans text-[12.5px] text-muted">{subtitle}</p>
        </div>
      </header>
      <div className="flex flex-col gap-4 px-4 py-3">{children}</div>
    </li>
  );
}

// ------------------------------------------------------------------ Extract

function ExtractStage({ stages, busy, onEdit }: { stages: Stages; busy: boolean; onEdit: Edit }) {
  return (
    <>
      {stages.extract.length === 0 && (
        <p className="font-sans text-[13px] text-attn">No SQL step yet: ask the assistant, or add one in the YAML view.</p>
      )}
      {stages.extract.map((step) => (
        <div key={step.id} className="flex flex-col gap-2">
          <StepTitle id={step.id} note={`→ ${step.output}`} />
          <TextField
            label="What it extracts"
            value={step.description}
            placeholder="In plain words: which data, for whom"
            disabled={busy}
            onCommit={(description) => onEdit({ steps: { [step.id]: { description } } })}
          />
          {step.tables.length > 0 && (
            <p className="font-sans text-[12.5px] text-muted">
              Reads <span className={clsx(MONO, "text-ink")}>{step.tables.join(", ")}</span>
            </p>
          )}
          <details className="font-sans text-[12.5px] text-muted">
            <summary className="cursor-pointer hover:text-ink">The SQL</summary>
            <TextField
              label={`SQL of ${step.id}`}
              hideLabel
              multiline
              mono
              value={step.sql}
              disabled={busy}
              onCommit={(sql) => onEdit({ steps: { [step.id]: { sql } } })}
            />
          </details>
        </div>
      ))}
      <div className="flex flex-col gap-2">
        <p className="dl-label">Parameters</p>
        {stages.parameters.length === 0 ? (
          <p className="font-sans text-[13px] text-muted">None: every run pulls the same data.</p>
        ) : (
          <ul className="flex flex-col gap-2">
            {stages.parameters.map((p) => (
              <li key={p.name} className="grid gap-2 sm:grid-cols-[10rem_12rem_minmax(0,1fr)] sm:items-end">
                <span className={clsx(MONO, "text-ink sm:pb-2")}>
                  {p.name} <span className="font-sans text-[12px] text-faint">{p.type}</span>
                </span>
                <TextField
                  label={`Default for ${p.name}`}
                  type={p.type === "date" ? "date" : p.type === "integer" || p.type === "number" ? "number" : "text"}
                  value={p.default == null ? "" : String(p.default)}
                  placeholder="No default"
                  disabled={busy}
                  onCommit={(value) =>
                    onEdit({
                      parameters: {
                        [p.name]: value.trim()
                          ? { default: p.type === "integer" || p.type === "number" ? Number(value) : value }
                          : { clear_default: true },
                      },
                    })
                  }
                />
                <TextField
                  label={`What ${p.name} is`}
                  value={p.description}
                  placeholder="Description"
                  disabled={busy}
                  onCommit={(description) => onEdit({ parameters: { [p.name]: { description } } })}
                />
              </li>
            ))}
          </ul>
        )}
      </div>
    </>
  );
}

// ------------------------------------------------------------------ Process & QC

function ProcessStage({ stages, busy, onEdit }: { stages: Stages; busy: boolean; onEdit: Edit }) {
  const files = stages.outputs;
  return (
    <>
      {stages.process.length === 0 && (
        <p className="font-sans text-[13px] text-attn">
          Nothing is checked yet. Add at least a check that the file isn't empty before it's delivered.
        </p>
      )}
      <ul className="flex flex-col gap-3">
        {stages.process.map((item) => (
          <li key={item.id} className="border-l-2 border-line pl-3">
            {item.kind === "check" ? (
              <CheckFields item={item} files={files.map((f) => f.ref)} busy={busy} onEdit={onEdit} />
            ) : (
              <ProcessFields item={item} busy={busy} onEdit={onEdit} />
            )}
          </li>
        ))}
      </ul>
      <AddToProcess files={files.map((f) => f.ref)} busy={busy} onEdit={onEdit} />
    </>
  );
}

function ProcessFields({ item, busy, onEdit }: { item: ProcessItem; busy: boolean; onEdit: Edit }) {
  const reads = Object.values(item.inputs ?? {});
  return (
    <div className="flex flex-col gap-2">
      <StepTitle
        id={item.id}
        kind={item.kind === "pipeline" ? `pipeline ${item.pipeline}` : item.kind === "custom_check" ? "custom R check" : "R step"}
        note={[reads.length ? `reads ${reads.join(", ")}` : "", Object.values(item.outputs ?? {}).length ? `→ ${Object.values(item.outputs ?? {}).join(", ")}` : ""].filter(Boolean).join(" ")}
      />
      <TextField
        label="What it does"
        value={item.description}
        placeholder="In plain words"
        disabled={busy}
        onCommit={(description) => onEdit({ steps: { [item.id]: { description } } })}
      />
      {item.drop_columns != null ? (
        <ListField
          label="Columns it removes"
          value={item.drop_columns}
          disabled={busy}
          onCommit={(drop_columns) => onEdit({ steps: { [item.id]: { drop_columns } } })}
        />
      ) : (
        item.script && (
          <details className="font-sans text-[12.5px] text-muted">
            <summary className="cursor-pointer hover:text-ink">The R code (edit it in the YAML view)</summary>
            <pre className="mt-1 max-h-60 overflow-auto rounded-[3px] bg-sunken px-2.5 py-1.5 font-mono text-[12px] whitespace-pre text-ink">
              {item.script}
            </pre>
          </details>
        )
      )}
    </div>
  );
}

function CheckFields({ item, files, busy, onEdit }: { item: ProcessItem; files: string[]; busy: boolean; onEdit: Edit }) {
  const check = (change: NonNullable<StageEdits["checks"]>[string]) => onEdit({ checks: { [item.id]: change } });
  const small = item.small_cells;
  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap items-baseline gap-2">
        <StepTitle id={item.id} kind="check" />
        <span className="font-sans text-[12.5px] text-muted">on</span>
        <select
          aria-label={`File ${item.id} checks`}
          className={clsx(FIELD, "w-auto py-0.5", MONO)}
          value={item.file ?? ""}
          disabled={busy}
          onChange={(e) => check({ file: e.target.value })}
        >
          {files.map((f) => (
            <option key={f} value={f}>
              {f}
            </option>
          ))}
        </select>
        <Button
          variant="ghost"
          className="ml-auto px-1.5 py-0.5 text-[12px]"
          disabled={busy}
          onClick={() => onEdit({ remove_steps: [item.id] })}
        >
          <Icon name="trash" size={12} /> Remove check
        </Button>
      </div>
      <div className="grid gap-2 sm:grid-cols-2">
        <TextField
          label="At least this many rows"
          type="number"
          value={item.min_rows == null ? "" : String(item.min_rows)}
          placeholder="No minimum"
          disabled={busy}
          onCommit={(v) => check(v.trim() ? { min_rows: Number(v) } : { remove: ["min_rows"] })}
        />
        <ListField
          label="One row per (no duplicates of)"
          value={item.unique_by ?? []}
          placeholder="e.g. PARTICIPANTIDENTIFIER, RECORD_DATE"
          disabled={busy}
          onCommit={(unique_by) => check(unique_by.length ? { unique_by } : { remove: ["unique_by"] })}
        />
        <ListField
          label="Columns it must have"
          value={item.required_columns ?? []}
          disabled={busy}
          onCommit={(required_columns) => check({ required_columns })}
        />
        <ListField
          label="Columns with no missing values"
          value={item.no_missing ?? []}
          disabled={busy}
          onCommit={(no_missing) => check({ no_missing })}
        />
      </div>
      <div className="grid gap-2 sm:grid-cols-[minmax(0,1fr)_8rem]">
        <ListField
          label="Small counts hidden in (count columns)"
          value={small?.count_columns ?? []}
          placeholder="Only for files of counts"
          disabled={busy}
          onCommit={(count_columns) =>
            check(count_columns.length ? { small_cells: { count_columns, min: small?.min ?? 11 } } : { remove: ["small_cells"] })
          }
        />
        {small && (
          <TextField
            label="Smallest count shown"
            value={String(small.min)}
            disabled={busy}
            onCommit={(v) => check({ small_cells: { count_columns: small.count_columns, min: /^\d+$/.test(v) ? Number(v) : v } })}
          />
        )}
      </div>
      {(item.other_rules?.length ?? 0) > 0 && (
        <p className="font-sans text-[12px] text-faint">Also checks {item.other_rules!.join(", ")} (in the YAML view).</p>
      )}
    </div>
  );
}

function AddToProcess({ files, busy, onEdit }: { files: string[]; busy: boolean; onEdit: Edit }) {
  const [adding, setAdding] = useState<"check" | "drop" | null>(null);
  const [file, setFile] = useState(files[files.length - 1] ?? "");
  const [columns, setColumns] = useState("");
  if (files.length === 0) return null;
  if (!adding) {
    return (
      <div className="flex flex-wrap gap-2">
        <Button className="px-2.5 py-1 text-[12.5px]" disabled={busy} onClick={() => setAdding("check")}>
          + Add a check
        </Button>
        <Button className="px-2.5 py-1 text-[12.5px]" disabled={busy} onClick={() => setAdding("drop")}>
          + Remove columns
        </Button>
      </div>
    );
  }
  const list = splitList(columns);
  return (
    <form
      aria-label={adding === "check" ? "Add a check" : "Remove columns"}
      className="flex flex-wrap items-end gap-2 rounded-[3px] bg-sunken p-2.5"
      onSubmit={(e) => {
        e.preventDefault();
        if (adding === "check") onEdit({ add_checks: [{ file, min_rows: 1, ...(list.length ? { unique_by: list } : {}) }] });
        else if (list.length) onEdit({ add_drop_columns: [{ input: file, columns: list }] });
        setAdding(null);
        setColumns("");
      }}
    >
      <label className="flex flex-col gap-1 font-sans text-[12px] text-muted">
        {adding === "check" ? "Check the file of" : "From the file of"}
        <select className={clsx(FIELD, "w-auto", MONO)} value={file} onChange={(e) => setFile(e.target.value)}>
          {files.map((f) => (
            <option key={f} value={f}>
              {f}
            </option>
          ))}
        </select>
      </label>
      <label className="flex min-w-[14rem] flex-1 flex-col gap-1 font-sans text-[12px] text-muted">
        {adding === "check" ? "One row per (optional)" : "Columns to remove"}
        <input
          className={clsx(FIELD, MONO)}
          value={columns}
          onChange={(e) => setColumns(e.target.value)}
          placeholder={adding === "check" ? "PARTICIPANTIDENTIFIER, RECORD_DATE" : "BODYBMI, BODYFAT"}
        />
      </label>
      <Button type="submit" variant="primary" className="px-2.5 py-1 text-[12.5px]" disabled={busy || (adding === "drop" && !list.length)}>
        Add
      </Button>
      <Button type="button" variant="ghost" className="px-2 py-1 text-[12.5px]" onClick={() => setAdding(null)}>
        Cancel
      </Button>
    </form>
  );
}

// ------------------------------------------------------------------ Deliver

function DeliverStage({
  stages,
  destinations,
  practice,
  busy,
  onEdit,
}: {
  stages: Stages;
  destinations: DestinationChoice[];
  practice: boolean;
  busy: boolean;
  onEdit: Edit;
}) {
  const deliver = stages.deliver;
  const first = destinations[0];
  if (!deliver) {
    const last = stages.outputs[stages.outputs.length - 1];
    return (
      <div className="flex flex-col gap-2 font-sans text-[13px] text-muted">
        <p>Nothing is delivered: the outputs stay in the run's folder on this computer.</p>
        {!first ? (
          <NoFolders />
        ) : (
          last && (
            <Button
              className="self-start px-2.5 py-1 text-[12.5px]"
              disabled={busy}
              onClick={() => onEdit({ deliver: { destination: first.key, folder: stages.name, files: [last.ref] } }, first)}
            >
              Deliver {last.file} to {first.name}
            </Button>
          )
        )}
      </div>
    );
  }
  if (!first) {
    return (
      <div className="flex flex-col gap-2 font-sans text-[13px] text-muted">
        <p>
          It delivers to <span className={clsx(MONO, "text-ink")}>{deliver.destination}</span>, which has no export
          folder on this computer.
        </p>
        <NoFolders />
        <Button
          variant="ghost"
          className="self-start px-1.5 py-0.5 text-[12px]"
          disabled={busy}
          onClick={() => onEdit({ no_deliver: true })}
        >
          Don't deliver: keep the outputs in the run's folder
        </Button>
      </div>
    );
  }
  const current = destinations.find((d) => d.key === deliver.destination);
  return (
    <div className="flex flex-col gap-3">
      <label className="flex flex-col gap-1">
        <span className="dl-label">Export folder</span>
        <select
          aria-label="Export folder"
          className={FIELD}
          value={current ? current.key : ""}
          disabled={busy || practice}
          onChange={(e) => {
            const chosen = destinations.find((d) => d.key === e.target.value);
            if (chosen) onEdit({ deliver: { destination: chosen.key } }, chosen);
          }}
        >
          {!current && <option value="">{deliver.destination} (not set on this computer)</option>}
          {destinations.map((d) => (
            <option key={d.key} value={d.key}>
              {d.name}
            </option>
          ))}
        </select>
        <span className="font-sans text-[12px] text-muted">
          {practice
            ? "Practice DataLab saves only to its own practice exports folder, on this computer."
            : "Files are written to this folder on this computer (saved locally). The file names it by a key, so it works on everyone's computer."}{" "}
          {current && <span className={clsx(MONO, "break-all text-ink")}>{current.path}</span>}
        </span>
        {current?.location_note && (
          <span className="font-sans text-[12px] text-muted">{current.location_note}</span>
        )}
      </label>
      <TextField
        label="Subfolder"
        mono
        value={deliver.folder}
        disabled={busy}
        onCommit={(folder) => onEdit({ deliver: { folder } })}
      />
      <fieldset className="flex flex-col gap-1">
        <legend className="dl-label mb-1">Files</legend>
        {stages.outputs.map((o) => (
          <label key={o.ref} className="flex items-center gap-2 font-sans text-[13px]">
            <input
              type="checkbox"
              checked={deliver.files.includes(o.ref)}
              disabled={busy}
              onChange={(e) =>
                onEdit({
                  deliver: {
                    files: e.target.checked ? [...deliver.files, o.ref] : deliver.files.filter((f) => f !== o.ref),
                  },
                })
              }
            />
            <span className={MONO}>{o.file}</span> <span className="text-faint">from {o.step}</span>
          </label>
        ))}
      </fieldset>
      {Object.entries(deliver.without_small_cells ?? {}).map(([file, why]) => (
        <p key={file} className="font-sans text-[12.5px] text-muted">
          <Chip>no small-cells check</Chip> <span className={MONO}>{file}</span>: {why}
        </p>
      ))}
      <Button
        variant="ghost"
        className="self-start px-1.5 py-0.5 text-[12px]"
        disabled={busy}
        onClick={() => onEdit({ no_deliver: true })}
      >
        Don't deliver: keep the outputs in the run's folder
      </Button>
    </div>
  );
}

/** No export folder to choose: where to add one. */
function NoFolders() {
  return (
    <p role="status" className="flex items-baseline gap-1.5 font-sans text-[13px] text-attn">
      <Icon name="folder" size={13} className="shrink-0 translate-y-[2px]" />
      <span>
        No export folders are set up yet. Add an export folder in{" "}
        <Link to="/settings" className="text-ink underline decoration-faint underline-offset-4 hover:decoration-ink">
          Settings → Export folders
        </Link>
        , then choose it here. Until then the outputs stay in the run's folder on this computer.
      </span>
    </p>
  );
}

// ------------------------------------------------------------------ fields

function StepTitle({ id, kind, note }: { id: string; kind?: string; note?: string }) {
  return (
    <p className="flex flex-wrap items-baseline gap-x-2 font-sans text-[12.5px] text-muted">
      <span className={clsx(MONO, "text-ink")}>{id}</span>
      {kind && <span>{kind}</span>}
      {note && <span className="font-mono text-[12px] text-faint">{note}</span>}
    </p>
  );
}

/** A field that sends its value when the person leaves it (or presses Enter), not on every key. */
function TextField({
  label,
  value,
  onCommit,
  placeholder,
  disabled,
  multiline,
  mono,
  hideLabel,
  type = "text",
}: {
  label: string;
  value: string;
  onCommit: (value: string) => void;
  placeholder?: string;
  disabled?: boolean;
  multiline?: boolean;
  mono?: boolean;
  hideLabel?: boolean;
  type?: string;
}) {
  const [draft, setDraft] = useState(value);
  // The draft follows the file whenever the file changes under it (another edit, the YAML view).
  const [seen, setSeen] = useState(value);
  if (seen !== value) {
    setSeen(value);
    setDraft(value);
  }
  const commit = () => {
    if (draft !== value) onCommit(draft);
  };
  const common = {
    "aria-label": label,
    value: draft,
    placeholder,
    disabled,
    onChange: (e: { target: { value: string } }) => setDraft(e.target.value),
    onBlur: commit,
    className: clsx(FIELD, mono && MONO),
  };
  return (
    <label className="flex min-w-0 flex-col gap-1">
      {!hideLabel && <span className="font-sans text-[12px] text-muted">{label}</span>}
      {multiline ? (
        <textarea {...common} rows={Math.min(14, Math.max(3, draft.split("\n").length))} spellCheck={false} />
      ) : (
        <input
          {...common}
          type={type}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              commit();
            }
          }}
        />
      )}
    </label>
  );
}

/** A list of column names, written with commas. */
function ListField({
  label,
  value,
  onCommit,
  placeholder,
  disabled,
}: {
  label: string;
  value: string[];
  onCommit: (value: string[]) => void;
  placeholder?: string;
  disabled?: boolean;
}) {
  return (
    <TextField
      label={label}
      mono
      value={value.join(", ")}
      placeholder={placeholder}
      disabled={disabled}
      onCommit={(text) => onCommit(splitList(text))}
    />
  );
}

export function splitList(text: string): string[] {
  return text
    .split(/[,\s]+/)
    .map((s) => s.trim())
    .filter(Boolean);
}
