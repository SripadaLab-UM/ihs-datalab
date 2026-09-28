// Light, dark, or whatever the computer uses (the default). The choice is this
// browser's own, kept in its local storage; when storage is refused (a private
// window, blocked site data) DataLab just follows the computer.
import { useCallback, useEffect, useState } from "react";

export type Theme = "light" | "dark" | "system";

export const THEME_KEY = "datalab.theme";
const THEMES: Theme[] = ["light", "dark", "system"];
const DARK = "(prefers-color-scheme: dark)";

export function readTheme(): Theme {
  try {
    const saved = localStorage.getItem(THEME_KEY);
    return THEMES.includes(saved as Theme) ? (saved as Theme) : "system";
  } catch {
    return "system";
  }
}

/** Saves the choice; false when this browser won't keep it (it still applies until the page closes). */
export function saveTheme(theme: Theme): boolean {
  try {
    if (theme === "system") localStorage.removeItem(THEME_KEY);
    else localStorage.setItem(THEME_KEY, theme);
    return true;
  } catch {
    return false;
  }
}

/** Sets the page's colours: index.css reads `data-theme` on <html>, and follows the computer without it. */
export function applyTheme(theme: Theme) {
  const root = document.documentElement;
  if (theme === "system") delete root.dataset.theme;
  else root.dataset.theme = theme;
}

/** Whether the page is dark right now, for things drawn outside CSS (charts). */
export function isDark(): boolean {
  const chosen = document.documentElement.dataset.theme;
  if (chosen === "dark") return true;
  if (chosen === "light") return false;
  return typeof window !== "undefined" && !!window.matchMedia?.(DARK).matches;
}

/** The choice, and a setter that applies and saves it. `kept` is false if the browser refused to save it. */
export function useTheme() {
  const [theme, setThemeState] = useState<Theme>(readTheme);
  const [kept, setKept] = useState(true);
  const setTheme = useCallback((next: Theme) => {
    applyTheme(next);
    setKept(saveTheme(next));
    setThemeState(next);
  }, []);
  // Another DataLab window changed it.
  useEffect(() => {
    const onStorage = (event: StorageEvent) => {
      if (event.key !== null && event.key !== THEME_KEY) return;
      const next = readTheme();
      applyTheme(next);
      setThemeState(next);
    };
    window.addEventListener("storage", onStorage);
    return () => window.removeEventListener("storage", onStorage);
  }, []);
  return { theme, setTheme, kept };
}
