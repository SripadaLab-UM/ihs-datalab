// This browser's sign-in to DataLab (backend web.py): the cookie the one-time
// sign-in link set. Ending it signs every window of this DataLab out.
import { request } from "./http";
import type { components } from "./schema";

export type SessionActivity = components["schemas"]["SessionActivityOut"];

export const sessionApi = {
  /** What's still going: it carries on after End session, but restarting DataLab to get back in stops it. */
  activity: () => request<SessionActivity>("/api/session/activity"),
  /** End session: the cookie stops working. Signing in again takes the link DataLab prints when it next starts. */
  end: () => request<void>("/api/session/end", { method: "POST" }),
};
