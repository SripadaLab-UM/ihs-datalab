import { useQuery } from "@tanstack/react-query";
import clsx from "clsx";
import { useEffect, useState } from "react";
import { NavLink, Outlet } from "react-router";

import { api, SIGNED_OUT } from "@/api/client";
import { clearAllDrafts } from "@/components/chat/plan";

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
    const onSignedOut = () => {
      setSignedOut(true);
      clearAllDrafts(); // plan edits in this tab go with the sign-in
    };
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
      <header className="flex items-center gap-8 border-b border-line px-5">
        <span className="py-3.5 font-serif text-[21px] leading-none">datalab.</span>
        <nav className="flex gap-1 self-stretch overflow-x-auto">
          {TABS.map((tab) => (
            <NavLink
              key={tab.to}
              to={tab.to}
              className={({ isActive }) =>
                clsx(
                  "flex items-center border-b-2 px-2.5 font-sans text-[13.5px] whitespace-nowrap transition-colors focus-visible:outline-offset-[-3px]",
                  isActive ? "border-ink font-medium text-ink" : "border-transparent text-muted hover:text-ink",
                )
              }
            >
              {tab.label}
            </NavLink>
          ))}
        </nav>
        {health.data?.profile === "practice" && (
          <span
            className="ml-auto shrink-0 self-center rounded-[2px] border border-attn/50 px-2 py-1 font-serif text-[14px] leading-none text-attn italic"
            title="The practice profile: synthetic data only, never the real study database"
          >
            practice · synthetic data
          </span>
        )}
      </header>
      <div className="min-h-0 flex-1">
        <Outlet />
      </div>
    </div>
  );
}
