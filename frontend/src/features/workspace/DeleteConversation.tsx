import { useMutation, useQueryClient } from "@tanstack/react-query";

import { api, type Conversation } from "@/api/client";
import { Button, Icon, Modal } from "@/components/ui";

/**
 * Deleting a conversation is permanent, so it's always confirmed, and not
 * offered while the agent is working (deleting would stop it mid-turn).
 */
export function DeleteConversation({
  conversation,
  onClose,
  onDeleted,
}: {
  conversation: Conversation;
  onClose: () => void;
  onDeleted: () => void;
}) {
  const queryClient = useQueryClient();
  const remove = useMutation({
    mutationFn: () => api.deleteConversation(conversation.id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["conversations"] });
      onDeleted();
    },
  });
  return (
    <Modal title="Delete this conversation?" onClose={onClose}>
      <div className="flex flex-col gap-3 text-sm">
        <p className="font-serif text-[17px]">“{conversation.title}”</p>
        <p>
          Its history, the files in its workspace, and its checkpoints are removed from DataLab. This can't be
          undone.
        </p>
        <p className="text-muted">Files you exported, and files or folders you attached, aren't touched.</p>
        {conversation.busy && (
          <p className="text-attn" role="status">
            The agent is working in this conversation. Stop it first.
          </p>
        )}
        {remove.error && <p className="text-danger">{remove.error.message}</p>}
        <div className="flex justify-end gap-2">
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="danger" onClick={() => remove.mutate()} disabled={conversation.busy || remove.isPending}>
            <Icon name="trash" size={14} /> {remove.isPending ? "Deleting…" : "Delete"}
          </Button>
        </div>
      </div>
    </Modal>
  );
}
