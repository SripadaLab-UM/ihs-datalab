// Feedback: report a bug or suggest an improvement, to the DataLab
// maintainer. It goes by Copy to clipboard or an email the person sends
// themselves, never to GitHub: the app's repo is public, and feedback can
// mention study details. Diagnostics are opt-in, and are the same report as
// Settings → About → Copy diagnostics (never keys, SQL, results or conversations).
import { useQuery } from "@tanstack/react-query";
import clsx from "clsx";
import { useState } from "react";

import { api } from "@/api/client";
import { type FeedbackContact, settingsApi } from "@/api/settings";
import { Button, Icon, Modal } from "@/components/ui";
import { writeClipboard } from "@/features/settings/sections/DiagnosticsSection";

export type FeedbackKind = "bug" | "suggestion";

const KINDS: { value: FeedbackKind; label: string }[] = [
  { value: "bug", label: "Bug" },
  { value: "suggestion", label: "Suggestion" },
];

/** The text that's copied or emailed. */
export function feedbackText({
  kind,
  description,
  version,
  profile,
  diagnostics,
}: {
  kind: FeedbackKind;
  description: string;
  version?: string;
  profile?: string;
  diagnostics?: string | null;
}): string {
  const lines = [
    `DataLab feedback: ${kind === "bug" ? "bug report" : "suggestion"}`,
    ...(version ? [`DataLab ${version}${profile ? ` (${profile})` : ""}`] : []),
    "",
    description.trim() || "(no description)",
  ];
  if (diagnostics) lines.push("", "--- Diagnostics ---", diagnostics.trim());
  return lines.join("\n");
}

export function feedbackSubject(kind: FeedbackKind): string {
  return kind === "bug" ? "DataLab: bug report" : "DataLab: suggestion";
}

/** The mailto: link, with the text in the body; null without an address to send to. */
export function feedbackMailto(contact: FeedbackContact | undefined, kind: FeedbackKind, text: string): string | null {
  if (!contact?.email) return null;
  return `mailto:${encodeURIComponent(contact.email)}?subject=${encodeURIComponent(feedbackSubject(kind))}&body=${encodeURIComponent(text)}`;
}

export function FeedbackDialog({ onClose }: { onClose: () => void }) {
  const [kind, setKind] = useState<FeedbackKind>("bug");
  const [description, setDescription] = useState("");
  const [withDiagnostics, setWithDiagnostics] = useState(false);
  const [copied, setCopied] = useState<"copied" | "refused" | null>(null);
  const health = useQuery({ queryKey: ["health"], queryFn: api.health });
  const contact = useQuery({ queryKey: ["feedback-contact"], queryFn: settingsApi.feedbackContact });
  const diagnostics = useQuery({
    queryKey: ["diagnostics"],
    queryFn: settingsApi.diagnostics,
    enabled: withDiagnostics,
    staleTime: 60_000,
  });
  const report = withDiagnostics ? (diagnostics.data?.text ?? null) : null;
  const waiting = withDiagnostics && !diagnostics.data && !diagnostics.isError;
  const text = feedbackText({
    kind,
    description,
    version: health.data?.version,
    profile: health.data?.profile,
    diagnostics: report,
  });
  const mailto = feedbackMailto(contact.data, kind, text);
  const who = contact.data?.contact;

  const copy = async () => {
    setCopied((await writeClipboard(Promise.resolve(text))) ? "copied" : "refused");
  };

  return (
    <Modal title="Send feedback" onClose={onClose}>
      <p className="flex items-start gap-2 border-l-2 border-attn/60 pl-3 text-sm font-medium text-ink" role="note">
        <Icon name="alert" size={14} className="mt-0.5 shrink-0 text-attn" />
        Don't include participant data: no names, IDs, dates or values from the study database.
      </p>
      <fieldset className="mt-4">
        <legend className="dl-label">Type</legend>
        <div className="mt-1.5 flex gap-2">
          {KINDS.map((choice) => (
            <label
              key={choice.value}
              className={clsx(
                "flex cursor-pointer items-center gap-2 rounded-[3px] border px-3 py-1.5 text-sm transition-colors focus-within:outline focus-within:outline-[1.5px] focus-within:outline-offset-2 focus-within:outline-ink",
                kind === choice.value ? "border-ink" : "border-line hover:border-muted",
              )}
            >
              <input
                type="radio"
                name="feedback-kind"
                value={choice.value}
                checked={kind === choice.value}
                onChange={() => setKind(choice.value)}
                className="accent-[var(--color-ink)] focus-visible:outline-none"
              />
              {choice.label}
            </label>
          ))}
        </div>
      </fieldset>
      <label className="mt-4 block">
        <span className="dl-label">{kind === "bug" ? "What happened, and what you expected" : "What would help"}</span>
        <textarea
          value={description}
          onChange={(e) => {
            setDescription(e.target.value);
            setCopied(null);
          }}
          rows={6}
          className="mt-1.5 w-full resize-y rounded-[3px] border border-line bg-field p-2 font-sans text-sm outline-none focus:border-ink"
        />
      </label>
      <label className="mt-3 flex cursor-pointer items-start gap-2 text-sm">
        <input
          type="checkbox"
          checked={withDiagnostics}
          onChange={(e) => {
            setWithDiagnostics(e.target.checked);
            setCopied(null);
          }}
          className="mt-[3px] accent-[var(--color-ink)]"
        />
        <span>
          Include diagnostics
          <span className="block text-xs text-muted">
            Versions, Safety check results and recent errors: never your keys, SQL, results or conversations.
          </span>
        </span>
      </label>
      {withDiagnostics && diagnostics.isError && (
        <p className="mt-2 text-xs text-danger">Couldn't gather the diagnostics: {diagnostics.error.message}</p>
      )}
      {report && (
        <textarea
          readOnly
          aria-label="Diagnostics to include"
          value={report}
          rows={5}
          className="mt-2 w-full resize-y rounded-[3px] border border-line bg-sunken p-2 font-mono text-[11px] leading-snug outline-none focus:border-ink"
        />
      )}
      <div className="mt-5 flex flex-wrap items-center gap-2">
        <Button variant="primary" onClick={copy} disabled={waiting}>
          Copy to clipboard
        </Button>
        {mailto && (
          <a
            href={waiting ? undefined : mailto}
            aria-disabled={waiting || undefined}
            className="inline-flex cursor-pointer items-center justify-center gap-1.5 rounded-[3px] border border-line px-3 py-1.5 font-sans text-[13.5px] font-medium text-ink transition-colors hover:border-ink"
          >
            Email the maintainer
          </a>
        )}
        {copied === "copied" && (
          <span role="status" className="flex items-center gap-1 text-sm text-data">
            <Icon name="check" size={13} /> Copied
          </span>
        )}
      </div>
      {copied === "refused" && (
        <>
          <p role="status" className="mt-2 text-sm text-attn">
            The browser didn't let DataLab copy it. The text is below, selected: press ⌘C (Mac) or Ctrl+C (Windows).
          </p>
          <textarea
            readOnly
            autoFocus
            aria-label="Feedback to copy"
            value={text}
            rows={6}
            onFocus={(e) => e.currentTarget.select()}
            className="mt-2 w-full resize-y rounded-[3px] border border-line bg-sunken p-2 font-mono text-[11px] leading-snug outline-none focus:border-ink"
          />
        </>
      )}
      <p className="mt-3 text-xs text-muted">
        {mailto
          ? `The email opens in your mail app, to ${who}, for you to read and send. If it's cut short, copy the text instead and paste it into the email.`
          : who
            ? `Copy it and send it to ${who}.`
            : "Copy it and send it to the DataLab maintainer: whoever gave you the lab's settings file."}
      </p>
    </Modal>
  );
}
