import { createContext, type ReactNode, use, useLayoutEffect } from "react";

/** What every Workflows page's header shows besides its own: the button that reopens the chat, and the list's menu button. */
export interface SharedHeader {
  /** Shown after the page's own actions' start ("Ask for help" while the chat is closed). */
  actions?: ReactNode;
  /** Before the title (the workflow files, on a window too narrow to show them beside the page). */
  menu?: ReactNode;
  /** A header says it's on screen, so the page doesn't float its own "Ask for help" as well. */
  register: () => () => void;
}

export const SharedHeaderContext = createContext<SharedHeader | null>(null);

/**
 * A Workflows page's header: its title and text on the left, and one action
 * area on the right whose last button lines up with the right edge of the
 * content below. On a narrow page the actions wrap under the title, still
 * right-aligned, and never overlap it.
 */
export function PageHeader({ children, actions }: { children?: ReactNode; actions?: ReactNode }) {
  const shared = use(SharedHeaderContext);
  const register = shared?.register;
  useLayoutEffect(() => register?.(), [register]);
  const any = Boolean(actions || shared?.actions);
  return (
    <header data-page-header className="flex flex-wrap items-start gap-x-6 gap-y-3">
      <div className="flex min-w-0 flex-1 basis-[18rem] items-start gap-3">
        {shared?.menu}
        <div className="min-w-0 flex-1">{children}</div>
      </div>
      {any && (
        <div data-header-actions className="ml-auto flex max-w-full shrink-0 flex-wrap items-center justify-end gap-2">
          {shared?.actions}
          {actions}
        </div>
      )}
    </header>
  );
}
