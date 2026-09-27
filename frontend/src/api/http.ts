// The one fetch helper every API file uses. Each feature's calls live in their
// own file beside this one (conversations.ts, files.ts, sql.ts...), so a tab's
// work never has to touch another's.

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
    /** The response's whole `detail`, when it's more than a sentence (`{message, problems}`). */
    readonly detail?: unknown,
  ) {
    super(message);
  }
}

/** Fired when DataLab no longer knows this browser (it restarted, or the sign-in expired). */
export const SIGNED_OUT = "datalab:signed-out";

export async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: { "content-type": "application/json", ...init?.headers },
  });
  if (!response.ok) {
    if (response.status === 401) window.dispatchEvent(new Event(SIGNED_OUT));
    const body = await response.json().catch(() => ({}));
    const detail = typeof body.detail === "string" ? body.detail : (body.detail?.message ?? response.statusText);
    throw new ApiError(response.status, typeof detail === "string" ? detail : response.statusText, body.detail);
  }
  return response.status === 204 ? (undefined as T) : response.json();
}

/** What `GET /api/<feature>/status` answers: whether that tab's backend is ready to use. */
export interface FeatureStatus {
  available: boolean;
}
