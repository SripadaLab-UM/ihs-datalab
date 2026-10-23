import { expect, it } from "vitest";

import { visibleSections } from "./sectionIds";

it("shows a computer every section but Account", () => {
  expect(visibleSections(false, true)).toEqual([
    "connections",
    "appearance",
    "export-folders",
    "updates",
    "storage",
    "safety",
    "about",
  ]);
});

it("on the lab's server, keeps the lab's own settings for its administrators", () => {
  expect(visibleSections(true, false)).toEqual(["account", "appearance", "about"]);
  const admin = visibleSections(true, true);
  expect(admin).toContain("connections");
  expect(admin).toContain("safety");
  // A server has no export folders (exports are downloads) and is updated by its administrator.
  expect(admin).not.toContain("export-folders");
  expect(admin).not.toContain("updates");
});
