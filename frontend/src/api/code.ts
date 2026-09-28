// The Code tab: a conversation's scripts, SQL and notebooks, each saved
// version, diffs, and the code it ran inline (backend api/code.py).
import { request } from "./http";
import type { components } from "./schema";

type Schemas = components["schemas"];
export type CodeListing = Schemas["CodeListingOut"];
export type CodeFile = Schemas["CodeFileOut"];
export type CodeVersion = Schemas["CodeVersionOut"];
export type InlineCode = Schemas["InlineCodeOut"];
export type CodeText = Schemas["CodeTextOut"];
export type CodeDiff = Schemas["CodeDiffOut"];

const query = (params: Record<string, string | number | boolean | null | undefined>) =>
  new URLSearchParams(
    Object.entries(params).flatMap(([key, value]) => (value == null ? [] : [[key, String(value)]])),
  ).toString();

export const codeApi = {
  /** Code this conversation made or changed; every code file with `all`. */
  list: (id: string, all = false) => request<CodeListing>(`/api/conversations/${id}/code?${query({ all })}`),
  /** A code file (a path in /work) as saved at `checkpoint`, else as it is now. */
  version: (id: string, path: string, checkpoint?: number | null) =>
    request<CodeText>(`/api/conversations/${id}/code/version?${query({ path, checkpoint })}`),
  /** What changed from the version at `base` to the one at `head` (else as it is now). */
  diff: (id: string, path: string, base: number, head?: number | null) =>
    request<CodeDiff>(`/api/conversations/${id}/code/diff?${query({ path, base, head })}`),
};
