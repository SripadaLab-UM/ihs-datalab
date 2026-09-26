import { useQuery } from "@tanstack/react-query";
import clsx from "clsx";
import { useEffect, useState } from "react";
import { NavLink, Outlet } from "react-router";

import { api, SIGNED_OUT } from "@/api/client";

const TABS = [
  { to: "/workspace", label: "Workspace" },
  { to: "/sql", label: "SQL Playground" },
  { to: "/workflows", label: "Workflows" },
  { to: "/pipelines", label: "Pipelines" },
  { to: "/knowledge", label: "Knowledge" },
  { to: "/settings", label: "Settings & Safety" },
];

export function Shell() {
  const health = useQuery({ queryKey: ["health"], queryFn: api.health });
  const [signedOut, setSignedOut] = useState(false);
  useEffect(() => {
    const onSignedOut = () => setSignedOut(true);
    window.addEventListener(SIGNED_OUT, onSignedOut);
    return () => window.removeEventListener(SIGNED_OUT, onSignedOut);
  }, []);
  return (
    <div className="flex h-full flex-col">
      {signedOut && (
        <div role="alert" className="bg-danger px-4 py-2 text-center text-sm font-medium text-white">
          DataLab doesn't recognise this window any more (it may have restarted). What's shown here may be out of date:
          open DataLab again with the link it printed when it started.
        </div>
      )}
      <header className="flex items-center gap-5 border-b border-line bg-surface px-4 py-2">
        <span className="flex items-center gap-2 font-semibold tracking-[-0.01em]">
          <svg viewBox="0 0 24 24" width="22" height="22" aria-hidden="true">
            <rect x="1" y="1" width="22" height="22" rx="7" fill="var(--color-accent)" />
            <path d="M6 15.5l3.5-3.5 3 2 5.5-6" fill="none" stroke="var(--color-accent-ink)" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
          DataLab
        </span>
        <nav className="flex gap-0.5 overflow-x-auto">
          {TABS.map((tab) => (
            <NavLink
              key={tab.to}
              to={tab.to}
              className={({ isActive }) =>
                clsx(
                  "rounded-full px-3 py-1.5 text-[14px] whitespace-nowrap transition-colors",
                  isActive ? "bg-sunken font-medium text-ink" : "text-muted hover:bg-sunken/60 hover:text-ink",
                )
              }
            >
              {tab.label}
            </NavLink>
          ))}
        </nav>
        {health.data?.profile === "practice" && (
          <span
            className="ml-auto shrink-0 rounded-full bg-attn-soft px-2.5 py-1 text-xs font-medium text-attn"
            title="The practice profile: synthetic data only, never the real study database"
          >
            Practice · synthetic data
          </span>
        )}
      </header>
      <div className="min-h-0 flex-1">
        <Outlet />
      </div>
    </div>
  );
}

export function ComingSoon({ name }: { name: string }) {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-2 text-muted">
      <p className="text-[17px] font-medium text-ink">{name}</p>
      <p>Coming in a later milestone.</p>
    </div>
  );
}
