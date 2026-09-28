// This browser's sign-in to DataLab (backend web.py): the cookie the one-time
// sign-in link set. Ending it signs every window of this DataLab out.
import { request } from "./http";

export const sessionApi = {
  /** End session: the cookie stops working. Signing in again takes the link DataLab prints when it next starts. */
  end: () => request<void>("/api/session/end", { method: "POST" }),
};
