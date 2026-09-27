import { afterEach, expect, it, vi } from "vitest";

import { api } from "./client";
import { ApiError, request, SIGNED_OUT } from "./http";
import { knowledgeApi } from "./knowledge";
import { pipelinesApi } from "./pipelines";
import { sqlApi } from "./sql";
import { workflowsApi } from "./workflows";

const answer = (status: number, body?: unknown) =>
  vi.fn(async () => new Response(body === undefined ? null : JSON.stringify(body), { status }));

afterEach(() => vi.unstubAllGlobals());

it("sends JSON and reads JSON back", async () => {
  const fetch = answer(200, { ok: 1 });
  vi.stubGlobal("fetch", fetch);
  expect(await request("/api/x", { method: "POST", body: "{}" })).toEqual({ ok: 1 });
  expect(fetch).toHaveBeenCalledWith("/api/x", expect.objectContaining({ method: "POST", headers: { "content-type": "application/json" } }));
});

it("reads nothing from a 204", async () => {
  vi.stubGlobal("fetch", answer(204));
  expect(await request("/api/x")).toBeUndefined();
});

it("turns an error's detail into an ApiError", async () => {
  vi.stubGlobal("fetch", answer(422, { detail: "The plan's Devices says only 'N/A'." }));
  const error = await request("/api/x").catch((e: unknown) => e);
  expect(error).toBeInstanceOf(ApiError);
  expect(error).toMatchObject({ status: 422, message: "The plan's Devices says only 'N/A'." });
});

it("says when DataLab no longer knows this browser", async () => {
  vi.stubGlobal("fetch", answer(401, {}));
  const signedOut = vi.fn();
  window.addEventListener(SIGNED_OUT, signedOut);
  await request("/api/x").catch(() => undefined);
  window.removeEventListener(SIGNED_OUT, signedOut);
  expect(signedOut).toHaveBeenCalledTimes(1);
});

it("asks each new tab's backend whether it's ready", async () => {
  const fetch = answer(200, { available: false });
  vi.stubGlobal("fetch", fetch);
  for (const status of [sqlApi.status, knowledgeApi.status, workflowsApi.status, pipelinesApi.status]) {
    expect(await status()).toEqual({ available: false });
  }
  expect(fetch.mock.calls.map((call) => (call as unknown[])[0])).toEqual([
    "/api/sql/status",
    "/api/knowledge/status",
    "/api/workflows/status",
    "/api/pipelines/status",
  ]);
});

it("keeps every call the Workspace and Settings use on `api`", () => {
  for (const name of ["health", "modes", "send", "answerApproval", "files", "fileUrl", "destinations", "export", "runSafetyCheck"]) {
    expect(typeof api[name as keyof typeof api]).toBe("function");
  }
  expect(api.fileUrl("c1", "outputs", "a b/c.png", 2)).toBe("/api/conversations/c1/files/outputs/a%20b/c.png?checkpoint=2");
});
