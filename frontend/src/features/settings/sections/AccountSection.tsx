import { useMutation, useQuery } from "@tanstack/react-query";

import { sessionApi } from "@/api/session";
import { clearAllDrafts } from "@/components/chat/plan";
import { Button } from "@/components/ui";
import { leaveApp, SESSION_ENDED_PATH } from "@/features/toolbar/SessionMenu";

/** The lab's shared DataLab: who's signed in, what's theirs, and Sign out. */
export function AccountSection() {
  const me = useQuery({ queryKey: ["session-me"], queryFn: sessionApi.me, staleTime: 5 * 60_000 });
  const out = useMutation({
    mutationFn: sessionApi.end,
    onSuccess: () => {
      clearAllDrafts(); // plan edits in this tab go with the sign-in
      leaveApp(SESSION_ENDED_PATH);
    },
  });
  return (
    <section aria-labelledby="account-title" className="flex flex-col gap-4 font-sans text-[13.5px]">
      <h2 id="account-title" className="dl-label">
        Account
      </h2>
      {me.data && (
        <p>
          Signed in with GitHub as <span className="font-medium text-ink">{me.data.name}</span>
          {me.data.login !== me.data.name && <span className="text-muted"> (@{me.data.login})</span>}
          {me.data.admin && <span className="text-muted">, one of this DataLab's administrators</span>}.
        </p>
      )}
      <p className="max-w-[40rem] text-muted">
        Your conversations, Playground, workflow runs and drafts are yours alone: nobody else signed in here sees them.
        What you save to the lab's knowledge base or pipelines goes to GitHub as you. Files you upload stay with their
        conversation; exports come to your browser as downloads.
      </p>
      {me.data && !me.data.admin && (
        <p className="max-w-[40rem] text-muted">
          The lab's connections (the database and U-M GPT), the Safety check and storage are looked after by
          DataLab's administrators.
        </p>
      )}
      <div>
        <Button onClick={() => out.mutate()} disabled={out.isPending}>
          {out.isPending ? "Signing out…" : "Sign out"}
        </Button>
        {out.error && <p className="mt-1 text-xs text-danger">{out.error.message}</p>}
      </div>
    </section>
  );
}
