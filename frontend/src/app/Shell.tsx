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
      <header className="flex items-center gap-6 border-b border-line bg-surface px-4">
        <span className="py-3 font-semibold tracking-tight">DataLab</span>
        <nav className="flex gap-1">
          {TABS.map((tab) => (
            <NavLink
              key={tab.to}
              to={tab.to}
              className={({ isActive }) =>
                clsx(
                  "border-b-2 px-3 py-3 text-sm",
                  isActive ? "border-accent font-medium" : "border-transparent text-muted hover:text-ink",
                )
              }
            >
              {tab.label}
            </NavLink>
          ))}
        </nav>
        {health.data?.profile === "practice" && (
          <span className="ml-auto rounded-full bg-research-soft px-2 py-0.5 text-xs font-medium text-research">
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
    <div className="flex h-full items-center justify-center text-muted">{name} is coming in a later milestone.</div>
  );
}
