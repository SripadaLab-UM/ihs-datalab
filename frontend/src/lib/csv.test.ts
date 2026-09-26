import { describe, expect, it } from "vitest";

import { formatBytes, parseCsv } from "./csv";

describe("parseCsv", () => {
  it("reads quoted fields with commas, quotes, and newlines", () => {
    expect(parseCsv('a,b\n"x, y","say ""hi"""\n"two\nlines",3\n')).toEqual([
      ["a", "b"],
      ["x, y", 'say "hi"'],
      ["two\nlines", "3"],
    ]);
  });

  it("keeps a last line with no newline, unless the file was cut off", () => {
    expect(parseCsv("a,b\n1,2")).toEqual([["a", "b"], ["1", "2"]]);
    expect(parseCsv("a,b\n1,2", { truncated: true })).toEqual([["a", "b"]]);
  });

  it("stops at the row limit and handles tabs and CRLF", () => {
    expect(parseCsv("a\tb\r\n1\t2\r\n3\t4\r\n", { delimiter: "\t", maxRows: 2 })).toEqual([
      ["a", "b"],
      ["1", "2"],
    ]);
  });
});

it("formats sizes", () => {
  expect(formatBytes(512)).toBe("512 bytes");
  expect(formatBytes(2048)).toBe("2.0 KB");
  expect(formatBytes(5 * 1024 * 1024)).toBe("5.0 MB");
});
