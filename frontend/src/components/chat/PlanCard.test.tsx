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
  limits: { section: 2000, title: 80, rationale: 300, reason: 500, additional: 3, plan: 12000 },
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
  fireEvent.change(await screen.findByDisplayValue("Fitbit only."), { target: { value: "Fitbit and Garmin." } });
  expect(screen.getByText("Describe or compare")).toBeInTheDocument();
  // Optional sections can be added, and removed again; required ones can't be removed.
  fireEvent.change(screen.getByLabelText("Add a section"), { target: { value: "missing_data" } });
  expect(screen.queryByLabelText("Missing data")).toBeNull(); // choosing isn't adding
  fireEvent.click(screen.getByRole("button", { name: "Add" }));
  expect(screen.getByLabelText("Missing data")).toHaveFocus();
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
  const measures = await screen.findByDisplayValue("Sleep minutes."); // the schema has loaded
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

const revision: PlanV2 = {
  ...proposed,
  revises: { plan_id: "pl_000000000001", sha256: "a".repeat(64) },
  revision_reason: "Garmin data became available.",
  sections: proposed.sections.map((s) => (s.kind === "additional" ? { ...s, content: "Fitbit and Garmin." } : s)),
};
const comparedWithApproved = { label: "The approved plan it revises", plan: proposed, approved_at: "2026-09-26T12:00:00Z" };

it("shows a revision against the plan it revises, and needs its reason", async () => {
  show({ ...pending, plan: revision, compareTo: comparedWithApproved });
  expect(screen.getByText("Revised analysis plan")).toBeInTheDocument();
  await screen.findByLabelText("Add a section"); // the schema has loaded
  expect(screen.getByText("Changes from the approved plan it revises")).toBeInTheDocument();
  expect(screen.getByText("Fitbit only.")).toBeInTheDocument(); // what it was
  expect(screen.getByText("5 other sections unchanged.")).toBeInTheDocument();
  const reason = screen.getByDisplayValue("Garmin data became available.");
  fireEvent.change(reason, { target: { value: "" } });
  expect(screen.getByRole("button", { name: "Approve revision" })).toBeDisabled();
  fireEvent.change(reason, { target: { value: "Garmin data became available in May." } });
  fireEvent.click(screen.getByRole("button", { name: "Approve revision" }));
  await waitFor(() => expect(api.answerApproval).toHaveBeenCalled());
  const sent = vi.mocked(api.answerApproval).mock.calls[0][4] as PlanV2;
  expect(sent.revises).toEqual(revision.revises);
  expect(sent.revision_reason).toBe("Garmin data became available in May.");
});

it("sends a plan back for another type of analysis, with the person's edits", async () => {
  vi.mocked(api.planSchema).mockResolvedValue({
    ...schema,
    types: [...schema.types, { id: "prediction", label: "Prediction", summary: "Predict an outcome.", required: [], optional: [], checks: "" }],
  });
  show(pending);
  fireEvent.change(await screen.findByDisplayValue("Sleep minutes."), { target: { value: "Sleep minutes, weeks 1 to 4." } });
  fireEvent.click(await screen.findByRole("button", { name: "A different kind of analysis?" }));
  fireEvent.change(screen.getByDisplayValue("Choose a type…"), { target: { value: "prediction" } });
  expect(screen.getByText("Predict an outcome.")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Send back" }));
  await waitFor(() => expect(api.answerApproval).toHaveBeenCalled());
  const [, , approve, , sent, changeType] = vi.mocked(api.answerApproval).mock.calls[0];
  expect([approve, changeType]).toEqual([false, "prediction"]);
  expect((sent as PlanV2).sections.find((s) => s.kind === "measures")?.content).toBe("Sleep minutes, weeks 1 to 4.");
});

it("says when a frozen plan was replaced by a revision, and what a revision changed", () => {
  show({
    ...pending,
    state: "approved",
    frozen: { at: "2026-09-26T12:00:00Z", sha256: "abcdef0123" },
    supersededBy: { planId: "pl_2", at: "2026-09-26T13:00:00Z" },
  });
  expect(screen.getByText("revised later")).toBeInTheDocument();
  expect(screen.getByText(/A revision replaced it/)).toBeInTheDocument();
});

it("lets a frozen revision show what changed", () => {
  show({ ...pending, plan: revision, compareTo: comparedWithApproved, state: "approved", frozen: { at: "2026-09-26T13:00:00Z", sha256: "0123456789" } });
  expect(screen.queryByText("Changes from the approved plan it revises")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Show what changed" }));
  expect(screen.getByText("Changes from the approved plan it revises")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Show the frozen plan" }));
  expect(screen.getByText("Why it changed")).toBeInTheDocument();
});

it("sends the person's edits back with Not yet, without approving", async () => {
  show(pending);
  fireEvent.change(await screen.findByDisplayValue("A table."), { target: { value: "A table and a figure." } });
  fireEvent.click(screen.getByRole("button", { name: "Not yet" }));
  await waitFor(() => expect(api.answerApproval).toHaveBeenCalled());
  const [, , approve, , sent] = vi.mocked(api.answerApproval).mock.calls[0];
  expect(approve).toBe(false);
  expect((sent as PlanV2).sections.find((s) => s.kind === "deliverables")?.content).toBe("A table and a figure.");
});

it("isn't editable or approvable until it knows which sections are required", async () => {
  let arrive: (value: PlanSchema) => void = () => {};
  vi.mocked(api.planSchema).mockReturnValue(new Promise((resolve) => (arrive = resolve)));
  show(pending);
  expect(screen.getByText("Loading the plan's sections…")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /^Remove/ })).toBeNull();
  expect(screen.queryByRole("button", { name: "Approve plan" })).toBeNull();
  expect(screen.getByRole("button", { name: "Not yet" })).toBeEnabled();
  arrive(schema);
  expect(await screen.findByRole("button", { name: "Approve plan" })).toBeEnabled();
  expect(screen.queryByRole("button", { name: "Remove Question and purpose" })).toBeNull();
});

it("says so when the sections can't be loaded, and can still be declined", async () => {
  vi.mocked(api.planSchema).mockRejectedValue(new Error("offline"));
  show(pending);
  expect(await screen.findByText(/couldn't be loaded, so it can't be edited or approved here/)).toBeInTheDocument();
  expect(screen.getByText("Fitbit only.")).toBeInTheDocument(); // shown, not editable
  expect(screen.queryByRole("button", { name: "Approve plan" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Not yet" }));
  await waitFor(() => expect(vi.mocked(api.answerApproval).mock.calls[0][2]).toBe(false));
});

it("shows each section's guidance and how much of its limit it uses", async () => {
  show(pending);
  const measures = await screen.findByLabelText("Measures and summaries");
  expect(measures).toHaveAccessibleDescription("Each measure, with its table and column. 14 / 2000 characters");
  // Counts describe their field; they aren't part of its name.
  expect(screen.getByRole("textbox", { name: "Why this kind of analysis" })).toHaveAccessibleDescription(
    /why this type of analysis fits the question\. 32 \/ 300 characters/,
  );
  expect(screen.getByText(/The whole plan: .* of 12,000 characters/)).toBeInTheDocument();
});

it("locks what was sent, then says it's being frozen rather than showing the unedited plan", async () => {
  let finish: () => void = () => {};
  vi.mocked(api.answerApproval).mockReturnValue(new Promise((resolve) => (finish = () => resolve(undefined))));
  const view = show(pending);
  fireEvent.change(await screen.findByDisplayValue("A table."), { target: { value: "A table and a figure." } });
  fireEvent.click(screen.getByRole("button", { name: "Approve plan" }));
  await waitFor(() => expect(screen.getByDisplayValue("A table and a figure.")).toBeDisabled());
  finish();
  // The chat has the answer, with the plan as approved, but not yet the frozen plan.
  const sent = vi.mocked(api.answerApproval).mock.calls[0][4] as PlanV2;
  view.rerender(
    <QueryClientProvider client={new QueryClient()}>
      <PlanCard conversationId="c1" approval={{ ...pending, plan: sent, state: "approved" }} />
    </QueryClientProvider>,
  );
  expect(screen.getByText("Approved. Freezing it…")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Show the plan as approved" }));
  expect(screen.getByText("A table and a figure.")).toBeInTheDocument();
});

it("says when an approved revision couldn't be frozen", () => {
  show({ ...pending, plan: revision, state: "approved", notFrozen: "Another revision of the same plan was approved first, so this one wasn't frozen." });
  expect(screen.getByText(/Approved, but not frozen: Another revision/)).toBeInTheDocument();
});

it("moves focus into the type choice, and keeps the buttons still when a type is chosen", async () => {
  vi.mocked(api.planSchema).mockResolvedValue({
    ...schema,
    types: [...schema.types, { id: "prediction", label: "Prediction", summary: "Predict an outcome.", required: [], optional: [], checks: "" }],
  });
  show(pending);
  fireEvent.click(await screen.findByRole("button", { name: "A different kind of analysis?" }));
  expect(screen.getByDisplayValue("Choose a type…")).toHaveFocus();
  expect(screen.getByText("Choose a type to see what it's for.")).toBeInTheDocument();
  fireEvent.change(screen.getByDisplayValue("Choose a type…"), { target: { value: "prediction" } });
  expect(screen.getByText("Predict an outcome.")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Send back" }));
  // Once answered, the panel goes.
  await waitFor(() => expect(screen.queryByRole("button", { name: "Send back" })).toBeNull());
});
