import { render, screen } from "@testing-library/react";
import { expect, it } from "vitest";

import { GitHubSignIn, signInReason } from "./SignIn";

it("sends the browser to the server's GitHub sign-in", () => {
  render(<GitHubSignIn />);
  expect(screen.getByRole("link", { name: "Sign in with GitHub" })).toHaveAttribute("href", "/auth/github/login");
  expect(screen.queryByRole("alert")).toBeNull();
});

it("says why a sign-in didn't work, in words", () => {
  render(<GitHubSignIn reason="not-in-lab" />);
  expect(screen.getByRole("alert")).toHaveTextContent("datalab-users team");
  expect(signInReason("admins-only")).toMatch(/administrators/);
  // An unknown code still gets a sentence, never the code itself.
  expect(signInReason("something-new")).toBe(signInReason("failed"));
  expect(signInReason(null)).toBeNull();
});
