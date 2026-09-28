// End session, in a menu of its own at the far right, apart from the
// shortcuts, and always confirmed. Ending it makes DataLab forget this
// browser's sign-in (the cookie the one-time link set) in every window.
// DataLab keeps running; signing in again takes the new link it makes when it
// next starts, from the launcher.
import { useMutation } from "@tanstack/react-query";
import { useState } from "react";

import { sessionApi } from "@/api/session";
import { clearAllDrafts } from "@/components/chat/plan";
import { Button, Icon, Modal } from "@/components/ui";

import { ITEM, Shortcut } from "./Popover";

export const SESSION_ENDED_PATH = "/session-ended";

/** Leaves the app for the "session ended" page: a full load, so nothing of this window's state stays. */
export function leaveApp(path: string) {
  window.location.assign(path);
}

export function SessionMenu() {
  const [confirming, setConfirming] = useState(false);
  return (
    <>
      <Shortcut kind="menu" icon="user" label="Session" title="This window's DataLab session: End session" width="w-64">
        {(close) => (
          <>
            <p role="none" className="px-3 pt-1.5 pb-2 text-[12.5px] text-muted">
              Signed in to this DataLab in this browser.
            </p>
            <div role="separator" className="mb-1 border-t border-line" />
            <button
              type="button"
              role="menuitem"
              tabIndex={-1}
              className={`${ITEM} text-danger`}
              onClick={() => {
                close();
                setConfirming(true);
              }}
            >
              <Icon name="close" size={14} /> End session…
            </button>
          </>
        )}
      </Shortcut>
      {confirming && <EndSessionDialog onClose={() => setConfirming(false)} />}
    </>
  );
}

export function EndSessionDialog({ onClose }: { onClose: () => void }) {
  const end = useMutation({
    mutationFn: sessionApi.end,
    onSuccess: () => {
      clearAllDrafts(); // plan edits in this tab go with the sign-in
      leaveApp(SESSION_ENDED_PATH);
    },
  });
  return (
    <Modal title="End this session?" onClose={onClose}>
      <p className="text-sm">You'll need a new sign-in link from the launcher.</p>
      <p className="mt-2 text-sm text-muted">
        Every DataLab window in this browser is signed out. DataLab itself keeps running, and anything already under
        way carries on. To sign in again, quit DataLab and open it from its launcher: it makes a new sign-in link each
        time it starts.
      </p>
      {end.isError && <p className="mt-3 text-sm text-danger">{end.error.message}</p>}
      <div className="mt-5 flex justify-end gap-2">
        <Button onClick={onClose} autoFocus>
          Cancel
        </Button>
        <Button variant="danger" onClick={() => end.mutate()} disabled={end.isPending}>
          {end.isPending ? "Ending…" : "End session"}
        </Button>
      </div>
    </Modal>
  );
}

/** Where End session leaves the window. */
export function SessionEnded() {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-3 px-6 text-center">
      <p className="font-serif text-[28px] text-ink">Session ended</p>
      <p className="max-w-[32rem] text-sm text-muted">
        DataLab no longer knows this browser. To sign in again, quit DataLab and open it from its launcher: it makes a
        new sign-in link each time it starts.
      </p>
    </div>
  );
}
