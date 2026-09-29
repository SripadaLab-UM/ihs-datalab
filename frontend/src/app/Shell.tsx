import { useQuery } from "@tanstack/react-query";
import clsx from "clsx";
import { useEffect, useRef, useState } from "react";
import { NavLink, Outlet, useLocation } from "react-router";

import { api, SIGNED_OUT } from "@/api/client";
import { Brand, usePracticeTab } from "@/app/brand";
import { clearAllDrafts } from "@/components/chat/plan";
import { DockerBanner } from "@/features/docker/DockerBanner";
import { HelpLink } from "@/features/help/HelpLink";
import { TourProvider } from "@/features/help/Tour";
import { UpdatePill, UpdatingBanner } from "@/features/settings/UpdatePill";
import { AttentionMenu } from "@/features/toolbar/AttentionMenu";
import { MoreMenu } from "@/features/toolbar/MoreMenu";
import { SessionMenu } from "@/features/toolbar/SessionMenu";
import { Shortcuts } from "@/features/toolbar/Toolbar";

const TABS = [
  { to: "/workspace", label: "Workspace" },
  { to: "/sql", label: "SQL Playground" },
  { to: "/workflows", label: "Workflows" },
  { to: "/pipelines", label: "Pipelines" },
  { to: "/knowledge", label: "Knowledge" },
  // "Settings" on narrower windows, so every tab fits.
  { to: "/settings", label: "Settings & Safety", short: "Settings" },
];

export function Shell() {
  const health = useQuery({ queryKey: ["health"], queryFn: api.health });
  const practice = health.data?.profile === "practice";
  usePracticeTab(practice);
  const [signedOut, setSignedOut] = useState(false);
  // On a narrow window the tabs scroll: keep the one you're on in view.
  const nav = useRef<HTMLElement>(null);
  const { pathname } = useLocation();
  useEffect(() => {
    nav.current?.querySelector<HTMLElement>('[aria-current="page"]')?.scrollIntoView?.({
      block: "nearest",
      inline: "nearest",
    });
  }, [pathname]);
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
          DataLab doesn't recognise this window any more (it restarted, or the session was ended), so what's shown here
          may be out of date. To get back in, quit DataLab (close its window, Terminal on a Mac or PowerShell on
          Windows, or press Ctrl-C in it) and start it again from its launcher: an old sign-in link won't work again.
        </div>
      )}
      <UpdatingBanner />
      <DockerBanner />
      <header className="flex items-center gap-4 border-b border-line px-5 lg:gap-8">
        <Brand practice={practice} />
        <nav ref={nav} className="flex min-w-0 gap-1 self-stretch overflow-x-auto">
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
              {tab.short ? (
                <>
                  <span className="2xl:hidden">{tab.short}</span>
                  <span className="hidden 2xl:inline">{tab.label}</span>
                </>
              ) : (
                tab.label
              )}
            </NavLink>
          ))}
        </nav>
        {/* The pill, the shortcuts, the practice badge, Help and More sit together
            at the right, closer to each other than to the tabs; End session is
            in its own menu at the far end, set apart. */}
        <div className="ml-auto flex shrink-0 items-center gap-2 self-stretch 2xl:gap-3">
          {/* Below 2xl "Update available" is one of the attention items instead. */}
          <span className="hidden 2xl:contents">
            <UpdatePill />
          </span>
          <AttentionMenu />
          <Shortcuts />
          {/* From lg to 2xl the brand beside the tabs says "practice" itself, so the
              badge makes room there for the shortcuts. */}
          {practice && (
            <span
              className="shrink-0 self-center lg:hidden 2xl:inline rounded-[2px] border border-attn/50 px-2 py-1 font-serif text-[14px] leading-none text-attn italic"
              title="The practice profile: synthetic data only, never the real study database"
            >
              practice<span className="hidden xl:inline"> · synthetic data</span>
            </span>
          )}
          <HelpLink />
          <MoreMenu />
          <div className="flex shrink-0 self-stretch border-l border-line pl-1">
            <SessionMenu />
          </div>
        </div>
      </header>
      <div className="min-h-0 flex-1">
        <TourProvider>
          <Outlet />
        </TourProvider>
      </div>
    </div>
  );
}
