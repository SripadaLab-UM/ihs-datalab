/**
 * What the U-M GPT key is for, and what works without it. Everything that
 * talks with the agent goes through DataLab's model relay, which refuses
 * without a key; the Playground, workflows and exports never ask a model.
 * The same words as `datalab setup` (setup.KEY_UNLOCKS).
 */
export function KeyFeatures({ practice }: { practice: boolean }) {
  return (
    <div className="mt-3 grid gap-3 text-sm sm:grid-cols-2">
      <div>
        <p className="font-medium">Needs the key</p>
        <ul className="mt-1 list-disc pl-5 text-muted">
          <li>Conversations with the agent, in the Workspace, and their automatic titles</li>
          <li>Drafting a workflow with the agent</li>
          <li>The Safety check's model checks</li>
          <li>
            Knowledge's Edit with agent, and the agent's suggested updates
            {practice && " (in the real DataLab)"}
          </li>
        </ul>
      </div>
      <div>
        <p className="font-medium">Works without it</p>
        <ul className="mt-1 list-disc pl-5 text-muted">
          <li>The SQL Playground</li>
          <li>Save as workflow, and running workflows</li>
          <li>Exports and Settings</li>
        </ul>
      </div>
    </div>
  );
}
