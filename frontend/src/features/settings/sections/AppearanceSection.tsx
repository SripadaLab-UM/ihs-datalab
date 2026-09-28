import clsx from "clsx";

import { type Theme, useTheme } from "@/lib/theme";

import { Section } from "./Section";

const CHOICES: { value: Theme; label: string; about: string }[] = [
  { value: "light", label: "Light", about: "Dark ink on white paper." },
  { value: "dark", label: "Dark", about: "Light ink on a dark page." },
  { value: "system", label: "System", about: "Whatever this computer is set to, changing when it does." },
];

/** Appearance: Light, Dark, or the computer's own setting (the default). Kept in this browser. */
export function AppearanceSection() {
  const { theme, setTheme, kept } = useTheme();
  return (
    <Section title="Appearance">
      <p className="mt-1 text-sm text-muted">How DataLab looks in this browser. Other computers keep their own choice.</p>
      <fieldset className="mt-5">
        <legend className="dl-label">Theme</legend>
        <div className="mt-2 grid gap-2 sm:grid-cols-3">
          {CHOICES.map((choice) => {
            const chosen = theme === choice.value;
            return (
              <label
                key={choice.value}
                className={clsx(
                  "flex cursor-pointer items-start gap-2.5 rounded-[3px] border px-3 py-2.5 text-sm transition-colors has-[:focus-visible]:outline-2 has-[:focus-visible]:outline-offset-2 has-[:focus-visible]:outline-ink",
                  chosen ? "border-ink" : "border-line hover:border-muted",
                )}
              >
                <input
                  type="radio"
                  name="theme"
                  value={choice.value}
                  checked={chosen}
                  onChange={() => setTheme(choice.value)}
                  aria-labelledby={`theme-${choice.value}`}
                  aria-describedby={`theme-${choice.value}-about`}
                  className="mt-[3px] shrink-0 accent-[var(--color-ink)] focus-visible:outline-none"
                />
                <span>
                  <span id={`theme-${choice.value}`} className="block font-medium">
                    {choice.label}
                  </span>
                  <span id={`theme-${choice.value}-about`} className="block text-xs text-muted">
                    {choice.about}
                  </span>
                </span>
              </label>
            );
          })}
        </div>
      </fieldset>
      {!kept && (
        <p className="mt-3 text-sm text-attn" role="status">
          This browser won't let DataLab remember the choice, so it lasts until this page is reloaded or closed.
        </p>
      )}
    </Section>
  );
}
