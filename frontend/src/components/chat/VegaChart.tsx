import { useEffect, useRef, useState } from "react";
import embed from "vega-embed";
import { expressionInterpreter } from "vega-interpreter";

import { isDark } from "@/lib/theme";

/**
 * Draws a Vega-Lite chart from an agent's answer.
 * - The expression interpreter keeps Vega from generating code at runtime, so
 *   the page's strict security policy can stay in place.
 * - Charts must embed their data. Any URL a spec points at is refused before
 *   Vega would fetch it.
 */
export default function VegaChart({ spec }: { spec: string }) {
  const container = useRef<HTMLDivElement>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!container.current) return;
    let parsed: unknown;
    try {
      parsed = JSON.parse(spec);
    } catch {
      setError("This chart's specification isn't valid JSON.");
      return;
    }
    // Vega would only log a refused link and draw an empty chart: say why.
    const refuse = () => {
      setError("Charts can't load data from links. The data must be in the chart itself.");
      return Promise.reject(new Error("Charts can't load data from links."));
    };
    const dark = isDark();
    const result = embed(container.current, parsed as never, {
      actions: { export: true, source: false, compiled: false, editor: false },
      renderer: "svg",
      theme: dark ? "dark" : undefined,
      config: { background: "transparent" },
      ast: true,
      expr: expressionInterpreter,
      loader: { load: refuse, sanitize: refuse } as never,
    });
    result.catch((reason: unknown) => setError(String(reason)));
    return () => {
      result.then((view) => view.finalize()).catch(() => {});
    };
  }, [spec]);

  return error ? (
    <div className="rounded border border-line bg-sunken p-3 text-sm text-muted">Chart not shown: {error}</div>
  ) : (
    <div ref={container} className="my-3 overflow-x-auto" />
  );
}
