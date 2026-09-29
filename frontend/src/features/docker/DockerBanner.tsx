// Docker on Windows (backend api/docker.py, windows_vm.py). While Docker can't
// run, every page says so in a banner at the top, with what to do. When a
// Windows policy has stopped Docker's virtual machine, a dialog opens by itself
// (once a session, unless put off with Later) and walks through the fix: turn
// on the temporary administrator access (on a Michigan Medicine computer, from
// the profile page), then answer one Windows permission box. DataLab never
// shows that box without the person asking.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type ReactNode, useEffect, useRef, useState } from "react";

import { type DockerFix, type DockerStatus, dockerApi } from "@/api/docker";
import { Button, Icon, Modal } from "@/components/ui";

const KEY = ["docker"];
// Nothing to sort out: checked now and then, as a policy can take the right away while DataLab is open.
const SETTLED = new Set<DockerStatus["state"]>(["ready", "unsupported"]);
// What a check after the fix means it worked: Windows lets the VM start again.
const VM_STARTS = new Set<DockerStatus["state"]>(["ready", "starting", "stopped"]);
// "Later" holds for this browser session (the banner stays and reopens the dialog).
const LATER = "datalab.docker-fix-later";

export function useDockerStatus() {
  return useQuery({
    queryKey: KEY,
    queryFn: dockerApi.status,
    refetchInterval: (query) => (query.state.data && SETTLED.has(query.state.data.state) ? 5 * 60_000 : 30_000),
    retry: false,
  });
}

function putOffUntilLater() {
  try {
    sessionStorage.setItem(LATER, "1");
  } catch {
    // Private windows may refuse storage: it then opens again next time.
  }
}

function putOff(): boolean {
  try {
    return sessionStorage.getItem(LATER) === "1";
  } catch {
    return false;
  }
}

function Bar({ children, urgent = false }: { children: ReactNode; urgent?: boolean }) {
  return (
    <div
      role={urgent ? "alert" : "status"}
      className="flex flex-wrap items-center justify-center gap-x-3 gap-y-1 border-b border-attn/30 bg-attn-soft px-4 py-2 text-center text-sm font-medium text-ink"
    >
      <Icon name="alert" size={14} className="shrink-0 text-attn" />
      {children}
    </div>
  );
}

export function DockerBanner() {
  const client = useQueryClient();
  const docker = useDockerStatus();
  const state = docker.data?.state;
  const [open, setOpen] = useState(false);
  // The dialog opens by itself once per page, unless put off this session;
  // after that the banner's button opens it.
  const opened = useRef(false);
  useEffect(() => {
    if (state === "vm-refused" && !opened.current) {
      opened.current = true;
      if (!putOff()) setOpen(true);
    }
  }, [state]);
  const start = useMutation({ mutationFn: dockerApi.start, onSuccess: (status) => client.setQueryData(KEY, status) });
  if (!docker.data) return null;
  return (
    <>
      {state === "vm-refused" && (
        <Bar urgent>
          <span>Docker can't start: Windows is blocking its virtual machine, so conversations and workflows can't run.</span>
          <Button className="py-0.5" onClick={() => setOpen(true)}>
            How to fix it…
          </Button>
        </Bar>
      )}
      {state === "not-installed" && (
        <Bar>
          <span>
            Docker Desktop isn't installed, so conversations and workflows can't run. Run the DataLab installer again: it
            sets Docker Desktop up.
          </span>
        </Bar>
      )}
      {state === "stopped" && (
        <Bar>
          <span>Docker Desktop isn't running, so conversations and workflows can't start.</span>
          <Button className="py-0.5" disabled={start.isPending} onClick={() => start.mutate()}>
            {start.isPending ? "Opening…" : "Open Docker Desktop"}
          </Button>
        </Bar>
      )}
      {state === "starting" && (
        <Bar>
          <span>Docker Desktop is starting. Conversations and workflows can start once it's ready (a minute or two).</span>
        </Bar>
      )}
      {open && (
        <DockerFixDialog
          status={docker.data}
          onClose={() => setOpen(false)}
          onLater={() => {
            putOffUntilLater();
            setOpen(false);
          }}
        />
      )}
    </>
  );
}

function outcomeText(outcome: DockerFix["outcome"]): { text: string; ok: boolean } {
  switch (outcome) {
    case "fixed":
      return { ok: true, text: "Fixed. Docker Desktop is restarting: conversations and workflows can start in a minute or two." };
    case "declined":
      return {
        ok: false,
        text: "Windows didn't give permission, or the change didn't go through. Check that your temporary administrator access is on (it can take a minute to turn on), then click Fix it again.",
      };
    case "still-refused":
      return { ok: false, text: "That didn't fix it. Restart Windows instead: that puts the permission back too." };
    case "busy":
      return { ok: false, text: "Windows' permission box is already open: look for it (it may be behind other windows)." };
    case "not-needed":
      return { ok: true, text: "Windows isn't blocking Docker's virtual machine now, so there's nothing to fix." };
    default:
      return { ok: false, text: "This computer doesn't need this fix." };
  }
}

export function DockerFixDialog({
  status,
  onClose,
  onLater = onClose,
}: {
  status: DockerStatus;
  onClose: () => void;
  onLater?: () => void;
}) {
  const client = useQueryClient();
  const fix = useMutation({
    mutationFn: dockerApi.fix,
    onSuccess: (result) => client.setQueryData<DockerStatus>(KEY, (old) => old && { ...old, state: result.state, fixing: false }),
  });
  const recheck = useMutation({
    mutationFn: dockerApi.check,
    onSuccess: (fresh) => client.setQueryData(KEY, fresh),
  });
  const url = status.admin_access_url;
  const outcome = fix.data && outcomeText(fix.data.outcome);
  const vmStarts = recheck.data !== undefined && VM_STARTS.has(recheck.data.state);
  const fixed = fix.data?.outcome === "fixed" || fix.data?.outcome === "not-needed" || vmStarts;
  const waiting = fix.isPending || (status.fixing && !fix.data);
  return (
    <Modal
      title="Docker needs a Windows fix"
      onClose={onClose}
      actions={
        fixed ? (
          <Button variant="primary" onClick={onClose}>
            Done
          </Button>
        ) : (
          <>
            <Button variant="ghost" onClick={onLater}>
              Later
            </Button>
            <Button disabled={recheck.isPending || waiting} onClick={() => recheck.mutate()}>
              {recheck.isPending ? "Checking…" : "Check again"}
            </Button>
            <Button variant="primary" disabled={waiting} onClick={() => fix.mutate()}>
              {waiting ? "Waiting for Windows…" : "Fix it"}
            </Button>
          </>
        )
      }
    >
      <p className="text-sm">
        Windows won't let Docker's virtual machine start: a Michigan Medicine policy took away a permission it needs, and
        it can do that again from time to time. Until it's fixed, conversations and workflows can't run. Nothing you've
        done in DataLab is lost.
      </p>
      <ol className="mt-3 list-decimal space-y-2 pl-5 text-sm">
        <li>
          Turn on your temporary administrator access
          {url ? (
            <>
              {" "}
              at{" "}
              <a
                href={url}
                target="_blank"
                rel="noopener noreferrer"
                className="font-medium text-ink underline decoration-faint underline-offset-4 hover:decoration-ink"
              >
                {url.replace(/^https:\/\//, "").replace(/\/$/, "")}
              </a>
            </>
          ) : (
            " (on a Michigan Medicine computer, from your profile page; elsewhere, ask IT)"
          )}
          , and wait until it says it's on.
        </li>
        <li>
          Click <span className="font-medium">Fix it</span>. Windows asks whether to allow changes (the box may be behind
          other windows): click <span className="font-medium">Yes</span>. DataLab gives the permission back and restarts
          Docker Desktop.
        </li>
      </ol>
      <p className="mt-3 text-sm text-muted">No administrator access? Restarting Windows fixes it too, for a while.</p>
      {waiting && (
        <p role="status" className="mt-3 text-sm font-medium">
          Windows is asking for permission: look for its box (it may be behind other windows) and click Yes.
        </p>
      )}
      {outcome && (
        <p role="status" className={`mt-3 text-sm font-medium ${outcome.ok ? "text-data" : "text-attn"}`}>
          {outcome.text}
        </p>
      )}
      {!fix.data && !waiting && recheck.data && (
        <p role="status" className="mt-3 text-sm font-medium">
          {vmStarts
            ? "Docker's virtual machine can start again."
            : recheck.data.state === "vm-refused"
              ? "Still blocked. Follow the steps above, or restart Windows."
              : "DataLab couldn't tell whether Windows lets it start. Try Fix it, or restart Windows."}
        </p>
      )}
      {(fix.error || recheck.error) && (
        <p role="alert" className="mt-3 text-sm font-medium text-danger">
          DataLab couldn't ask Windows: {(fix.error ?? recheck.error)?.message}
        </p>
      )}
    </Modal>
  );
}
