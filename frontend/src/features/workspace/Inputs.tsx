import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api, type Conversation } from "@/api/client";
import { Button } from "@/components/ui";

/** Files and folders attached to a conversation. The agent reads them; it can't change them. */
export function Inputs({ conversation, practice }: { conversation: Conversation; practice: boolean }) {
  const queryClient = useQueryClient();
  const inputs = useQuery({ queryKey: ["inputs", conversation.id], queryFn: () => api.inputs(conversation.id) });
  const [refused, setRefused] = useState<{ path: string; reason: string }[]>([]);
  const [warnings, setWarnings] = useState<string[]>([]);
  const refresh = () => queryClient.invalidateQueries({ queryKey: ["inputs", conversation.id] });
  const attach = useMutation({
    mutationFn: async (request: { source: "files" | "folder" | "sample"; sample?: string }) => {
      if (
        conversation.kind === "research" &&
        !window.confirm(
          "This is a research session, which has the internet. Anything you attach may be sent to websites. Attach anyway?",
        )
      ) {
        return { added: [], refused: [], warnings: [] };
      }
      return api.attach(conversation.id, request.source, request.sample);
    },
    onSuccess: (result) => {
      setRefused(result.refused);
      setWarnings(result.warnings ?? []);
      refresh();
    },
  });
  const detach = useMutation({ mutationFn: (id: string) => api.detach(conversation.id, id), onSuccess: refresh });
  const blocked = conversation.busy || attach.isPending;

  return (
    <div className="flex flex-col gap-3">
      <p className="text-xs text-muted">
        The agent can read these at <code className="font-mono">/inputs</code> but can't change them. An attached
        folder is shared whole, including anything added to it later.
        {conversation.kind === "research" && (
          <span className="text-research"> This is a research session: anything attached may reach the internet.</span>
        )}
      </p>
      {practice ? (
        <Samples onAttach={(sample) => attach.mutate({ source: "sample", sample })} disabled={blocked} />
      ) : (
        <div className="flex gap-2">
          <Button onClick={() => attach.mutate({ source: "files" })} disabled={blocked}>
            Attach files…
          </Button>
          <Button onClick={() => attach.mutate({ source: "folder" })} disabled={blocked}>
            Attach folder…
          </Button>
        </div>
      )}
      {attach.isPending && !practice && <p className="text-xs text-muted">Choose in the window that opened…</p>}
      {conversation.busy && <p className="text-xs text-muted">You can attach once the agent has finished.</p>}
      {attach.error && <p className="text-xs text-danger">{attach.error.message}</p>}
      {detach.error && <p className="text-xs text-danger">{detach.error.message}</p>}
      {warnings.map((warning) => (
        <p key={warning} className="rounded-lg bg-research-soft px-2 py-1 text-xs text-research" role="alert">
          {warning}
        </p>
      ))}
      {refused.map((r) => (
        <p key={r.path} className="text-xs text-danger">
          Not attached: {r.reason}
        </p>
      ))}
      <ul className="flex flex-col gap-1">
        {inputs.data?.map((item) => (
          <li key={item.id} className="rounded-lg border border-line p-2 text-xs">
            <div className="flex items-center gap-2">
              <span aria-hidden>{item.kind === "folder" ? "📁" : "📄"}</span>
              <span className="min-w-0 flex-1 truncate font-mono" title={item.container_path}>
                {item.container_path}
              </span>
              <Button
                variant="ghost"
                className="px-2 py-0.5 text-xs"
                disabled={conversation.busy || detach.isPending}
                onClick={() => detach.mutate(item.id)}
              >
                Remove
              </Button>
            </div>
            <div className="mt-0.5 truncate text-muted" title={item.host_path}>
              {practice ? "Practice sample" : item.host_path}
            </div>
            {!item.available && <div className="mt-0.5 text-danger">Not found on this computer any more.</div>}
          </li>
        ))}
      </ul>
    </div>
  );
}

function Samples({ onAttach, disabled }: { onAttach: (sample: string) => void; disabled: boolean }) {
  const samples = useQuery({ queryKey: ["input-samples"], queryFn: api.inputSamples });
  return (
    <div className="rounded-lg bg-sunken p-2 text-xs">
      <p className="text-muted">Practice DataLab can't attach your own files. Try a synthetic sample:</p>
      <ul className="mt-1 flex flex-col">
        {samples.data?.map((name) => (
          <li key={name} className="flex items-center justify-between gap-2">
            <span className="truncate font-mono">{name}</span>
            <Button variant="ghost" className="px-2 py-0.5 text-xs" disabled={disabled} onClick={() => onAttach(name)}>
              Attach
            </Button>
          </li>
        ))}
      </ul>
    </div>
  );
}
