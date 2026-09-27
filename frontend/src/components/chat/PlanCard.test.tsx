import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";

import { api, ApiError, type PlanSchema } from "@/api/client";

import type { Approval } from "./ApprovalCard";
import type { PlanV2 } from "./plan";
import { PlanCard } from "./PlanCard";

vi.mock("@/api/client", async (original) => ({
  ...(await original<typeof import("@/api/client")>()),
  api: { planSchema: vi.fn(), answerApproval: vi.fn() },
}));

const section = (kind: string, label: string, guidance = "") => ({ kind, label, guidance });
const schema: PlanSchema = {
  schema_version: 2,
  sections: [
    section("question_and_purpose", "Question and purpose"),
    section("data_and_scope", "Data and scope"),
    section("checks_and_limitations", "Checks and limitations"),
    section("deliverables", "Deliverables"),
    section("measures", "Measures and summaries", "Each measure, with its table and column."),
    section("comparison", "Comparison groups"),
    section("missing_data", "Missing data"),
  ],
  core: ["question_and_purpose", "data_and_scope", "checks_and_limitations", "deliverables"],
  modules: ["missing_data"],
  types: [{ id: "describe", label: "Describe or compare", summary: "", required: ["measures"], optional: ["comparison"], checks: "" }],
  limits: { section: 2000, title: 80, rationale: 300, additional: 3, plan: 12000 },
};

const proposed: PlanV2 = {
  schema_version: 2,
  analysis_type: "describe",
  analysis_type_label: "Describe or compare",
  rationale: "The question asks for a summary.",
  sections: [
    { kind: "question_and_purpose", label: "Question and purpose", content: "How long do interns sleep?" },
    { kind: "data_and_scope", label: "Data and scope", content: "IHS_2025." },
    { kind: "checks_and_limitations", label: "Checks and limitations", content: "Coverage by month." },
    { kind: "deliverables", label: "Deliverables", content: "A table." },
    { kind: "measures", label: "Measures and summaries", content: "Sleep minutes." },
    { kind: "additional", label: "Devices", content: "Fitbit only." },
  ],
};

const pending: Approval = { kind: "approval", id: "ap1", approvalKind: "analysis_plan", question: "", plan: proposed, state: "pending" };

function show(approval: Approval) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <PlanCard conversationId="c1" approval={approval} />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.mocked(api.planSchema).mockResolvedValue(schema);
  vi.mocked(api.answerApproval).mockReset().mockResolvedValue(undefined);
});

it("approves exactly the plan as the person edited it, their own sections included", async () => {
  show(pending);
  expect(screen.getByText("Describe or compare")).toBeInTheDocument();
  fireEvent.change(screen.getByDisplayValue("Fitbit only."), { target: { value: "Fitbit and Garmin." } });
  // Optional sections can be added, and removed again; required ones can't be removed.
  const add = await screen.findByRole("combobox");
  fireEvent.change(add, { target: { value: "missing_data" } });
  expect(screen.getByText("Missing data")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Remove Missing data" }));
  expect(screen.queryByRole("button", { name: "Remove Measures and summaries" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Approve plan" }));
  await waitFor(() => expect(api.answerApproval).toHaveBeenCalled());
  const sent = vi.mocked(api.answerApproval).mock.calls[0][4] as PlanV2;
  expect(sent.sections.map((s) => s.kind)).toEqual(proposed.sections.map((s) => s.kind));
  expect(sent.sections.at(-1)).toEqual({ kind: "additional", label: "Devices", content: "Fitbit and Garmin." });
});

it("can't be approved with a required section empty, and shows the server's reason for a refusal", async () => {
  vi.mocked(api.answerApproval).mockRejectedValue(new ApiError(422, "The plan's Devices says only 'N/A'."));
  show(pending);
  await screen.findByRole("combobox"); // the schema has loaded
  const measures = screen.getByDisplayValue("Sleep minutes.");
  fireEvent.change(measures, { target: { value: " " } });
  expect(screen.getByRole("button", { name: "Approve plan" })).toBeDisabled();
  expect(screen.getByText(/Still to write: Measures and summaries/)).toBeInTheDocument();
  fireEvent.change(measures, { target: { value: "Sleep minutes." } });
  fireEvent.change(screen.getByDisplayValue("Fitbit only."), { target: { value: "N/A" } });
  fireEvent.click(screen.getByRole("button", { name: "Approve plan" }));
  expect(await screen.findByText("The plan's Devices says only 'N/A'.")).toBeInTheDocument();
});

it("folds a frozen plan to its question, with every section a click away", () => {
  show({ ...pending, state: "approved", frozen: { at: "2026-09-26T12:00:00Z", sha256: "abcdef0123" } });
  expect(screen.getByText("How long do interns sleep?")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Show the frozen plan" }));
  expect(screen.getByText("Devices")).toBeInTheDocument();
  expect(screen.getByText("Fitbit only.")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Approve plan" })).toBeNull();
});

it("shows a version-1 plan with the labels it was frozen with", () => {
  show({
    ...pending,
    state: "approved",
    plan: { question: "Sleep and mood?", outcome: "PHQ-9", estimand: "" },
    frozen: { at: "2026-09-01T12:00:00Z", sha256: "0123456789" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Show the frozen plan" }));
  expect(screen.getByText("Outcome")).toBeInTheDocument();
  expect(screen.queryByText("Estimand (what exactly is estimated)")).toBeNull();
});
