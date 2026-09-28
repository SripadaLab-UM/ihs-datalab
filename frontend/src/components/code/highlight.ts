// Static syntax highlighting: CodeMirror's parsers, without an editor. Loaded
// only when code is first shown (CodeBlock.tsx), and each language's parser
// is its own chunk, fetched when that language first appears.
//
// The result is plain data (text and class names), which the page renders as
// React text nodes: never as HTML, so nothing in the code can become markup.
import type { StreamParser } from "@codemirror/language";
import type { Parser } from "@lezer/common";
import { classHighlighter, highlightTree } from "@lezer/highlight";

import type { CodeLanguage } from "./languages";

export interface Token {
  text: string;
  /** Classes such as "tok-keyword" (styles/index.css), or "" for plain text. */
  className: string;
}

const parsers = new Map<CodeLanguage, Promise<Parser | null>>();

async function stream<State>(load: () => Promise<StreamParser<State>>): Promise<Parser> {
  const [{ StreamLanguage }, mode] = await Promise.all([import("@codemirror/language"), load()]);
  return StreamLanguage.define(mode).parser;
}

async function loadParser(language: CodeLanguage): Promise<Parser | null> {
  switch (language) {
    case "sql": {
      // The study database is Oracle.
      const { sql, PLSQL } = await import("@codemirror/lang-sql");
      return sql({ dialect: PLSQL }).language.parser;
    }
    case "yaml":
      return (await import("@codemirror/lang-yaml")).yaml().language.parser;
    case "markdown":
      return (await import("@codemirror/lang-markdown")).markdown().language.parser;
    case "r":
      return stream(async () => (await import("@codemirror/legacy-modes/mode/r")).r);
    case "python":
      return stream(async () => (await import("@codemirror/legacy-modes/mode/python")).python);
    case "shell":
      return stream(async () => (await import("@codemirror/legacy-modes/mode/shell")).shell);
    case "json":
      return stream(async () => (await import("@codemirror/legacy-modes/mode/javascript")).json);
    case "diff":
    case "text":
      return null;
  }
}

function parserFor(language: CodeLanguage): Promise<Parser | null> {
  let parser = parsers.get(language);
  if (!parser) {
    parser = loadParser(language).catch(() => null);
    parsers.set(language, parser);
  }
  return parser;
}

/** `text` as lines of tokens. A language without a parser comes back as plain lines. */
export async function highlightLines(text: string, language: CodeLanguage): Promise<Token[][]> {
  if (language === "diff") return diffLines(text);
  const parser = await parserFor(language);
  if (!parser) return plainLines(text);
  const lines: Token[][] = [[]];
  let at = 0;
  const take = (to: number, className: string) => {
    const pieces = text.slice(at, to).split("\n");
    pieces.forEach((piece, i) => {
      if (i > 0) lines.push([]);
      if (piece) lines[lines.length - 1].push({ text: piece, className });
    });
    at = to;
  };
  highlightTree(parser.parse(text), classHighlighter, (from, to, classes) => {
    if (from > at) take(from, "");
    if (to > at) take(to, classes);
  });
  take(text.length, "");
  return lines;
}

export function plainLines(text: string): Token[][] {
  return text.split("\n").map((line) => (line ? [{ text: line, className: "" }] : []));
}

/** A unified diff, marked line by line: what was added, removed, and where. */
function diffLines(text: string): Token[][] {
  return text.split("\n").map((line) => {
    if (!line) return [];
    const className = /^(\+\+\+|---|@@|diff |index )/.test(line)
      ? "tok-meta"
      : line.startsWith("+")
        ? "tok-inserted"
        : line.startsWith("-")
          ? "tok-deleted"
          : "";
    return [{ text: line, className }];
  });
}
