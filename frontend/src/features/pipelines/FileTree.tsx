import clsx from "clsx";
import { useMemo, useState } from "react";

import { Icon } from "@/components/ui";

import { type Folder, folderTree } from "./pipelines";

// Open to begin with: the package and the workflow files, what the tab is for.
const OPEN = new Set(["ihsDataR/", "ihsDataR/R/", "ihsDataR/inst/", "ihsDataR/inst/pipelines/", "workflows/"]);

/** The repo's files, by folder, with a filter. */
export function FileTree({
  files,
  selected,
  onOpen,
}: {
  files: { path: string; size: number }[];
  selected: string | null;
  onOpen: (path: string) => void;
}) {
  const [filter, setFilter] = useState("");
  const [open, setOpen] = useState<Set<string>>(OPEN);
  const shown = useMemo(() => {
    const words = filter.trim().toLowerCase();
    return folderTree(words ? files.filter((f) => f.path.toLowerCase().includes(words)) : files);
  }, [files, filter]);
  const toggle = (path: string) => {
    const next = new Set(open);
    if (next.has(path)) next.delete(path);
    else next.add(path);
    setOpen(next);
  };
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <label className="mx-3 mt-3 mb-2 flex items-center gap-2 rounded-[3px] border border-line bg-field px-2 py-1 focus-within:border-ink">
        <Icon name="search" size={13} />
        <span className="sr-only">Find a file</span>
        <input
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          placeholder="Find a file"
          className="min-w-0 flex-1 bg-transparent font-sans text-[13px] outline-none"
        />
      </label>
      <ul aria-label="The pipelines repo's files" className="min-h-0 flex-1 overflow-y-auto px-2 pb-3">
        <Items folder={shown} depth={0} open={filter ? null : open} toggle={toggle} selected={selected} onOpen={onOpen} />
      </ul>
      {files.length > 0 && shown.folders.length === 0 && shown.files.length === 0 && (
        <p className="px-4 pb-3 font-sans text-[13px] text-muted">No file's name has “{filter}” in it.</p>
      )}
    </div>
  );
}

function Items({
  folder,
  depth,
  open,
  toggle,
  selected,
  onOpen,
}: {
  folder: Folder;
  depth: number;
  // null: every folder open (while filtering).
  open: Set<string> | null;
  toggle: (path: string) => void;
  selected: string | null;
  onOpen: (path: string) => void;
}) {
  const indent = { paddingLeft: `${0.5 + depth * 0.85}rem` };
  return (
    <>
      {folder.folders.map((child) => {
        const expanded = open === null || open.has(child.path);
        return (
          <li key={child.path}>
            <button
              type="button"
              aria-expanded={expanded}
              onClick={() => toggle(child.path)}
              style={indent}
              className="flex w-full items-center gap-1.5 rounded-[3px] py-1 pr-2 text-left font-sans text-[13px] text-ink hover:bg-surface/70"
            >
              <span className={clsx("inline-flex transition-transform", expanded ? "rotate-90" : "")}>
                <Icon name="chevron" size={11} />
              </span>
              {child.name}
            </button>
            {expanded && (
              <ul aria-label={child.name}>
                <Items folder={child} depth={depth + 1} open={open} toggle={toggle} selected={selected} onOpen={onOpen} />
              </ul>
            )}
          </li>
        );
      })}
      {folder.files.map((file) => (
        <li key={file.path}>
          <button
            type="button"
            aria-current={file.path === selected ? "true" : undefined}
            onClick={() => onOpen(file.path)}
            style={{ paddingLeft: `${1.35 + depth * 0.85}rem` }}
            className={clsx(
              "block w-full truncate rounded-[3px] py-1 pr-2 text-left font-mono text-[12.5px]",
              file.path === selected ? "bg-surface text-ink shadow-[inset_0_0_0_1px_var(--color-line)]" : "text-muted hover:bg-surface/70 hover:text-ink",
            )}
            title={file.path}
          >
            {file.name}
          </button>
        </li>
      ))}
    </>
  );
}
