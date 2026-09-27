// Signing in to GitHub (backend api/github.py): one sign-in for both lab repos,
// the knowledge base and the pipelines. Each repo's own status and Sync are in
// knowledge.ts and pipelines.ts.
import { request } from "./http";
import type { components } from "./schema";

type Schemas = components["schemas"];
export type GitHubStatus = Schemas["GitHubStatusOut"];
export type GitHubRepo = Schemas["GitHubRepoOut"];
export type SignIn = Schemas["GitHubSignInOut"];

const post: RequestInit = { method: "POST" };

export const githubApi = {
  /** Whether signing in is possible here, who is signed in, and which repos it's for. */
  status: () => request<GitHubStatus>("/api/github/status"),
  /** Where signing in is, without asking GitHub. */
  signIn: () => request<SignIn>("/api/github/sign-in"),
  /** Start GitHub's device flow: the code to enter at github.com/login/device. */
  startSignIn: () => request<SignIn>("/api/github/sign-in", post),
  /** Ask whether the code was entered; call it every `interval` seconds. */
  pollSignIn: () => request<SignIn>("/api/github/sign-in/poll", post),
  cancelSignIn: () => request<SignIn>("/api/github/sign-in/cancel", post),
  signOut: () => request<SignIn>("/api/github/sign-out", post),
};
