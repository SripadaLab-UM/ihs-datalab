// The lab's server: an export comes back as a download (a .zip DataLab keeps
// for a day, for its maker only), which the browser saves.
import { at } from "@/lib/base";

/** Have the browser save what DataLab made ("/api/downloads/…"). */
export function startDownload(url: string): void {
  const link = document.createElement("a");
  link.href = at(url);
  link.download = "";
  document.body.append(link);
  link.click();
  link.remove();
}

/** Where an export went, when it was a download: the file, to save again. */
export function DownloadReady({ url, name }: { url: string; name: string }) {
  return (
    <p className="text-[12.5px] text-muted">
      Your browser is saving <span className="font-mono text-ink">{name}</span>.{" "}
      <a
        href={at(url)}
        download
        className="text-ink underline decoration-faint underline-offset-4 hover:decoration-ink"
      >
        Save it again
      </a>{" "}
      (for a day).
    </p>
  );
}
