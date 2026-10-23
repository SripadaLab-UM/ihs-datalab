// The lab's shared DataLab (a server): signing in with GitHub. The server
// sends the browser to GitHub and back (backend auth/routes.py); a sign-in
// that doesn't work comes back to /signed-out?reason=<code>.
import { IhsMark } from "@/app/brand";
import { at } from "@/lib/base";

const REASONS: Record<string, string> = {
  "signed-out": "You've signed out.",
  "not-in-lab":
    "Your GitHub account can't open the lab's repository. Ask the lab's DataLab maintainer to add you to the datalab-users team, then sign in again.",
  "admins-only": "The lab's DataLab is open to its administrators while it's being set up. You'll hear when it's ready for you.",
  expired: "That sign-in took too long, or was started in another window. Sign in again.",
  denied: "The GitHub sign-in was cancelled.",
  "not-set-up": "GitHub sign-in isn't set up on this server yet.",
  unavailable: "GitHub couldn't be reached. Try again in a minute.",
  failed: "GitHub didn't say who you are. Try again in a minute.",
};

export function signInReason(code: string | null): string | null {
  if (!code) return null;
  return REASONS[code] ?? REASONS.failed;
}

export function GitHubSignIn({ reason }: { reason?: string | null }) {
  const message = signInReason(reason ?? null);
  return (
    <main className="flex h-full items-center justify-center px-6">
      <div className="flex max-w-md flex-col items-center gap-5 text-center">
        <span className="text-ink">
          <IhsMark size={28} />
        </span>
        <h1 className="font-serif text-[30px] leading-tight">DataLab</h1>
        <p className="text-muted">The lab's DataLab. Sign in with the GitHub account you use for the lab.</p>
        {message && (
          <p role="alert" className="rounded-xl border border-line bg-surface px-4 py-3 text-sm text-ink">
            {message}
          </p>
        )}
        <a
          href={at("/auth/github/login")}
          className="rounded-lg bg-accent px-4 py-2 font-sans text-sm font-medium text-accent-ink hover:opacity-85"
        >
          Sign in with GitHub
        </a>
      </div>
    </main>
  );
}
