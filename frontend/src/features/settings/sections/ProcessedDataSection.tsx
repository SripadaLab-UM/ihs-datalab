import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { type Processed, processedApi, type ProcessedSignIn } from "@/api/processed";
import { Button, Chip, Icon } from "@/components/ui";
import { ago } from "@/features/knowledge/repoState";
import { formatBytes } from "@/lib/csv";

import { PROCESSED_ANCHOR } from "../sectionIds";
import { FixedOnPractice, Part } from "./Section";

const STATUS = ["processed-status"];

/**
 * The lab's processed data: the cleaned files the lab keeps in Dropbox. A
 * conversation can use them as well as, or instead of, the database. DataLab
 * reads them through Dropbox's API with the lab's read-only app, signed in
 * as the person: Connect Dropbox opens Dropbox's page, the person clicks
 * Allow, and pastes the code Dropbox shows. Then they choose the folder.
 */
export function ProcessedDataSection() {
  const queryClient = useQueryClient();
  const status = useQuery({ queryKey: STATUS, queryFn: processedApi.status });
  const settle = (next: Processed) => queryClient.setQueryData(STATUS, next);
  const refresh = useMutation({ mutationFn: processedApi.refresh, onSuccess: settle });
  const shown = status.data;

  if (!shown) {
    return (
      <Part id={PROCESSED_ANCHOR} title="Processed data (Dropbox)">
        <p className="mt-1 text-sm text-muted">{status.error ? status.error.message : "Checking…"}</p>
      </Part>
    );
  }
  if (shown.source === "practice") {
    return (
      <Part
        id={PROCESSED_ANCHOR}
        title="Processed data (Dropbox)"
        state={<FixedOnPractice>A synthetic folder on the practice DataLab</FixedOnPractice>}
      >
        <p className="mt-1 text-sm text-muted">
          Practice DataLab never connects to Dropbox. Its conversations can use a small synthetic folder shaped like the
          lab's processed data instead.
        </p>
        <FileList processed={shown} onRefresh={() => refresh.mutate()} refreshing={refresh.isPending} />
        {refresh.error && <p className="mt-2 text-sm text-danger">{refresh.error.message}</p>}
      </Part>
    );
  }
  if (shown.source === "off") {
    return (
      <Part id={PROCESSED_ANCHOR} title="Processed data (Dropbox)" state={<Chip>Not set up</Chip>}>
        <p className="mt-1 text-sm text-muted">
          {shown.problem} Ask the DataLab maintainer to set the{" "}
          <code className="font-mono text-[12.5px]">[dropbox]</code> section of settings.toml. Until then, conversations
          use the study database only.
        </p>
      </Part>
    );
  }
  return <Dropbox processed={shown} />;
}

function Dropbox({ processed }: { processed: Processed }) {
  const queryClient = useQueryClient();
  // The sign-in as the last answer gave it, then as DataLab's status says.
  const afterSignIn = (next: ProcessedSignIn) => {
    queryClient.setQueryData<Processed>(STATUS, (old) => (old ? { ...old, sign_in: next } : old));
    queryClient.invalidateQueries({ queryKey: STATUS });
  };
  const settle = (next: Processed) => queryClient.setQueryData(STATUS, next);
  const connect = useMutation({ mutationFn: processedApi.connect, onSuccess: afterSignIn });
  const cancel = useMutation({ mutationFn: processedApi.cancel, onSuccess: afterSignIn });
  const disconnect = useMutation({ mutationFn: processedApi.disconnect, onSuccess: afterSignIn });
  const refresh = useMutation({ mutationFn: processedApi.refresh, onSuccess: settle });
  const [choosing, setChoosing] = useState(false);
  const flow = processed.sign_in;
  const signedIn = flow?.state === "signed in";
  const error = connect.error ?? cancel.error ?? disconnect.error ?? refresh.error;

  let body;
  if (flow?.state === "waiting" && flow.authorize_url) {
    body = <Waiting flow={flow} onDone={afterSignIn} onCancel={() => cancel.mutate()} />;
  } else if (!signedIn) {
    const why = flow?.message;
    body = (
      <div className="mt-3 flex flex-col gap-3">
        {why && (
          <p className="flex items-baseline gap-1.5 text-sm text-attn" role="status">
            <Icon name="alert" size={13} className="translate-y-[2px]" /> {why}
          </p>
        )}
        <p className="text-sm text-muted">
          Connect your U-M Dropbox account so conversations can use the lab's processed data. DataLab can only read files,
          never change them, and fetches only the files a conversation needs. The sign-in is kept in this computer's
          keychain.
        </p>
        <div>
          <Button variant="primary" onClick={() => connect.mutate()} disabled={connect.isPending}>
            {connect.isPending ? "Opening…" : "Connect Dropbox"}
          </Button>
        </div>
      </div>
    );
  } else if (!processed.folder || choosing) {
    body = (
      <ChooseFolder
        current={processed.folder}
        onChosen={(next) => {
          settle(next);
          setChoosing(false);
        }}
        onCancel={processed.folder ? () => setChoosing(false) : undefined}
      />
    );
  } else {
    body = (
      <div className="mt-4 flex flex-col gap-3">
        <div className="flex flex-wrap items-center gap-3">
          <span className="inline-flex h-8 w-8 items-center justify-center rounded-full border border-line text-data">
            <Icon name="check" size={15} />
          </span>
          <p className="flex-1 text-sm">
            Connected as <span className="font-medium">{processed.sign_in?.account}</span>
          </p>
          <Button onClick={() => setChoosing(true)}>Change folder…</Button>
          <Button onClick={() => disconnect.mutate()} disabled={disconnect.isPending}>
            Disconnect
          </Button>
        </div>
        {processed.sign_in?.message && <p className="text-sm text-attn">{processed.sign_in.message}</p>}
        <FileList processed={processed} onRefresh={() => refresh.mutate()} refreshing={refresh.isPending} />
        <p className="text-xs text-muted">
          Disconnect revokes DataLab's access on Dropbox, and forgets the folder, its file list and the files downloaded
          for conversations on this computer.
        </p>
      </div>
    );
  }
  const state =
    flow?.state === "waiting" ? (
      <Chip tone="you">Waiting for the code</Chip>
    ) : signedIn && processed.folder ? (
      <Chip tone="good">Connected</Chip>
    ) : signedIn ? (
      <Chip tone="attn">Choose the folder</Chip>
    ) : (
      <Chip tone="attn">Not connected</Chip>
    );
  return (
    <Part id={PROCESSED_ANCHOR} title="Processed data (Dropbox)" state={state}>
      <p className="mt-1 text-sm text-muted">
        The cleaned files the lab keeps in Dropbox, made from the study database. A conversation can use them as well as,
        or instead of, the database.
      </p>
      {body}
      {error && <p className="mt-2 text-sm text-danger">{error.message}</p>}
    </Part>
  );
}

function Waiting({
  flow,
  onDone,
  onCancel,
}: {
  flow: ProcessedSignIn;
  onDone: (next: ProcessedSignIn) => void;
  onCancel: () => void;
}) {
  const [code, setCode] = useState("");
  const finish = useMutation({ mutationFn: () => processedApi.finish(code.trim()), onSuccess: onDone });
  const field = "w-full max-w-sm rounded-xl border border-line bg-canvas px-3 py-2 font-mono text-sm outline-none focus:border-accent";
  return (
    <form
      className="mt-4 flex flex-col gap-3 border-l border-you pl-5"
      onSubmit={(e) => {
        e.preventDefault();
        if (code.trim()) finish.mutate();
      }}
    >
      <p className="font-serif text-[19px]">Allow IHS DataLab on Dropbox</p>
      <ol className="list-decimal space-y-1 pl-5 text-sm">
        <li>
          <a
            href={flow.authorize_url ?? undefined}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-1 text-ink underline decoration-faint underline-offset-4 hover:decoration-ink"
          >
            Open Dropbox <Icon name="open" size={12} />
          </a>
          , signed in with your U-M account, and click Allow.
        </li>
        <li>Dropbox shows a code. Paste it here.</li>
      </ol>
      <label className="block">
        <span className="dl-label">Code from Dropbox</span>
        <input
          value={code}
          onChange={(e) => setCode(e.target.value)}
          autoComplete="off"
          spellCheck={false}
          className={`mt-1.5 ${field}`}
        />
      </label>
      {(finish.data?.message ?? flow.message) && (
        <p className="text-sm text-attn" role="status">
          {finish.data?.message ?? flow.message}
        </p>
      )}
      {finish.error && <p className="text-sm text-danger">{finish.error.message}</p>}
      <div className="flex gap-2">
        <Button type="submit" variant="primary" disabled={!code.trim() || finish.isPending}>
          {finish.isPending ? "Connecting…" : "Connect"}
        </Button>
        <Button type="button" onClick={onCancel}>
          Cancel
        </Button>
      </div>
    </form>
  );
}

function ChooseFolder({
  current,
  onChosen,
  onCancel,
}: {
  current?: string | null;
  onChosen: (next: Processed) => void;
  onCancel?: () => void;
}) {
  const folders = useQuery({ queryKey: ["processed-folders"], queryFn: processedApi.folders });
  const [picked, setPicked] = useState("");
  const choose = useMutation({ mutationFn: () => processedApi.choose(picked), onSuccess: onChosen });
  return (
    <div className="mt-4 flex flex-col gap-3">
      <p className="text-sm">
        Choose the lab's processed-data folder{current ? <span className="text-muted"> (now {current})</span> : null}.
        DataLab lists its files, names and sizes only, without downloading any.
      </p>
      {folders.isPending && <p className="text-sm text-muted">Asking Dropbox for your folders…</p>}
      {folders.error && <p className="text-sm text-danger">{folders.error.message}</p>}
      {folders.data && (
        <select
          aria-label="Folder"
          value={picked}
          onChange={(e) => setPicked(e.target.value)}
          className="w-full max-w-md rounded-xl border border-line bg-canvas px-3 py-2 text-sm outline-none focus:border-accent"
        >
          <option value="">Choose a folder…</option>
          {folders.data.map((f) => (
            <option key={f.id} value={f.id}>
              {f.name}
            </option>
          ))}
        </select>
      )}
      {choose.error && <p className="text-sm text-danger">{choose.error.message}</p>}
      <div className="flex gap-2">
        <Button variant="primary" onClick={() => choose.mutate()} disabled={!picked || choose.isPending}>
          {choose.isPending ? "Listing its files…" : "Use this folder"}
        </Button>
        {onCancel && <Button onClick={onCancel}>Cancel</Button>}
      </div>
    </div>
  );
}

function FileList({ processed, onRefresh, refreshing }: { processed: Processed; onRefresh: () => void; refreshing: boolean }) {
  return (
    <div className="mt-3 flex flex-wrap items-center gap-x-3 gap-y-1 border-t border-line pt-3 text-sm">
      <span className="font-mono text-[12.5px]">{processed.folder}</span>
      <span className="text-muted">
        {processed.indexed_at
          ? `${processed.files.toLocaleString()} files, ${formatBytes(processed.bytes)}, listed ${ago(processed.indexed_at)}`
          : "Not listed yet"}
      </span>
      <Button variant="ghost" className="ml-auto px-2 text-[12.5px]" onClick={onRefresh} disabled={refreshing}>
        {refreshing ? "Refreshing…" : "Refresh list"}
      </Button>
    </div>
  );
}
