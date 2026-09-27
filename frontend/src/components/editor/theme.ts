// The editor on paper: the page's own tokens (styles/index.css), so it follows
// light and dark with everything else. Syntax is ink, weight and italics,
// with no colour: colour in DataLab means something (docs/DESIGN.md). Only
// problems are coloured, in the tones used everywhere for them.
import { HighlightStyle, syntaxHighlighting } from "@codemirror/language";
import { EditorView } from "@codemirror/view";
import { tags as t } from "@lezer/highlight";

const selection = "color-mix(in srgb, var(--color-ink) 16%, transparent)";

const theme = EditorView.theme({
  "&": {
    height: "100%",
    minHeight: "6rem",
    color: "var(--color-ink)",
    backgroundColor: "var(--color-field)",
    fontSize: "12.5px",
  },
  "&.cm-focused": { outline: "none" },
  ".cm-scroller": { fontFamily: "var(--font-mono)", lineHeight: "1.6", overflow: "auto" },
  ".cm-content": { padding: "8px 0", caretColor: "var(--color-ink)" },
  ".cm-line": { padding: "0 12px" },
  ".cm-cursor, .cm-dropCursor": { borderLeftColor: "var(--color-ink)", borderLeftWidth: "1.5px" },
  "&.cm-focused > .cm-scroller > .cm-selectionLayer .cm-selectionBackground, .cm-selectionBackground, .cm-content ::selection":
    { backgroundColor: selection },
  // The line being edited, only while the editor has focus.
  ".cm-activeLine": { backgroundColor: "transparent" },
  "&.cm-focused .cm-activeLine": { backgroundColor: "color-mix(in srgb, var(--color-ink) 4%, transparent)" },
  ".cm-gutters": {
    backgroundColor: "var(--color-field)",
    color: "var(--color-faint)",
    borderRight: "1px solid var(--color-line)",
  },
  ".cm-activeLineGutter": { backgroundColor: "transparent" },
  "&.cm-focused .cm-activeLineGutter": { color: "var(--color-ink)" },
  ".cm-lineNumbers .cm-gutterElement": { padding: "0 8px 0 12px", minWidth: "2.5em" },
  "&.cm-focused .cm-matchingBracket": { backgroundColor: selection, outline: "none" },
  ".cm-nonmatchingBracket": { color: "var(--color-danger)" },
  // Problems: a wavy underline in the problem's tone, and a note on hover or in the list (Ctrl+Shift+M).
  ".cm-lintRange": { backgroundImage: "none", textDecoration: "underline wavy", textUnderlineOffset: "3px" },
  ".cm-lintRange-error": { textDecorationColor: "var(--color-danger)" },
  ".cm-lintRange-warning": { textDecorationColor: "var(--color-attn)" },
  ".cm-lintRange-info, .cm-lintRange-hint": { textDecorationColor: "var(--color-you)" },
  ".cm-lintPoint:after": { borderBottomColor: "var(--color-danger)" },
  ".cm-lintPoint-warning:after": { borderBottomColor: "var(--color-attn)" },
  ".cm-lintPoint-info:after": { borderBottomColor: "var(--color-you)" },
  ".cm-tooltip": {
    backgroundColor: "var(--color-surface)",
    color: "var(--color-ink)",
    border: "1px solid var(--color-line)",
    borderRadius: "3px",
    fontFamily: "var(--font-sans)",
    fontSize: "13px",
  },
  ".cm-diagnostic": { padding: "4px 8px", borderLeftWidth: "3px" },
  ".cm-diagnostic-error": { borderLeftColor: "var(--color-danger)" },
  ".cm-diagnostic-warning": { borderLeftColor: "var(--color-attn)" },
  ".cm-diagnostic-info": { borderLeftColor: "var(--color-you)" },
  ".cm-panels": { backgroundColor: "var(--color-surface)", color: "var(--color-ink)" },
  ".cm-panels-bottom": { borderTop: "1px solid var(--color-line)" },
  ".cm-panel.cm-panel-lint ul [aria-selected]": { backgroundColor: selection, color: "var(--color-ink)" },
  ".cm-panel.cm-panel-lint button": { color: "var(--color-muted)" },
  // Diffs (@codemirror/merge): the soft tones for what was added and removed.
  ".cm-changedLine, &.cm-merge-b .cm-changedLine": { backgroundColor: "var(--color-data-soft)" },
  "&.cm-merge-a .cm-changedLine, .cm-deletedChunk": { backgroundColor: "var(--color-danger-soft)" },
  "&.cm-merge-b .cm-changedText, .cm-insertedLine .cm-changedText": {
    background: "color-mix(in srgb, var(--color-data) 22%, transparent)",
  },
  "&.cm-merge-a .cm-changedText, .cm-deletedChunk .cm-deletedText": {
    background: "color-mix(in srgb, var(--color-danger) 20%, transparent)",
  },
  ".cm-insertedLine": { backgroundColor: "var(--color-data-soft)" },
  ".cm-changeGutter": { width: "3px", paddingLeft: "0" },
  "&.cm-merge-b .cm-changedLineGutter, .cm-insertedLineGutter": { backgroundColor: "var(--color-data)" },
  "&.cm-merge-a .cm-changedLineGutter, .cm-deletedLineGutter": { backgroundColor: "var(--color-danger)" },
  ".cm-collapsedLines": {
    background: "var(--color-sunken)",
    color: "var(--color-muted)",
    fontFamily: "var(--font-sans)",
    fontSize: "12px",
  },
});

const highlight = HighlightStyle.define([
  { tag: [t.keyword, t.operatorKeyword, t.controlKeyword, t.definitionKeyword, t.moduleKeyword], fontWeight: "600" },
  { tag: [t.comment, t.lineComment, t.blockComment], color: "var(--color-faint)", fontStyle: "italic" },
  { tag: [t.string, t.special(t.string), t.regexp], color: "var(--color-muted)" },
  { tag: [t.number, t.bool, t.null, t.atom], color: "var(--color-ink)" },
  { tag: [t.propertyName, t.attributeName], color: "var(--color-ink)", fontWeight: "500" },
  { tag: t.invalid, color: "var(--color-danger)" },
  // Markdown
  { tag: t.heading, fontWeight: "600" },
  { tag: t.strong, fontWeight: "600" },
  { tag: t.emphasis, fontStyle: "italic" },
  { tag: t.link, textDecoration: "underline" },
  { tag: [t.processingInstruction, t.meta, t.contentSeparator], color: "var(--color-faint)" },
]);

export const paper = [theme, syntaxHighlighting(highlight)];
