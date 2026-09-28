import { render, screen } from "@testing-library/react";
import { expect, it } from "vitest";

import { AboutSection } from "./AboutSection";

const health = { status: "ok", version: "0.1.0", profile: "practice", database_configured: true } as const;

it("says why the catalog is empty, and what happens next", () => {
  const problem = "DataLab builds it (metadata only) the first time it connects to the database.";
  render(<AboutSection health={{ ...health, catalog_tables: 0, catalog_problem: problem }} />);
  expect(screen.getByText(problem)).toBeTruthy();
});

it("says nothing more once the catalog has tables", () => {
  render(<AboutSection health={{ ...health, catalog_tables: 12, catalog_problem: null }} />);
  expect(screen.getByText("12 tables and views")).toBeTruthy();
});
