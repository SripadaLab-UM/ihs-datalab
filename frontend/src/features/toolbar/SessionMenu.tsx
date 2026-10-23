// End session, in a menu of its own at the far right, apart from the
// shortcuts, and always confirmed. Ending it makes DataLab forget this
// browser's sign-in (the cookie the one-time link set) in every window.
// DataLab keeps running; signing in again takes the new link it makes when it
// next starts, from the launcher.
//
// On the lab's shared DataLab (a server) it's Sign out: the menu says who's
// signed in, and signing in again is the GitHub button on the page it leaves for.
import { useMutation, useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { api } from "@/api/client";
import { type SessionActivity, sessionApi } from "@/api/session";
import { usePracticeTab } from "@/app/brand";
import { GitHubSignIn } from "@/app/SignIn";
import { clearAllDrafts } from "@/components/chat/plan";
import { at } from "@/lib/base";
import { Button, Icon, Modal } from "@/components/ui";

import { ITEM, Shortcut } from "./Popover";

export const SESSION_ENDED_PATH = "/session-ended";

/** Leaves the app for the "session ended" page: a full load, so nothing of this window's state stays. */
export function leaveApp(path: string) {
  window.location.assign(at(path));
}

export function SessionMenu({ onEnded = () => leaveApp(SESSION_ENDED_PATH) }: { onEnded?: () => void } = {}) {
  const [confirming, setConfirming] = useState(false);
  const me = useQuery({ queryKey: ["session-me"], queryFn: sessionApi.me, staleTime: 5 * 60_000 });
  if (me.data?.deployment === "server") return <SignOutMenu name={me.data.name} login={me.data.login} />;
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
      {confirming && <EndSessionDialog onClose={() => setConfirming(false)} onEnded={onEnded} />}
    </>
  );
}

/** The lab's shared DataLab: who's signed in, and Sign out (no restart needed to sign in again). */
function SignOutMenu({ name, login }: { name: string; login: string }) {
  const out = useMutation({
    mutationFn: sessionApi.end,
    onSuccess: () => {
      clearAllDrafts(); // plan edits in this tab go with the sign-in
      leaveApp(SESSION_ENDED_PATH);
    },
  });
  return (
    <Shortcut kind="menu" icon="user" label="Session" title={`Signed in as ${name}`} width="w-64">
      {(close) => (
        <>
          <p role="none" className="px-3 pt-1.5 pb-2 text-[12.5px] text-muted">
            Signed in as <span className="font-medium text-ink">{name}</span>
            {login !== name && <span> (@{login})</span>}
          </p>
          <div role="separator" className="mb-1 border-t border-line" />
          <button
            type="button"
            role="menuitem"
            tabIndex={-1}
            className={ITEM}
            disabled={out.isPending}
            onClick={() => {
              close();
              out.mutate();
            }}
          >
            <Icon name="close" size={14} /> Sign out
          </button>
        </>
      )}
    </Shortcut>
  );
}

export function EndSessionDialog({ onClose, onEnded }: { onClose: () => void; onEnded: () => void }) {
  const end = useMutation({
    mutationFn: sessionApi.end,
    onSuccess: () => {
      clearAllDrafts(); // plan edits in this tab go with the sign-in
      onEnded();
    },
  });
  const activity = useQuery({ queryKey: ["session-activity"], queryFn: sessionApi.activity, staleTime: 0 });
  const stillGoing = activityWarning(activity.data);
  return (
    <Modal title="End this session?" onClose={onClose}>
      <p className="text-sm">You'll need a new sign-in link from the launcher.</p>
      <p className="mt-2 text-sm font-medium">
        DataLab only makes a sign-in link when it starts, so to get back in you'll have to quit DataLab (close its
        window, Terminal on a Mac or PowerShell on Windows, or press Ctrl-C in it) and start it again from its
        launcher. The old link won't work again.
      </p>
      {stillGoing && (
        <p role="status" className="mt-2 flex items-start gap-2 text-sm font-medium text-attn">
          <Icon name="alert" size={14} className="mt-0.5 shrink-0" />
          <span>{stillGoing}</span>
        </p>
      )}
      <p className="mt-2 text-sm text-muted">
        Every DataLab window in this browser is signed out. Until you quit it, DataLab keeps running, and anything
        already under way carries on.
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

/** What's still going, which a restart to get back in would stop; null when nothing is. */
export function activityWarning(activity: SessionActivity | undefined): string | null {
  if (!activity) return null;
  const going =
    activity.agent_turn && activity.workflow_run
      ? "An agent turn and a workflow run are"
      : activity.agent_turn
        ? "An agent turn is"
        : activity.workflow_run
          ? "A workflow run is"
          : null;
  if (!going) return null;
  const them = activity.agent_turn && activity.workflow_run ? "them" : "it";
  return `${going} still going; ${them === "it" ? "it carries" : "they carry"} on, but restarting DataLab to get back in will stop ${them}.`;
}

/** Where End session leaves the window: no API calls but /api/health, which needs no sign-in. */
export function SessionEnded() {
  const health = useQuery({ queryKey: ["health"], queryFn: api.health, retry: false });
  usePracticeTab(health.data?.profile === "practice");
  if (health.data?.deployment === "server") return <GitHubSignIn reason="signed-out" />;
  return (
    <div className="flex h-full flex-col items-center justify-center gap-3 px-6 text-center">
      <p className="font-serif text-[28px] text-ink">Session ended</p>
      <p className="max-w-[32rem] text-sm text-muted">
        DataLab no longer knows this browser. To sign in again, quit DataLab (close its window, Terminal on a Mac or
        PowerShell on Windows, or press Ctrl-C in it) and start it again from its launcher: it makes a new sign-in
        link each time it starts. The old link won't work again.
      </p>
    </div>
  );
}
