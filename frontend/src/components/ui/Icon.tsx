import type { IconName } from "@/components/chat/activity";

// One stroke style for every icon, drawn on a 24-unit grid.
const PATHS: Record<IconName | "lock" | "send" | "stop" | "close" | "chevron" | "open" | "history" | "export" | "attach" | "image" | "page" | "restore" | "menu" | "trash" | "key" | "sun" | "more" | "message" | "user", string> = {
  book: "M4 5.5A1.5 1.5 0 0 1 5.5 4H11v15H5.5A1.5 1.5 0 0 0 4 20.5zM20 5.5A1.5 1.5 0 0 0 18.5 4H13v15h5.5a1.5 1.5 0 0 1 1.5 1.5z",
  search: "M16.5 10.5a6 6 0 1 1-12 0 6 6 0 0 1 12 0zM15 15l5 5",
  table: "M4 6.5A1.5 1.5 0 0 1 5.5 5h13A1.5 1.5 0 0 1 20 6.5v11a1.5 1.5 0 0 1-1.5 1.5h-13A1.5 1.5 0 0 1 4 17.5zM4 10h16M10 10v9",
  link: "M10 14a4 4 0 0 1 0-5.7l2.3-2.3a4 4 0 0 1 5.7 5.7L16.5 13M14 10a4 4 0 0 1 0 5.7l-2.3 2.3a4 4 0 0 1-5.7-5.7L7.5 11",
  db: "M5 6c0 1.4 3.1 2.5 7 2.5S19 7.4 19 6s-3.1-2.5-7-2.5S5 4.6 5 6zm0 0v12c0 1.4 3.1 2.5 7 2.5s7-1.1 7-2.5V6M5 12c0 1.4 3.1 2.5 7 2.5s7-1.1 7-2.5",
  code: "m9 8-4 4 4 4M15 8l4 4-4 4",
  chart: "M4 19h16M6 15l4-4 3 2 5-6",
  shield: "M12 3.5 5 6v5.5c0 4 2.9 7.3 7 9 4.1-1.7 7-5 7-9V6zM9 12l2 2 4-4",
  file: "M7 3.5h6.5L18 8v11.5a1 1 0 0 1-1 1H7a1 1 0 0 1-1-1v-15a1 1 0 0 1 1-1zM13 3.5V8h5",
  folder: "M3.5 7A1.5 1.5 0 0 1 5 5.5h4l2 2h8A1.5 1.5 0 0 1 20.5 9v8.5A1.5 1.5 0 0 1 19 19H5a1.5 1.5 0 0 1-1.5-1.5z",
  eye: "M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12zM14.5 12a2.5 2.5 0 1 1-5 0 2.5 2.5 0 0 1 5 0z",
  globe: "M20.5 12a8.5 8.5 0 1 1-17 0 8.5 8.5 0 0 1 17 0zM3.5 12h17M12 3.5c2.2 2.4 3.3 5.2 3.3 8.5s-1.1 6.1-3.3 8.5c-2.2-2.4-3.3-5.2-3.3-8.5S9.8 5.9 12 3.5z",
  pen: "M4.5 19.5 5.5 15 15.8 4.7a1.7 1.7 0 0 1 2.5 0l1 1a1.7 1.7 0 0 1 0 2.5L9 18.5zM13.5 7l3.5 3.5",
  alert: "M12 4 21 19.5H3zM12 10v4.5M12 17.2v.3",
  check: "m5 12.5 4.5 4.5L19 7",
  spark: "M12 3.5v5M12 15.5v5M3.5 12h5M15.5 12h5M6.5 6.5l2.5 2.5M15 15l2.5 2.5M17.5 6.5 15 9M9 15l-2.5 2.5",
  lock: "M7 10.5V8a5 5 0 0 1 10 0v2.5M5.5 10.5h13v9h-13z",
  send: "M4 12 20 4l-5 16-3-6.5zM12 13.5 20 4",
  stop: "M7 7h10v10H7z",
  close: "M6 6l12 12M18 6 6 18",
  chevron: "m9 6 6 6-6 6",
  open: "M14 4h6v6M20 4l-8.5 8.5M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5",
  history: "M4 12a8 8 0 1 0 2.4-5.7M4 4.5v3.8h3.8M12 8v4.5l3 2",
  export: "M12 15V4M8 8l4-4 4 4M5 14v5a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1v-5",
  image: "M4.5 5.5h15v13h-15zM4.5 15.5l4-4 3.5 3.5 2.5-2.5 5 5M15 9.5h.01",
  page: "M7 3.5h6.5L18 8v11.5a1 1 0 0 1-1 1H7a1 1 0 0 1-1-1v-15a1 1 0 0 1 1-1zM9 12h6M9 15.5h6M9 8.5h2",
  restore: "M4 12a8 8 0 1 0 2.4-5.7M4 4.5v3.8h3.8",
  menu: "M4 7h16M4 12h16M4 17h16",
  trash: "M4 7h16M9 7V4.5h6V7M6.5 7l1 12.5a1 1 0 0 0 1 1h7a1 1 0 0 0 1-1l1-12.5M10 11v6M14 11v6",
  key: "M12 12a4 4 0 1 1-8 0 4 4 0 0 1 8 0zM12 12h8.5M17.5 12v3M20.5 12v2.5",
  sun: "M15.5 12a3.5 3.5 0 1 1-7 0 3.5 3.5 0 0 1 7 0zM12 3v2M12 19v2M3 12h2M19 12h2M5.6 5.6 7 7M17 17l1.4 1.4M5.6 18.4 7 17M17 7l1.4-1.4",
  more: "M7 12a1 1 0 1 1-2 0 1 1 0 0 1 2 0zM13 12a1 1 0 1 1-2 0 1 1 0 0 1 2 0zM19 12a1 1 0 1 1-2 0 1 1 0 0 1 2 0z",
  message: "M4.5 5.5h15v10h-8l-4.5 3.5v-3.5h-2.5z",
  user: "M15.5 8a3.5 3.5 0 1 1-7 0 3.5 3.5 0 0 1 7 0zM5 20c.8-3.6 3.6-5.5 7-5.5s6.2 1.9 7 5.5",
  attach: "m19 11.5-7.3 7.3a4.5 4.5 0 0 1-6.4-6.4l7.6-7.6a3 3 0 0 1 4.3 4.3l-7.6 7.6a1.5 1.5 0 0 1-2.1-2.1l7-7",
};

export type AnyIcon = keyof typeof PATHS;

export function Icon({ name, size = 16, className }: { name: AnyIcon; size?: number; className?: string }) {
  return (
    <svg
      viewBox="0 0 24 24"
      width={size}
      height={size}
      fill="none"
      stroke="currentColor"
      strokeWidth={1.9}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      className={className}
    >
      <path d={PATHS[name]} />
    </svg>
  );
}
