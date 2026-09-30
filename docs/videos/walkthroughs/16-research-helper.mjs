// Research sessions and the research helper (16-research-helper.md). Two live
// conversations: a Research session asked about methods, then a Data
// extraction session whose agent asks the research helper. Shot 3.2 is the
// approval card, before and after Send (targets `card` and `sent`).

const RESEARCH_Q = "What R packages are commonly used for mixed models with repeated daily measures?";
const HELPER_Q = "Before we query anything: what are the standard PHQ-9 severity cutoffs? Please check with the research helper.";

const dialog = (page) => page.getByRole("dialog");
const composer = (page) => page.locator("main").getByRole("textbox", { name: "Your question or instruction" });
const busy = (page) => page.evaluate(async (id) => (await fetch("/api/conversations").then((r) => r.json())).find((c) => c.id === id)?.busy ?? false, page.url().split("/").at(-1));
const answer = (page) => page.locator("main section").filter({ has: page.getByText("Answer", { exact: true }) }).last();
const sendBtn = (page) => page.getByRole("button", { name: "Send to the research helper" });
const card = (page) => page.locator("main").getByText("The agent wants to look something up online").last().locator("xpath=ancestor::div[contains(@class,'border-l')][1]");
const sent = (page) => page.locator("main").getByText("What will be sent", { exact: true }).last().locator("xpath=following-sibling::*[1]");
const helperAnswer = (page) => page.locator("main details").filter({ hasText: "The helper's answer" }).last();
const waitIdle = async (page, pause) => {
  await pause(3);
  while (await busy(page)) await pause(2);
  await pause(1.5);
};

async function newConversation(page, { until, mark, pause }, mode) {
  await page.getByRole("button", { name: "New conversation" }).first().click();
  await dialog(page).waitFor();
  await pause(0.5);
  await mark("modes");
  const m = dialog(page).getByRole("button", { name: new RegExp(`^${mode}`) });
  return m;
}

export default {
  mask: "paths",
  shots: {
    "2.1": {
      async prepare(page, { BASE, pause }) {
        await page.goto(BASE + "/workspace");
        await page.getByRole("button", { name: "New conversation" }).first().waitFor();
        await pause(1);
      },
      async act(page, ctx) {
        const { until, mark, pause } = ctx;
        await until("Press", -0.2);
        const research = await newConversation(page, ctx, "Research");
        await until("choose Research", -0.2);
        await research.hover();
        await pause(0.3);
        await research.click();
        await mark("research");
        await until("The note says", 0);
        await mark("note");
        await until("Then press Start", 0);
        const start = dialog(page).getByRole("button", { name: /^Start/ });
        await start.hover();
        await mark("start");
        await pause(0.5);
        await start.click();
        await page.waitForURL(/\/workspace\/c_/);
        await until("Then press Start", 2);
      },
      targets: {
        modes: (page) => dialog(page).getByRole("button", { name: /^Analysis/ }).locator("xpath=.."),
        research: (page) => dialog(page).getByRole("button", { name: /^Research/ }),
        note: (page) => dialog(page).getByText(/Research sessions can use the web/),
        start: (page) => dialog(page).getByRole("button", { name: /^Start/ }),
      },
    },
    "2.2": {
      async act(page, { until, mark, pause, fastForward }) {
        await composer(page).click();
        await page.keyboard.type(RESEARCH_Q, { delay: 22 });
        await mark("composer");
        await pause(0.4);
        await page.keyboard.press("Enter");
        await fastForward(() => waitIdle(page, pause), "and answers");
        await answer(page).first().evaluate((n) => n.scrollIntoView({ block: "start" }));
        await page.mouse.wheel(0, -80);
        await pause(0.8);
        await mark("answer");
        await until("with its sources", 1.2);
      },
      targets: { composer, answer: (page) => answer(page).first() },
    },
    "2.3": {
      async act(page, { until, mark, pause }) {
        await mark("answer");
        await pause(1.5);
        await page.mouse.move(940, 600);
        await page.mouse.wheel(0, 420);
        await pause(1);
        await mark("answer");
        await until("nothing moves the other way", 1);
      },
      targets: { answer: (page) => answer(page).first() },
    },
    "3.1": {
      async prepare(page, { BASE, pause }) {
        if (!page.url().includes("/workspace")) await page.goto(BASE + "/workspace");
        await page.getByRole("button", { name: "New conversation" }).first().waitFor();
        await pause(0.5);
      },
      async act(page, ctx) {
        const { until, mark, pause, fastForward } = ctx;
        const extraction = await newConversation(page, ctx, "Data extraction");
        await pause(0.4);
        await extraction.hover();
        await pause(0.3);
        await extraction.click();
        await pause(0.3);
        await mark("note");
        await until("it asks the research helper", 0);
        await dialog(page).getByRole("button", { name: /^Start/ }).click();
        await page.waitForURL(/\/workspace\/c_/);
        await composer(page).waitFor();
        await until("Here, in a Data extraction session", -0.2);
        await composer(page).click();
        await page.keyboard.type(HELPER_Q, { delay: 18 });
        await mark("composer");
        await pause(0.3);
        await page.keyboard.press("Enter");
        // The agent reads the lab's guide and writes the question: sped up,
        // so the card is up when shot 3.2 starts.
        await fastForward(async () => {
          await sendBtn(page).waitFor({ timeout: 600_000 });
          await pause(1);
        }, "severity cutoffs");
        await card(page).evaluate((n) => n.scrollIntoView({ block: "center" }));
        await page.mouse.move(1900, 1070);
        await until("severity cutoffs", 1);
      },
      targets: {
        modes: (page) => dialog(page).getByRole("button", { name: /^Analysis/ }).locator("xpath=.."),
        note: (page) => dialog(page).getByText(/Data sessions can query/),
        composer,
      },
    },
    "3.2": {
      async act(page, { until, mark, pause, fastForward }) {
        await sendBtn(page).waitFor({ timeout: 600_000 });
        await card(page).evaluate((n) => n.scrollIntoView({ block: "center" }));
        await pause(0.3);
        await mark("card", "sent");
        await until("What will be sent", 0);
        await mark("sent");
        await until("You can edit it", 0);
        await mark("edit");
        await until("press Don't send", 0);
        await page.getByRole("button", { name: "Don't send" }).hover();
        await mark("decline");
        await until("Press Send to the research helper", 0);
        await sendBtn(page).hover();
        await mark("send");
        await until("The helper looks it up", -0.3);
        await sendBtn(page).click();
        await fastForward(async () => {
          await helperAnswer(page).waitFor({ timeout: 600_000 });
          // Let the agent finish its turn too, so the screen stays still.
          await waitIdle(page, pause);
          // Once the agent has answered, its steps fold into "How this answer
          // was made": open it, so the card (now a record) shows again.
          if (!(await card(page).isVisible().catch(() => false))) {
            await page.locator("main button").filter({ hasText: "How this answer was made" }).last().click().catch(() => {});
            await pause(0.8);
          }
          await card(page).evaluate((n) => n.scrollIntoView({ block: "center" }));
          await pause(0.4);
          await helperAnswer(page).locator("summary").click().catch(() => {});
          await pause(0.6);
          await card(page).evaluate((n) => n.scrollIntoView({ block: "center" }));
          await pause(0.6);
        }, "on the web, answers");
        await mark("card", "answer");
        await pause(0.3);
        await mark("card", "answer");
        await until("is then deleted", 1.2);
      },
      targets: {
        card,
        sent,
        edit: (page) => card(page).locator("textarea").first(),
        decline: (page) => page.getByRole("button", { name: "Don't send" }),
        send: sendBtn,
        answer: helperAnswer,
      },
    },
    "3.3": {
      async act(page, { until, mark, pause, fastForward }) {
        await fastForward(() => waitIdle(page, pause), "web content");
        await answer(page).first().evaluate((n) => n.scrollIntoView({ behavior: "smooth", block: "center" }));
        await pause(0.8);
        await mark("answer");
        await until("aren't counted as traced", 1.2);
      },
      targets: { answer: (page) => answer(page).first() },
    },
  },
};
