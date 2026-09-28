import clsx from "clsx";
import { type KeyboardEvent, type ReactNode, useEffect, useMemo, useRef, useState } from "react";

import type { KbEntry } from "@/api/knowledge";
import { Chip, Icon } from "@/components/ui";

import { statusTone } from "./pages";
import { ancestorsOf, buildTree, filterTree, pageId, searchWords, type TreeNode, visibleRows } from "./tree";

// Which sections and groups this viewer has open, kept across reloads.
export const OPEN_KEY = "datalab:kb:tree-open";

function loadOpen(): Set<string> {
  try {
    const saved: unknown = JSON.parse(localStorage.getItem(OPEN_KEY) ?? "[]");
    return new Set(Array.isArray(saved) ? saved.filter((id): id is string => typeof id === "string") : []);
  } catch {
    return new Set();
  }
}

function saveOpen(open: Set<string>) {
  try {
    localStorage.setItem(OPEN_KEY, JSON.stringify([...open]));
  } catch {
    // Storage can be refused (a private window): the choices last until the page goes.
  }
}

/**
 * The knowledge base's pages and skills as a tree (WAI-ARIA tree pattern):
 * sections start folded, opening a page opens what holds it, and the person's
 * own choices are kept. Search looks in folded sections too, and shows the
 * matches opened without touching those choices.
 */
export function KnowledgeTree({
  entries,
  selected,
  reveal,
  onOpen,
  loading,
}: {
  entries: KbEntry[];
  /** The page shown, highlighted when it's in view. */
  selected: string;
  /** Whether to open what holds the selected page: when it was chosen, not the index shown by default. */
  reveal: boolean;
  onOpen: (path: string) => void;
  loading: boolean;
}) {
  const tree = useMemo(() => buildTree(entries), [entries]);
  const [typed, setTyped] = useState("");
  const words = useMemo(() => searchWords(typed), [typed]);
  const searching = words.length > 0;
  const found = useMemo(() => (searching ? filterTree(tree, words) : null), [tree, words, searching]);
  const shown = found?.tree ?? tree;

  // The person's own choices, and, while searching, what they've changed from
  // the search's view (dropped when the search changes).
  const [saved, setSaved] = useState(loadOpen);
  const [toggled, setToggled] = useState<{ query: string; ids: Set<string> }>({
    query: "",
    ids: new Set(),
  });
  const query = words.join(" ");
  const flipped = toggled.query === query ? toggled.ids : new Set<string>();
  const isOpen = (id: string) => (found ? found.open.has(id) !== flipped.has(id) : saved.has(id));
  const setOpen = (id: string, open: boolean) => {
    if (isOpen(id) === open) return;
    if (found) {
      const ids = new Set(flipped);
      if (ids.has(id)) ids.delete(id);
      else ids.add(id);
      setToggled({ query, ids });
    } else {
      const next = new Set(saved);
      if (open) next.add(id);
      else next.delete(id);
      saveOpen(next);
      setSaved(next);
    }
  };

  const rowRefs = useRef(new Map<string, HTMLLIElement>());
  const scrollTo = (id: string) => requestAnimationFrame(() => rowRefs.current.get(id)?.scrollIntoView?.({ block: "nearest" }));

  // Opening a page opens what holds it (once per page chosen, so folding it
  // again afterwards sticks), kept with the person's choices.
  const revealed = useRef<string | null>(null);
  useEffect(() => {
    if (!reveal || !selected || revealed.current === selected) return;
    const above = ancestorsOf(tree, selected);
    if (above.length === 0) return; // not listed (yet)
    revealed.current = selected;
    setSaved((prev) => {
      if (above.every((id) => prev.has(id))) return prev;
      const next = new Set([...prev, ...above]);
      saveOpen(next);
      return next;
    });
    scrollTo(pageId(selected));
  }, [tree, selected, reveal]);
  // Clearing the search goes back to the person's choices, the selected page in view.
  const wasSearching = useRef(false);
  useEffect(() => {
    if (wasSearching.current && !searching && selected) scrollTo(pageId(selected));
    wasSearching.current = searching;
  }, [searching, selected]);

  // Roving focus: one row is in the tab order, the arrow keys move it.
  const rows = visibleRows(shown, isOpen);
  const [focusId, setFocusId] = useState<string | null>(null);
  const tabbable = rows.find((r) => r.node.id === focusId)?.node.id ?? rows.find((r) => r.node.id === pageId(selected))?.node.id ?? rows[0]?.node.id;
  const moveTo = (id: string | undefined) => {
    if (!id) return;
    setFocusId(id);
    rowRefs.current.get(id)?.focus();
  };
  const activate = (n: TreeNode) => {
    if (n.entry) onOpen(n.entry.path);
    else setOpen(n.id, !isOpen(n.id));
  };

  const onKeyDown = (event: KeyboardEvent<HTMLUListElement>) => {
    const at = rows.findIndex((r) => r.node.id === (document.activeElement as HTMLElement | null)?.dataset.node);
    if (at < 0) return;
    const { node: n, parent } = rows[at];
    const hasChildren = n.children.length > 0;
    const handled = () => {
      event.preventDefault();
      event.stopPropagation();
    };
    switch (event.key) {
      case "ArrowDown":
        handled();
        return moveTo(rows[at + 1]?.node.id);
      case "ArrowUp":
        handled();
        return moveTo(rows[at - 1]?.node.id);
      case "Home":
        handled();
        return moveTo(rows[0]?.node.id);
      case "End":
        handled();
        return moveTo(rows.at(-1)?.node.id);
      case "ArrowRight":
        handled();
        if (!hasChildren) return;
        return isOpen(n.id) ? moveTo(n.children[0]?.id) : setOpen(n.id, true);
      case "ArrowLeft":
        handled();
        return hasChildren && isOpen(n.id) ? setOpen(n.id, false) : moveTo(parent ?? undefined);
      case "Enter":
      case " ":
        handled();
        return activate(n);
    }
  };

  const renderNodes = (nodes: TreeNode[], level: number): ReactNode =>
    nodes.map((n, i) => {
      const hasChildren = n.children.length > 0;
      const open = hasChildren && isOpen(n.id);
      const isSelected = n.entry ? n.entry.path === selected : undefined;
      const context = found && n.entry && !found.hits.has(n.id); // shown only to place a match
      const status = n.entry?.status && n.entry.status !== "reviewed" ? n.entry.status : null;
      const name = n.entry ? [n.label, status].filter(Boolean).join(", ") : `${n.label}, ${n.count} ${n.count === 1 ? "page" : "pages"}`;
      return (
        <li
          key={n.id}
          ref={(el) => {
            if (el) rowRefs.current.set(n.id, el);
            else rowRefs.current.delete(n.id);
          }}
          role="treeitem"
          data-node={n.id}
          aria-label={name}
          aria-level={level}
          aria-setsize={nodes.length}
          aria-posinset={i + 1}
          aria-expanded={hasChildren ? open : undefined}
          aria-selected={isSelected}
          aria-current={isSelected ? "page" : undefined}
          tabIndex={n.id === tabbable ? 0 : -1}
          onFocus={(e) => e.target === e.currentTarget && setFocusId(n.id)}
          className="outline-none [&:focus-visible>div]:outline-[1.5px] [&:focus-visible>div]:outline-offset-[-1.5px] [&:focus-visible>div]:outline-ink [&:focus-visible>div]:outline-solid"
        >
          <div
            onClick={() => {
              setFocusId(n.id);
              activate(n);
            }}
            title={n.entry?.path}
            style={{ paddingLeft: `${(level - 1) * 14 + 2}px` }}
            className={clsx(
              "flex cursor-pointer items-start gap-1 rounded-[3px] py-1 pr-2 select-none",
              isSelected ? "bg-accent-soft" : "hover:bg-sunken",
              level === 1 && "mt-1",
            )}
          >
            {hasChildren ? (
              <span
                aria-hidden
                onClick={(e) => {
                  e.stopPropagation();
                  setFocusId(n.id);
                  setOpen(n.id, !open);
                }}
                className="mt-[2px] shrink-0 rounded-[2px] p-0.5 text-faint hover:bg-accent-soft hover:text-ink"
              >
                <Icon name="chevron" size={11} className={clsx("transition-transform", open && "rotate-90")} />
              </span>
            ) : (
              <span aria-hidden className="w-[15px] shrink-0" />
            )}
            <span className="flex min-w-0 flex-1 flex-col gap-0.5">
              <span className="flex min-w-0 items-center gap-1.5">
                <span
                  className={clsx(
                    "min-w-0 truncate",
                    !n.entry
                      ? level === 1
                        ? "dl-label text-ink"
                        : "font-sans text-[13px] font-medium text-ink"
                      : n.entry.place === "skill_file"
                        ? "font-mono text-[12px]"
                        : "font-sans text-[13px]",
                    n.entry && (context ? "text-muted" : "text-ink"),
                  )}
                >
                  {n.label}
                </span>
                {hasChildren && <span className="shrink-0 font-sans text-[11.5px] text-faint tabular">· {n.count}</span>}
                {status && <Chip tone={statusTone(status)}>{status}</Chip>}
              </span>
              {n.entry?.summary && !context && <span className="truncate font-sans text-[11.5px] leading-snug text-muted">{n.entry.summary}</span>}
            </span>
          </div>
          {open && (
            <ul role="group" className="flex flex-col">
              {renderNodes(n.children, level + 1)}
            </ul>
          )}
        </li>
      );
    });

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="px-4 pt-4 pb-2">
        <label className="relative block">
          <span className="sr-only">Search pages and skills</span>
          <Icon name="search" size={13} className="pointer-events-none absolute top-1/2 left-2.5 -translate-y-1/2 text-faint" />
          <input
            type="search"
            value={typed}
            onChange={(e) => setTyped(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "ArrowDown" && rows.length) {
                e.preventDefault();
                moveTo(found ? (rows.find((r) => found.hits.has(r.node.id)) ?? rows[0]).node.id : tabbable);
              }
            }}
            placeholder="Search pages and skills"
            className="w-full rounded-[3px] border border-line bg-field py-1 pr-2 pl-8 font-sans text-[13px] outline-none placeholder:text-faint focus:border-ink"
          />
        </label>
        {found && (
          <p className="mt-1.5 font-sans text-[11.5px] text-faint" aria-live="polite">
            {found.hits.size === 1 ? "1 matching page" : `${found.hits.size} matching pages`}
          </p>
        )}
      </div>
      <nav aria-label="Knowledge base" className="min-h-0 flex-1 overflow-y-auto px-2 pb-4">
        {loading && <p className="px-2 font-sans text-[13px] text-muted">Loading…</p>}
        {!loading && shown.length === 0 && (
          <p className="px-2 font-sans text-[13px] text-muted">{searching ? "Nothing matches." : "No pages yet."}</p>
        )}
        {shown.length > 0 && (
          <ul role="tree" aria-label="Pages and skills" onKeyDown={onKeyDown} className="flex flex-col">
            {renderNodes(shown, 1)}
          </ul>
        )}
      </nav>
    </div>
  );
}
