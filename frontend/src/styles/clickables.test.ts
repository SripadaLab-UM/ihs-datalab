// A lint in test form: something you can click is a button or a link, so it
// shows the hand, takes focus and answers Enter and Space. Any other element
// with a click, pointer-down or mouse-down handler must have an interactive
// role and a tabIndex (and its own key handling). See docs/DESIGN.md,
// "Cursor, hover and focus". It reads the JSX with TypeScript's own parser, so
// strings, nested braces and spreads can't hide a handler.
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative } from "node:path";

import ts from "typescript";
import { expect, it } from "vitest";

const SRC = join(__dirname, "..");

function sources(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) return sources(path);
    return /\.tsx$/.test(name) && !/\.test\.tsx$/.test(name) ? [path] : [];
  });
}

const HANDLERS = new Set(["onClick", "onPointerDown", "onMouseDown"]);
// Elements that are controls already, or whose click is a control's (a label's is its field's).
const NATIVE = new Set(["button", "a", "input", "select", "textarea", "summary", "label", "option", "details"]);
const INTERACTIVE_ROLES = new Set([
  "button", "link", "tab", "menuitem", "menuitemcheckbox", "menuitemradio", "option", "switch", "checkbox",
  "radio", "treeitem", "gridcell", "row", "slider", "spinbutton", "combobox",
]); // prettier-ignore

/**
 * The justified exceptions, each marked in the source so it can't happen by accident:
 * - `data-scrim`: the dimmed layer behind a drawer, docked chat or dialog. A
 *   click on it closes what's over it; the keyboard's way is Escape (and the
 *   Close button), so the scrim itself is not a control.
 * - `data-row` (the SQL results grid): a click opens a row to its full values;
 *   the keyboard's way is the grid's one tab stop and its arrow keys.
 * - `data-tree-row`: keyboard handled on the parent role=treeitem.
 * - A dialog's box whose only click handling stops the click reaching its scrim.
 */
const MARKERS = ["data-scrim", "data-row", "data-tree-row"];

interface Found {
  tag: string;
  attrs: Map<string, ts.JsxAttribute>;
  handlers: string[];
  line: number;
}

/** The DOM elements in this source with a click, pointer-down or mouse-down handler. */
function elements(file: string, text = readFileSync(file, "utf8")): Found[] {
  const source = ts.createSourceFile(file, text, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  const out: Found[] = [];
  const visit = (node: ts.Node) => {
    if ((ts.isJsxOpeningElement(node) || ts.isJsxSelfClosingElement(node)) && ts.isIdentifier(node.tagName)) {
      const tag = node.tagName.text;
      // Lowercase: a DOM element. Components (Button, Link) are checked where they render one.
      if (/^[a-z]/.test(tag)) {
        const attrs = new Map<string, ts.JsxAttribute>();
        const handlers: string[] = [];
        for (const prop of node.attributes.properties) {
          if (ts.isJsxAttribute(prop)) {
            const name = prop.name.getText(source);
            attrs.set(name, prop);
            if (HANDLERS.has(name)) handlers.push(name);
          } else if (ts.isJsxSpreadAttribute(prop)) {
            // {...{ onClick }} or {...{ onClick: go }}: an object written right there.
            const expr = prop.expression;
            if (ts.isObjectLiteralExpression(expr)) {
              for (const p of expr.properties) {
                const name = p.name?.getText(source);
                if (name && HANDLERS.has(name)) handlers.push(name);
              }
            }
          }
        }
        if (handlers.length) out.push({ tag, attrs, handlers, line: source.getLineAndCharacterOfPosition(node.getStart()).line + 1 });
      }
    }
    ts.forEachChild(node, visit);
  };
  visit(source);
  return out;
}

function stringValue(attr: ts.JsxAttribute | undefined): string | undefined {
  const init = attr?.initializer;
  if (!init) return undefined;
  if (ts.isStringLiteral(init)) return init.text;
  if (ts.isJsxExpression(init) && init.expression && ts.isStringLiteralLike(init.expression)) return init.expression.text;
  return undefined;
}

function problem(el: Found): string | null {
  if (NATIVE.has(el.tag)) return null;
  if (MARKERS.some((m) => el.attrs.has(m))) return null;
  const role = stringValue(el.attrs.get("role"));
  if (role === "dialog" && el.handlers.every((h) => h === "onClick")) {
    const handler = el.attrs.get("onClick")?.initializer?.getText() ?? "";
    if (/^\{\s*\(\w*\)\s*=>\s*\w+\.stopPropagation\(\)\s*\}$/.test(handler)) return null;
  }
  if (role && INTERACTIVE_ROLES.has(role) && el.attrs.has("tabIndex")) return null;
  return role && !INTERACTIVE_ROLES.has(role) ? `role="${role}" isn't a control's role` : "no interactive role and tabIndex";
}

it("has no clickable element that isn't a control", () => {
  const found: string[] = [];
  for (const file of sources(SRC)) {
    for (const el of elements(file)) {
      const why = problem(el);
      if (why) found.push(`${relative(SRC, file)}:${el.line} <${el.tag} ${el.handlers.join(" ")}>: ${why}`);
    }
  }
  expect(found, "use a <button>, or add an interactive role, tabIndex and key handling").toEqual([]);
});

it("catches the ways a handler can hide, and lets the marked exceptions through", () => {
  const sample = `
    const a = <div className="p-2 {x}" title="a > b" onClick={() => go({ deep: { deeper: 1 } })}>Open</div>;
    const b = <li onMouseDown={go} />;
    const c = <span {...{ onPointerDown }} />;
    const d = <section role="region" tabIndex={0} onClick={go} />;
    const e = <custom-el onClick={go} />;
    const ok1 = <div role="button" tabIndex={0} onClick={go} onKeyDown={key} />;
    const ok2 = <div data-scrim className="inset-0" onClick={close} />;
    const ok3 = <div data-tree-row onClick={go} />;
    const ok4 = <div role="dialog" onClick={(e) => e.stopPropagation()} />;
    const ok5 = <button onClick={go} />;
  `;
  const flagged = elements(join(__dirname, "sample.tsx"), sample).filter((el) => problem(el) !== null);
  expect(flagged.map((el) => el.tag)).toEqual(["div", "li", "span", "section", "custom-el"]);
});
