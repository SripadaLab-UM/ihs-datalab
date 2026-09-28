import { type ReactNode, useState } from "react";

import { Button, Modal } from "@/components/ui";

// This computer and the local network: never opened from an agent's link.
const LOCAL_HOST =
  /^(localhost|.*\.(localhost|local|internal|home\.arpa|lan)|0\.0\.0\.0|127\.\d+\.\d+\.\d+|10\.\d+\.\d+\.\d+|192\.168\.\d+\.\d+|172\.(1[6-9]|2\d|3[01])\.\d+\.\d+|169\.254\.\d+\.\d+|100\.(6[4-9]|[7-9]\d|1[01]\d|12[0-7])\.\d+\.\d+|\[.*\])$/;

/** The parts of a link worth showing before it's opened, or null if it can't be opened. */
export function describeLink(href: string): { url: string; host: string } | null {
  let url: URL;
  try {
    url = new URL(href);
  } catch {
    return null;
  }
  if (url.protocol !== "https:" && url.protocol !== "http:") return null;
  // A trailing dot names the same host ("localhost." is this computer).
  if (LOCAL_HOST.test(url.hostname.replace(/\.+$/, "")) || url.username || url.password) return null;
  // The host as the browser will use it: look-alike letters show as xn--.
  return { url: url.href, host: url.host };
}

/**
 * A link the agent wrote (or, with `source="guide"`, one in DataLab's own
 * guide). Opening it is a request to another site that carries everything in
 * the address, so the person sees the full address, not just the link's
 * text, and confirms first.
 */
export function ExternalLink({
  href,
  children,
  source = "agent",
}: {
  href: string | undefined;
  children: ReactNode;
  source?: "agent" | "guide";
}) {
  const [asking, setAsking] = useState(false);
  const link = href ? describeLink(href) : null;
  if (!link) return <span title="This link can't be opened from DataLab.">{children}</span>;
  return (
    <>
      <button type="button" className="text-accent underline decoration-faint underline-offset-[3px] hover:decoration-ink" title={link.url} onClick={() => setAsking(true)}>
        {children}
      </button>
      {asking && (
        <Modal
          title={source === "guide" ? "Open a link from the guide?" : "Open a link the agent wrote?"}
          onClose={() => setAsking(false)}
          actions={
            <>
              <Button onClick={() => setAsking(false)}>Cancel</Button>
              <Button
                variant="primary"
                onClick={() => {
                  window.open(link.url, "_blank", "noopener,noreferrer");
                  setAsking(false);
                }}
              >
                Open {link.host}
              </Button>
            </>
          }
        >
          <p className="text-sm">
            This opens <strong>{link.host}</strong> in your browser, outside DataLab. Everything in the address below,
            including the site's name, is sent out.
          </p>
          <p className="mt-2 text-sm text-danger">
            Check that no part of it comes from your data. If you didn't expect this link, don't open it.
          </p>
          <p
            className="mt-3 max-h-40 overflow-y-auto break-all rounded-lg bg-sunken px-3 py-2 font-mono text-xs"
            data-testid="link-address"
          >
            {link.url}
          </p>
        </Modal>
      )}
    </>
  );
}
