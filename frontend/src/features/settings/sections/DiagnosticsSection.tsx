import { useEffect, useRef, useState } from "react";

import { settingsApi } from "@/api/settings";
import { Button, Icon } from "@/components/ui";

import { Section } from "./Section";

type State =
  | { step: "idle" }
  | { step: "gathering" }
  | { step: "copied" | "select"; text: string }
  | { step: "failed"; message: string };

/**
 * Copy diagnostics: a metadata-only report for the DataLab maintainer, to
 * paste into an email or a GitHub issue. It never holds keys, SQL, query
 * results or conversation content (the backend builds it; tests check it).
 * If the clipboard can't be written, the text is shown selected instead.
 */
export function DiagnosticsSection() {
  const [state, setState] = useState<State>({ step: "idle" });
  const box = useRef<HTMLTextAreaElement>(null);
  useEffect(() => {
    if (state.step === "select") {
      box.current?.focus();
      box.current?.select();
    }
  }, [state]);

  const copy = async () => {
    setState({ step: "gathering" });
    const text = settingsApi.diagnostics().then((d) => d.text);
    // Started while the click still counts as the person's own action: some
    // browsers only allow writing the clipboard then, and the report takes a
    // moment to gather.
    const written = writeClipboard(text);
    let report: string;
    try {
      report = await text;
    } catch (error) {
      setState({ step: "failed", message: error instanceof Error ? error.message : String(error) });
      return;
    }
    setState({ step: (await written) ? "copied" : "select", text: report });
  };

  const shown = state.step === "copied" || state.step === "select" ? state.text : null;
  return (
    <Section
      id="diagnostics"
      title="Diagnostics"
      actions={
        <Button onClick={copy} disabled={state.step === "gathering"}>
          {state.step === "gathering" ? "Gathering…" : "Copy diagnostics"}
        </Button>
      }
    >
      <p className="mt-1 text-sm text-muted">
        When something goes wrong, copy this report and paste it into an email or a GitHub issue for the DataLab
        maintainer. It holds versions, Safety check results and recent errors, and never your keys, SQL, results or
        conversations.
      </p>
      {state.step === "failed" && <p className="mt-3 text-sm text-danger">{state.message}</p>}
      {state.step === "copied" && (
        <p className="mt-3 flex items-center gap-1.5 text-sm text-data" role="status">
          <Icon name="check" size={14} /> Copied. This is what was copied:
        </p>
      )}
      {state.step === "select" && (
        <p className="mt-3 text-sm text-attn" role="status">
          The browser didn't let DataLab copy it. The text is selected: press ⌘C (Mac) or Ctrl+C (Windows).
        </p>
      )}
      {shown !== null && (
        <textarea
          ref={box}
          readOnly
          aria-label="Diagnostics"
          value={shown}
          rows={14}
          onFocus={(e) => e.currentTarget.select()}
          className="mt-2 w-full resize-y rounded-[3px] border border-line bg-sunken p-3 font-mono text-[11.5px] leading-snug outline-none focus:border-ink"
        />
      )}
    </Section>
  );
}

/** Write text that's still on its way to the clipboard. Whether it worked. */
export async function writeClipboard(text: Promise<string>): Promise<boolean> {
  const clipboard = navigator.clipboard;
  if (!clipboard) return false;
  if (typeof ClipboardItem !== "undefined" && clipboard.write) {
    try {
      const blob = text.then((t) => new Blob([t], { type: "text/plain" }));
      await clipboard.write([new ClipboardItem({ "text/plain": blob })]);
      return true;
    } catch {
      // Fall back to writing the text once it's here.
    }
  }
  try {
    await clipboard.writeText(await text);
    return true;
  } catch {
    return false;
  }
}
