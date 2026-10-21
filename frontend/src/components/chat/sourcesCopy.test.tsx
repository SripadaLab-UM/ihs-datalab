import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { expect, it } from "vitest";

import { SessionBadge } from "@/components/ui";

import { safetyLines } from "./Intro";

it("says what a data conversation reads, by its sources", () => {
  const badge = (sources?: ("database" | "dropbox")[]) =>
    render(
      <MemoryRouter>
        <SessionBadge kind="data" sources={sources} />
      </MemoryRouter>,
    );
  badge();
  expect(screen.getByText("Data session · database access, web blocked")).toBeInTheDocument();
  badge(["dropbox"]);
  expect(screen.getByText("Data session · processed data, web blocked")).toBeInTheDocument();
  badge(["database", "dropbox"]);
  expect(screen.getByText("Data session · database and processed data, web blocked")).toBeInTheDocument();
});

it("never says a Dropbox-only conversation queries the database", () => {
  const first = (sources?: ("database" | "dropbox")[]) => safetyLines("data", true, sources)[0]![1];
  expect(first()).toBe("Queries the IHS database, read-only");
  expect(first(["database"])).toBe("Queries the IHS database, read-only");
  expect(first(["dropbox"])).not.toMatch(/database/i);
  expect(first(["database", "dropbox"])).toMatch(/database.*processed data/);
});
