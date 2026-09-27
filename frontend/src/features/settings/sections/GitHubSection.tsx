import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef } from "react";

import { api } from "@/api/client";
import { type GitHubStatus, githubApi, type SignIn } from "@/api/github";
import { knowledgeApi } from "@/api/knowledge";
import { pipelinesApi } from "@/api/pipelines";
import { Button, Chip, Icon } from "@/components/ui";
import { ago, devicePage, repoState, type RepoStatusLike } from "@/features/knowledge/repoState";

import { Section } from "./Section";

const SIGN_IN = ["github-sign-in"];
const GITHUB = ["github-status"];
const KNOWLEDGE = ["knowledge-status"];
const PIPELINES = ["pipelines-status"];
const ASK_TO_SIGN_IN = "Sign in to GitHub to use it.";

/**
 * GitHub App sign-in, for syncing the lab knowledge and pipelines repos (milestones 5 and 6):
 * one sign-in for whichever of them this DataLab has set up. GitHub's device flow:
 * DataLab shows a code, the person enters it on github.com, and DataLab asks GitHub
 * every few seconds until it's done.
 */
export function GitHubSection() {
  const health = useQuery({ queryKey: ["health"], queryFn: api.health });
  const practice = health.data?.profile === "practice";
  const status = useQuery({ queryKey: GITHUB, queryFn: githubApi.status, enabled: health.isSuccess && !practice });
  let body;
  if (practice) {
    body = (
      <p className="mt-1 text-sm text-muted">
        Practice DataLab never signs in to GitHub, so nothing from practice can reach the lab's knowledge base or
        pipelines. Practice conversations don't get a copy of the knowledge base either.
      </p>
    );
  } else if (!status.data) {
    body = <p className="mt-1 text-sm text-muted">{status.error ? status.error.message : "Checking…"}</p>;
  } else if (!status.data.available) {
    body = (
      <p className="mt-1 text-sm text-muted">
        {status.data.message ?? "The lab's repositories aren't set up on this DataLab."} Ask the DataLab maintainer to set
        the <code className="font-mono text-[12.5px]">[repos]</code> section of settings.toml.
      </p>
    );
  } else {
    return <SignInOrOut github={status.data} />;
  }
  return <Section title="GitHub">{body}</Section>;
}

function SignInOrOut({ github }: { github: GitHubStatus }) {
  const queryClient = useQueryClient();
  const areas = new Set(github.repos.map((r) => r.area));
  const signIn = useQuery({ queryKey: SIGN_IN, queryFn: githubApi.signIn });
  // Each repo's own state, and its Sync.
  const knowledge = useQuery({ queryKey: KNOWLEDGE, queryFn: knowledgeApi.status, enabled: areas.has("knowledge") });
  const pipelines = useQuery({ queryKey: PIPELINES, queryFn: pipelinesApi.status, enabled: areas.has("pipelines") });
  const syncKnowledge = useMutation({
    mutationFn: knowledgeApi.sync,
    onSuccess: (next) => queryClient.setQueryData(KNOWLEDGE, next),
  });
  const syncPipelines = useMutation({
    mutationFn: pipelinesApi.sync,
    onSuccess: (next) => queryClient.setQueryData(PIPELINES, next),
  });
  const settle = (next: SignIn) => {
    queryClient.setQueryData(SIGN_IN, next);
    for (const key of [GITHUB, KNOWLEDGE, PIPELINES]) queryClient.invalidateQueries({ queryKey: key });
    // Just signed in: download the repos straight away.
    if (next.state === "signed in") {
      if (areas.has("knowledge")) syncKnowledge.mutate();
      if (areas.has("pipelines")) syncPipelines.mutate();
    }
  };
  const start = useMutation({ mutationFn: githubApi.startSignIn, onSuccess: settle });
  const cancel = useMutation({ mutationFn: githubApi.cancelSignIn, onSuccess: settle });
  const signOut = useMutation({ mutationFn: githubApi.signOut, onSuccess: settle });
  const flow = signIn.data;
  usePolling(flow, settle);
  const error = start.error ?? cancel.error ?? signOut.error ?? syncKnowledge.error ?? syncPipelines.error;
  const rows: RepoRowProps[] = [];
  if (areas.has("knowledge"))
    rows.push({ what: "the knowledge base", status: knowledge.data, onSync: () => syncKnowledge.mutate(), syncing: syncKnowledge.isPending });
  if (areas.has("pipelines"))
    rows.push({ what: "the pipelines repo", status: pipelines.data, onSync: () => syncPipelines.mutate(), syncing: syncPipelines.isPending });
  const names = github.repos.map((r) => r.name);
  const what = [areas.has("knowledge") && "knowledge base", areas.has("pipelines") && "pipelines repo"]
    .filter(Boolean)
    .join(" and ");

  let body;
  if (flow?.state === "waiting") {
    body = <Waiting flow={flow} onCancel={() => cancel.mutate()} cancelling={cancel.isPending} />;
  } else if (github.signed_in) {
    body = (
      <SignedIn github={github} rows={rows} onSignOut={() => signOut.mutate()} signingOut={signOut.isPending} note={flow?.message} />
    );
  } else {
    // Why not, if there's a reason: the code expired, GitHub said no, or the sign-in ran out.
    const why = (flow?.message !== ASK_TO_SIGN_IN && flow?.message) || null;
    const again = Boolean(why);
    body = (
      <div className="mt-3 flex flex-col gap-3">
        {why && (
          <p className="flex items-baseline gap-1.5 text-sm text-attn" role="status">
            <Icon name="alert" size={13} className="translate-y-[2px]" /> {why}
          </p>
        )}
        <p className="text-sm text-muted">
          Sign in with your GitHub account to read the lab's {what} (
          <span className="font-mono text-[12.5px]">{names.join(", ")}</span>) and share the changes you save. The
          sign-in is kept in this computer's keychain, and commits are made as you.
        </p>
        <div>
          <Button variant="primary" onClick={() => start.mutate()} disabled={start.isPending}>
            {start.isPending ? "Asking GitHub for a code…" : again ? "Sign in again" : "Sign in with GitHub"}
          </Button>
        </div>
      </div>
    );
  }

  return (
    <Section title="GitHub">
      <p className="mt-1 text-sm text-muted">For the lab's {what}: reading it, and sharing the changes you save.</p>
      {body}
      {error && <p className="mt-2 text-sm text-danger">{error.message}</p>}
    </Section>
  );
}

/** While a code is waiting to be entered, ask GitHub every `interval` seconds. */
function usePolling(flow: SignIn | undefined, settle: (next: SignIn) => void) {
  const waiting = flow?.state === "waiting";
  const seconds = Math.max(1, flow?.interval ?? 5);
  const code = flow?.user_code;
  // The latest settle, so a new render doesn't restart the timer.
  const onDone = useRef(settle);
  onDone.current = settle;
  useEffect(() => {
    if (!waiting) return;
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;
    const tick = () => {
      timer = setTimeout(async () => {
        try {
          const next = await githubApi.pollSignIn();
          if (stopped) return;
          if (next.state === "waiting") tick();
          else onDone.current(next);
        } catch {
          if (!stopped) tick(); // offline for a moment: keep asking until the code expires
        }
      }, seconds * 1000);
    };
    tick();
    return () => {
      stopped = true;
      clearTimeout(timer);
    };
  }, [waiting, seconds, code]);
}

function Waiting({ flow, onCancel, cancelling }: { flow: SignIn; onCancel: () => void; cancelling: boolean }) {
  const page = devicePage(flow.verification_uri);
  return (
    <div className="mt-4 flex flex-col gap-3 border-l border-you pl-5">
      <p className="font-serif text-[19px]">Enter this code on GitHub</p>
      <p className="font-mono text-[30px] tracking-[0.12em] text-ink" aria-label="Your code">
        {flow.user_code}
      </p>
      <p className="text-sm">
        <a
          href={page}
          target="_blank"
          rel="noopener noreferrer"
          className="inline-flex items-center gap-1 text-ink underline decoration-faint underline-offset-4 hover:decoration-ink"
        >
          Open {page.replace(/^https:\/\//, "")} <Icon name="open" size={12} />
        </a>
        <span className="text-muted">, enter the code, and approve IHS DataLab.</span>
      </p>
      <p className="flex flex-wrap items-center gap-2 text-sm text-muted" role="status">
        <span className="dl-breathe size-[7px] rounded-full bg-ink" aria-hidden /> Waiting for GitHub…
        {flow.expires_at && (
          <span>
            The code works until {new Date(flow.expires_at).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}.
          </span>
        )}
      </p>
      <div>
        <Button onClick={onCancel} disabled={cancelling}>
          Cancel
        </Button>
      </div>
    </div>
  );
}

interface RepoRowProps {
  what: string;
  status: (RepoStatusLike & { name?: string | null; last_sync?: string | null }) | undefined;
  onSync: () => void;
  syncing: boolean;
}

function SignedIn({
  github,
  rows,
  onSignOut,
  signingOut,
  note,
}: {
  github: GitHubStatus;
  rows: RepoRowProps[];
  onSignOut: () => void;
  signingOut: boolean;
  note?: string | null;
}) {
  return (
    <div className="mt-4 flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-3">
        <span className="inline-flex h-8 w-8 items-center justify-center rounded-full border border-line text-data">
          <Icon name="check" size={15} />
        </span>
        <p className="flex-1 text-sm">
          Signed in as <span className="font-medium">{github.account?.name || github.account?.login}</span>
          {github.account?.name && <span className="font-mono text-[12.5px] text-muted"> @{github.account.login}</span>}
        </p>
        <Button onClick={onSignOut} disabled={signingOut}>
          Sign out
        </Button>
      </div>
      {note && <p className="text-sm text-attn">{note}</p>}
      {rows.map((row) => (
        <RepoRow key={row.what} {...row} />
      ))}
      <p className="text-xs text-muted">
        Signing out forgets the sign-in on this computer. To revoke DataLab's access altogether, use GitHub's Settings →
        Applications.
      </p>
    </div>
  );
}

/** One repo's copy on this computer: its state, when it was synced, and Sync. */
function RepoRow({ what, status, onSync, syncing }: RepoRowProps) {
  if (!status) return <p className="border-t border-line pt-3 text-sm text-muted">Checking {what}…</p>;
  const repo = repoState(status);
  return (
    <div className="flex flex-col gap-1 border-t border-line pt-3">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm">
        <span className="font-mono text-[12.5px]">{status.name}</span>
        <Chip tone={repo.tone}>{repo.label}</Chip>
        {status.last_sync && (
          <span className="text-muted" title={new Date(status.last_sync).toLocaleString()}>
            synced {ago(status.last_sync)}
          </span>
        )}
        {repo.canSync && (
          <Button
            variant="ghost"
            className="ml-auto px-2 text-[12.5px]"
            onClick={onSync}
            disabled={syncing}
            aria-label={`Sync ${what}`}
          >
            {syncing ? "Syncing…" : "Sync"}
          </Button>
        )}
      </div>
      {status.repo !== "in sync" && <p className={repo.tone === "bad" ? "text-sm text-danger" : "text-sm text-muted"}>{repo.text}</p>}
    </div>
  );
}
