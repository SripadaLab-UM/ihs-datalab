// Each language is its own chunk, loaded when an editor first needs it.
import { StreamLanguage } from "@codemirror/language";
import type { Extension } from "@codemirror/state";
import { EditorView } from "@codemirror/view";

import type { EditorLanguage } from "./types";

export async function loadLanguage(language: EditorLanguage): Promise<Extension> {
  switch (language) {
    case "sql": {
      // The study database is Oracle.
      const { sql, PLSQL } = await import("@codemirror/lang-sql");
      return sql({ dialect: PLSQL, upperCaseKeywords: true });
    }
    case "yaml": {
      const { yaml } = await import("@codemirror/lang-yaml");
      return yaml();
    }
    case "markdown": {
      const { markdown } = await import("@codemirror/lang-markdown");
      // Prose reads better wrapped.
      return [markdown(), EditorView.lineWrapping];
    }
    case "r": {
      const { r } = await import("@codemirror/legacy-modes/mode/r");
      return StreamLanguage.define(r);
    }
    case "text":
      return [];
  }
}
