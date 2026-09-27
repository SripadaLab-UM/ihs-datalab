import clsx from "clsx";
import { Link, useLocation } from "react-router";

import { helpPath, topicFor } from "@/lib/guide";

/** Help, opened on the topic of the screen it's pressed on. */
export function HelpLink() {
  const location = useLocation();
  const inHelp = location.pathname === "/help" || location.pathname.startsWith("/help/");
  const topic = topicFor(location.pathname);
  return (
    <Link
      to={inHelp ? "/help" : helpPath(topic)}
      state={inHelp ? undefined : { from: `${location.pathname}${location.search}${location.hash}` }}
      aria-current={inHelp ? "page" : undefined}
      aria-label={inHelp ? "Help" : `Help: ${topic.title}`}
      className={clsx(
        "flex shrink-0 items-center self-stretch border-b-2 px-2.5 font-sans text-[13.5px] focus-visible:outline-offset-[-3px]",
        inHelp ? "border-ink font-medium text-ink" : "border-transparent text-muted hover:text-ink",
      )}
    >
      Help
    </Link>
  );
}
