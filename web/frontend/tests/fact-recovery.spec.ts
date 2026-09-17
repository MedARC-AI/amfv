import { readFile } from "node:fs/promises"
import { expect, type Page, test } from "@playwright/test"
import {
  factApi,
  factFixture,
  factHeaders,
  loginFactUser,
} from "./utils/factFixtures"

test.use({ storageState: { cookies: [], origins: [] } })
let scenario: ReturnType<typeof factFixture>
test.beforeEach(() => {
  scenario = factFixture({ action: "seed" })
})
test.afterEach(() => {
  factFixture({
    action: "deactivate",
    datasets: scenario.datasets.map((d) => d.id),
  })
})

// Each test still owns which real response is held, aborted, or delivered.
function responseGate() {
  let release!: () => void
  const gate = new Promise<void>((resolve) => {
    release = resolve
  })
  return { gate, release }
}

async function changeDraftElsewhere(
  page: Page,
  id: number,
  sourceText: string,
) {
  const headers = await factHeaders(page)
  const response = await page.request.get(
    `${factApi}/api/v1/create/fact-decomp/items/${id}`,
    { headers },
  )
  expect(response.ok()).toBe(true)
  const detail = await response.json()
  const saved = await page.request.post(
    `${factApi}/api/v1/create/fact-decomp/draft`,
    {
      headers,
      data: {
        dataset_id: detail.dataset_id,
        document_id: detail.document_id,
        item_id: id,
        expected_item_revision: detail.item_revision,
        facts: detail.facts,
        source_text: sourceText,
        request_id: crypto.randomUUID(),
      },
    },
  )
  expect(saved.ok()).toBe(true)
}

async function newDraft(page: Page) {
  await loginFactUser(page, scenario.author)
  await page.getByRole("link", { name: "Create", exact: true }).first().click()
  await page.getByRole("link", { name: /Fact Decomposition/ }).click()
  await page.getByTestId("dataset-select").click()
  await page
    .getByRole("option", { name: scenario.datasets[0].name, exact: true })
    .click()
  await page
    .getByLabel("Source text", { exact: true })
    .fill("Alpha is true. Unicode 😀 Café.")
  await page.getByLabel("Fact 1", { exact: true }).fill("Alpha is true.")
  await page.getByLabel("Fact 2", { exact: true }).fill("Beta is true.")
}
async function saveDraft(page: Page) {
  await page.getByRole("button", { name: "Save draft", exact: true }).click()
  await expect(page.getByText(/Draft saved as item \d+/)).toBeVisible()
  return Number(new URL(page.url()).searchParams.get("item_id"))
}
async function review(page: Page, mode: "authored" | "model") {
  await loginFactUser(page, scenario.reviewer)
  const id = scenario.tasks[mode === "authored" ? 0 : 1]
  await page.goto(`/review/fact-decomposition?task_id=${id}`)
  await page.getByRole("button", { name: "Looks good", exact: true }).click()
  if (mode === "model")
    await page
      .getByLabel("I checked the text for missing worthwhile claims")
      .check()
  else
    await page.getByLabel("Comments", { exact: true }).fill("My local review")
  return id
}

async function cancelExit(page: Page) {
  await page.getByRole("link", { name: "My Work", exact: true }).click()
  await expect(page.getByRole("dialog")).toBeVisible()
  await page.getByRole("button", { name: "Stay", exact: true }).click()
  await expect(page.getByRole("dialog")).not.toBeVisible()
}

test("ordinary author resumes the same draft from My Work, reload, and a fresh context", async ({
  page,
  browser,
}) => {
  await newDraft(page)
  const id = await saveDraft(page)
  const original = await (
    await page.request.get(`${factApi}/api/v1/create/fact-decomp/items/${id}`, {
      headers: await factHeaders(page),
    })
  ).json()
  await page.getByRole("link", { name: "My Work", exact: true }).click()
  await page
    .getByRole("link", { name: `Resume draft ${id}`, exact: true })
    .click()
  await expect(page.getByLabel("Source text", { exact: true })).toHaveValue(
    original.source_text,
  )
  await page.reload()
  await expect(page.getByLabel("Fact 2", { exact: true })).toHaveValue(
    "Beta is true.",
  )
  const fresh = await browser.newContext({
    storageState: await page.context().storageState(),
  })
  const second = await fresh.newPage()
  await second.goto(page.url())
  await expect(second.getByLabel("Fact 1", { exact: true })).toHaveValue(
    "Alpha is true.",
  )
  await expect(second.getByTestId("dataset-select")).toBeDisabled()
  await second.getByLabel("Fact 1", { exact: true }).fill("Updated Alpha")
  await second.getByRole("button", { name: "Save draft", exact: true }).click()
  await expect(
    second.getByText(`Draft saved as item ${id} (revision 2).`),
  ).toBeVisible()
  await fresh.close()
  const outsider = await browser.newContext()
  const outsiderPage = await outsider.newPage()
  await loginFactUser(outsiderPage, scenario.reviewer)
  const forbidden = await outsiderPage.request.get(
    `${factApi}/api/v1/create/fact-decomp/items/${id}`,
    { headers: await factHeaders(outsiderPage) },
  )
  expect(forbidden.status()).toBe(404)
  await outsider.close()
})

test("author can cancel sidebar, browser Back, refresh, and logout without losing edits", async ({
  page,
}, info) => {
  await newDraft(page)
  await cancelExit(page)
  await expect(page.getByLabel("Fact 1", { exact: true })).toHaveValue(
    "Alpha is true.",
  )
  await page.goBack()
  await expect(page.getByRole("dialog")).toBeVisible()
  await page.getByRole("button", { name: "Stay", exact: true }).click()
  const nativeDialog = page.waitForEvent("dialog")
  const cancelledReload = page.reload({ timeout: 2000 }).catch(() => {})
  const warning = await nativeDialog
  expect(warning.type()).toBe("beforeunload")
  await warning.dismiss()
  await cancelledReload
  await expect(page.getByLabel("Source text", { exact: true })).toHaveValue(
    "Alpha is true. Unicode 😀 Café.",
  )
  await page.getByTestId("user-menu").click()
  await page.getByRole("menuitem", { name: "Log Out" }).click()
  await page.getByRole("button", { name: "Stay", exact: true }).click()
  expect(
    await page.evaluate(() => localStorage.getItem("access_token")),
  ).toBeTruthy()
  await page.getByRole("link", { name: "My Work", exact: true }).click()
  await page.setViewportSize({ width: 390, height: 844 })
  await page.screenshot({
    path: info.outputPath("mobile-exit-dialog.png"),
    fullPage: true,
  })
  const dialog = await page.getByRole("dialog").boundingBox()
  expect(dialog!.x).toBeGreaterThanOrEqual(0)
  expect(dialog!.x + dialog!.width).toBeLessThanOrEqual(390)
  await page.getByRole("button", { name: "Discard and leave" }).click()
  await expect(page).toHaveURL(/my-work$/)
})

test("delayed save locks content and exact replay recovers a request lost before commit", async ({
  page,
}, info) => {
  await newDraft(page)
  const commands: unknown[] = []
  const { gate, release } = responseGate()
  let attempt = 0
  await page.route("**/create/fact-decomp/draft", async (route) => {
    commands.push(route.request().postDataJSON())
    if (++attempt === 1) {
      await gate
      await route.abort("failed")
    } else await route.continue()
  })
  await page.getByRole("button", { name: "Save draft", exact: true }).click()
  await expect(page.getByLabel("Source text", { exact: true })).toBeDisabled()
  await expect(page.getByLabel("Fact 1", { exact: true })).toBeDisabled()
  await expect(
    page.getByRole("button", { name: "Add fact", exact: true }),
  ).toBeDisabled()
  await expect(
    page.getByRole("button", { name: "Move fact down" }).first(),
  ).toBeDisabled()
  await expect(page.getByTestId("document-select")).toBeDisabled()
  release()
  await expect(
    page.getByRole("button", { name: "Retry same save" }),
  ).toBeVisible()
  await expect(page.getByText(/Draft saved as item/)).toHaveCount(0)
  await expect(
    page.getByRole("button", { name: "Save draft", exact: true }),
  ).toBeDisabled()
  await expect(
    page.getByRole("button", { name: "Submit", exact: true }),
  ).toBeDisabled()
  await page.getByRole("button", { name: "Check save status" }).click()
  await expect(
    page.getByRole("button", { name: "Retry same save" }),
  ).toBeVisible()
  await page.setViewportSize({ width: 390, height: 844 })
  await page.screenshot({
    path: info.outputPath("mobile-recovery-controls.png"),
    fullPage: true,
  })
  const downloadEvent = page.waitForEvent("download")
  await page.getByRole("button", { name: "Download local copy" }).click()
  const download = await downloadEvent
  await download.saveAs(info.outputPath("local-draft.json"))
  await page.getByRole("button", { name: "Retry same save" }).click()
  await expect(
    page.getByText(/Draft saved as item \d+ \(revision 1\)/),
  ).toBeVisible()
  expect(commands).toHaveLength(2)
  expect(commands[0]).toEqual(commands[1])
  const id = new URL(page.url()).searchParams.get("item_id")
  const stored = await (
    await page.request.get(`${factApi}/api/v1/create/fact-decomp/items/${id}`, {
      headers: await factHeaders(page),
    })
  ).json()
  expect(stored.item_revision).toBe(1)
  expect(stored.source_text).toBe(
    await page.getByLabel("Source text", { exact: true }).inputValue(),
  )
})

test("another tab's revision retains local edits until a confirmed reload", async ({
  page,
  browser,
}) => {
  await newDraft(page)
  const id = await saveDraft(page)
  const other = await browser.newContext({
    storageState: await page.context().storageState(),
  })
  const second = await other.newPage()
  await second.goto(page.url())
  await expect(second.getByLabel("Fact 1", { exact: true })).toHaveValue(
    "Alpha is true.",
  )
  await page.getByLabel("Fact 1", { exact: true }).fill("Local unsaved fact")
  await second
    .getByLabel("Fact 1", { exact: true })
    .fill("Other tab saved fact")
  await second.getByRole("button", { name: "Save draft", exact: true }).click()
  await expect(
    second.getByText(`Draft saved as item ${id} (revision 2).`),
  ).toBeVisible()
  await page.getByRole("button", { name: "Save draft", exact: true }).click()
  await expect(
    page.getByRole("button", { name: "Reload saved version" }),
  ).toBeVisible()
  await expect(page.getByLabel("Fact 1", { exact: true })).toHaveValue(
    "Local unsaved fact",
  )
  await page.getByRole("button", { name: "Reload saved version" }).click()
  await page.getByRole("button", { name: "Stay", exact: true }).click()
  await expect(page.getByLabel("Fact 1", { exact: true })).toHaveValue(
    "Local unsaved fact",
  )
  await page.getByRole("button", { name: "Reload saved version" }).click()
  await page.getByRole("button", { name: "Discard and leave" }).click()
  await expect(page.getByLabel("Fact 1", { exact: true })).toHaveValue(
    "Other tab saved fact",
  )
  await expect(
    page.getByRole("button", { name: "Save draft", exact: true }),
  ).toBeEnabled()
  await other.close()
})

test("lost Submit response recovers the same submitted item and stale URLs remain read-only", async ({
  page,
}) => {
  await newDraft(page)
  const id = await saveDraft(page)
  await page.route("**/create/fact-decomp/submit", async (route) => {
    const response = await route.fetch()
    expect(response.ok()).toBe(true)
    await route.abort("failed")
  })
  await page.getByRole("button", { name: "Submit", exact: true }).click()
  await expect(
    page.getByText(`Submission of item ${id} was recovered.`),
  ).toBeVisible()
  await page.reload()
  await expect(page.getByText(/This saved item is read-only/)).toBeVisible()
  await expect(page.getByLabel("Fact 1", { exact: true })).toBeDisabled()
  await expect(
    page.getByRole("button", { name: "Save draft", exact: true }),
  ).toBeDisabled()
})

for (const mode of ["authored", "model"] as const) {
  test(`${mode} review retries before-commit failure and retains recovery when reads fail`, async ({
    page,
  }) => {
    const id = await review(page, mode)
    let failReads = true
    let failPost = true
    await page.route(`**/review/fact-decomp/${id}**`, async (route) => {
      if (
        (route.request().method() === "POST" && failPost) ||
        (route.request().method() === "GET" && failReads)
      )
        await route.abort("failed")
      else await route.continue()
    })
    await page.getByRole("button", { name: "Save and next" }).click()
    await expect(
      page.getByRole("button", { name: "Check save status" }),
    ).toBeVisible()
    await expect(
      page.getByRole("button", { name: "Looks good", exact: true }),
    ).toBeDisabled()
    await expect(page).toHaveURL(new RegExp(`task_id=${id}$`))
    failReads = false
    await page.getByRole("button", { name: "Check save status" }).click()
    await expect(
      page.getByRole("button", { name: "Retry save", exact: true }),
    ).toBeEnabled()
    await expect(
      page.getByRole("button", { name: "Looks good", exact: true }),
    ).toBeEnabled()
    if (mode === "authored")
      await page
        .getByLabel("Comments", { exact: true })
        .fill("Changed before retry")
    failPost = false
    await page.getByRole("button", { name: "Retry save", exact: true }).click()
    await expect(page).not.toHaveURL(new RegExp(`task_id=${id}$`))
    const stored = await (
      await page.request.get(`${factApi}/api/v1/review/fact-decomp/${id}`, {
        headers: await factHeaders(page),
      })
    ).json()
    expect(stored.existing_review).toBeTruthy()
    if (mode === "authored")
      expect(stored.existing_review.comment).toBe("Changed before retry")
  })

  test(`${mode} review shows a different concurrently saved version without claiming local success`, async ({
    page,
    browser,
  }) => {
    const id = await review(page, mode)
    const other = await browser.newContext({
      storageState: await page.context().storageState(),
    })
    const second = await other.newPage()
    await second.goto(page.url())
    await second.getByRole("button", { name: "Duplicate", exact: true }).click()
    if (mode === "model")
      await second
        .getByLabel("I checked the text for missing worthwhile claims")
        .check()
    else
      await second
        .getByLabel("Comments", { exact: true })
        .fill("Saved elsewhere")
    await second.getByRole("button", { name: "Save and next" }).click()
    await expect(second).not.toHaveURL(new RegExp(`task_id=${id}$`))
    await page.getByRole("button", { name: "Save and next" }).click()
    await expect(
      page.getByText(/A different saved review already exists/),
    ).toBeVisible()
    await expect(
      page.getByRole("button", { name: "Download local copy" }),
    ).toBeVisible()
    await expect(
      page.getByRole("button", { name: "Duplicate", exact: true }),
    ).toHaveAttribute("aria-pressed", "true")
    await expect(
      page.getByRole("button", { name: "Duplicate", exact: true }),
    ).toBeDisabled()
    await expect(page).toHaveURL(new RegExp(`task_id=${id}$`))
    await other.close()
  })
}

test("queue crosses datasets, refreshes exhaustion, preserves Previous, and escapes inaccessible history", async ({
  page,
}, info) => {
  await review(page, "authored")
  await page.getByRole("button", { name: "Save and next" }).click()
  await expect(page).toHaveURL(new RegExp(`task_id=${scenario.tasks[1]}$`))
  await expect(
    page.getByText(scenario.datasets[1].name, { exact: false }),
  ).toBeVisible()
  await page.getByRole("button", { name: "Looks good", exact: true }).click()
  await page
    .getByLabel("I checked the text for missing worthwhile claims")
    .check()
  await page.getByRole("button", { name: "Save and next" }).click()
  await expect(
    page.getByRole("heading", { name: "All caught up" }),
  ).toBeVisible()
  await page.reload()
  await expect(
    page.getByRole("heading", { name: "All caught up" }),
  ).toBeVisible()
  await page.screenshot({
    path: info.outputPath("empty-queue-after-reload.png"),
    fullPage: true,
  })
  await page.getByRole("button", { name: "Previous example" }).click()
  await expect(
    page.getByRole("button", { name: "Looks good", exact: true }),
  ).toBeDisabled()
  await page.getByRole("button", { name: "Previous example" }).click()
  await expect(page).toHaveURL(new RegExp(`task_id=${scenario.tasks[0]}$`))
  factFixture({ action: "deactivate_task", task_id: scenario.tasks[0] })
  await page.reload()
  await expect(page.getByText(/This example is unavailable/)).toBeVisible()
  await page.getByRole("button", { name: "Next example", exact: true }).click()
  await expect(page).toHaveURL(new RegExp(`task_id=${scenario.tasks[1]}$`))
  await page.getByRole("button", { name: "Next example", exact: true }).click()
  await expect(
    page.getByRole("heading", { name: "All caught up" }),
  ).toBeVisible()
  const added = factFixture({ action: "add_task", task_id: scenario.tasks[1] })
  await page.getByRole("button", { name: "Check for new examples" }).click()
  await expect(page).toHaveURL(new RegExp(`task_id=${added.tasks[0]}$`))
  await page.goto("/review/fact-decomposition?complete=true")
  await expect(page).toHaveURL(new RegExp(`task_id=${added.tasks[0]}$`))
})

test("a recovered receipt cannot overwrite a newer committed draft", async ({
  page,
}) => {
  await newDraft(page)
  const headers = await factHeaders(page)
  let itemId = 0
  await page.route("**/create/fact-decomp/draft", async (route) => {
    const response = await route.fetch()
    expect(response.ok()).toBe(true)
    const first = await response.json()
    itemId = first.id
    const newer = await page.request.post(
      `${factApi}/api/v1/create/fact-decomp/draft`,
      {
        headers,
        data: {
          ...route.request().postDataJSON(),
          request_id: crypto.randomUUID(),
          item_id: itemId,
          expected_item_revision: first.item_revision,
          source_text: "A newer saved source",
        },
      },
    )
    expect(newer.ok()).toBe(true)
    await route.abort("failed")
  })
  await page.getByRole("button", { name: "Save draft", exact: true }).click()
  await expect(
    page.getByText(
      /saved item changed in another tab|another tab has since changed the item/,
    ),
  ).toBeVisible()
  await expect(page.getByLabel("Source text", { exact: true })).toHaveValue(
    "Alpha is true. Unicode 😀 Café.",
  )
  await expect(
    page.getByRole("button", { name: "Save draft", exact: true }),
  ).toBeDisabled()
  await page.getByRole("button", { name: "Reload saved version" }).click()
  await page.getByRole("button", { name: "Discard and leave" }).click()
  await expect(page.getByLabel("Source text", { exact: true })).toHaveValue(
    "A newer saved source",
  )
  await expect(
    page.getByText(`Item ${itemId} · Revision 2`, { exact: false }),
  ).toBeVisible()
})

for (const storage of ["malformed", "unavailable"] as const) {
  test(`review handles ${storage} session history`, async ({ page }) => {
    await page.addInitScript(
      ({ userId, failure }) => {
        if (failure === "malformed")
          sessionStorage.setItem(
            `fact-review-history:${userId}`,
            '["bad",-1,null,{},2.5]',
          )
        else {
          const original = Storage.prototype.setItem
          Storage.prototype.setItem = function (key, value) {
            if (this === sessionStorage) throw new Error("storage denied")
            return original.call(this, key, value)
          }
        }
      },
      { userId: scenario.user_id, failure: storage },
    )
    await loginFactUser(page, scenario.reviewer)
    await page.goto(`/review/fact-decomposition?task_id=${scenario.tasks[0]}`)
    await expect(
      page.getByRole("button", { name: "Looks good", exact: true }),
    ).toBeEnabled()
    if (storage === "unavailable")
      await expect(
        page.getByText(/Reload history is unavailable/),
      ).toBeVisible()
    await expect(
      page.getByRole("button", { name: "Previous example" }),
    ).toBeDisabled()
  })
}

test("logout clears saved draft data before another account enters", async ({
  page,
}) => {
  await newDraft(page)
  const id = await saveDraft(page)
  await page.getByTestId("user-menu").click()
  await page.getByRole("menuitem", { name: "Log Out" }).click()
  await expect(page).toHaveURL(/login$/)
  expect(
    await page.evaluate(() => localStorage.getItem("access_token")),
  ).toBeNull()
  await loginFactUser(page, scenario.reviewer)
  await page.getByRole("link", { name: "My Work", exact: true }).click()
  await expect(
    page.getByText("No saved fact drafts on this page."),
  ).toBeVisible()
  await expect(
    page.getByRole("link", { name: `Resume draft ${id}`, exact: true }),
  ).toHaveCount(0)
  await page.getByRole("link", { name: "Review", exact: true }).first().click()
  await page.getByRole("link", { name: /Fact Decomposition/ }).click()
  await expect(
    page.getByRole("button", { name: "Previous example" }),
  ).toBeDisabled()
})

test("discarding a pending save cannot pull the author back after its response arrives", async ({
  page,
}) => {
  await newDraft(page)
  const { gate, release } = responseGate()
  let delivered = false
  await page.route("**/create/fact-decomp/draft", async (route) => {
    const response = await route.fetch()
    expect(response.ok()).toBe(true)
    await gate
    await route.fulfill({ response })
    delivered = true
  })
  await page.getByRole("button", { name: "Save draft", exact: true }).click()
  await page.getByRole("link", { name: "My Work", exact: true }).click()
  await expect(
    page.getByText(/A save is pending or its outcome is unknown/),
  ).toBeVisible()
  await page.getByRole("button", { name: "Discard and leave" }).click()
  await expect(page).toHaveURL(/my-work$/)
  release()
  await expect.poll(() => delivered).toBe(true)
  await expect(page).toHaveURL(/my-work$/)
})

test("an abandoned receipt failure cannot lock a new draft", async ({
  page,
}) => {
  await newDraft(page)
  const { gate, release } = responseGate()
  let reading = false
  let delivered = false
  await page.route("**/create/fact-decomp/draft", (route) =>
    route.abort("failed"),
  )
  await page.route("**/create/fact-decomp/receipts/*", async (route) => {
    reading = true
    await gate
    await route.abort("failed")
    delivered = true
  })
  await page.getByRole("button", { name: "Save draft", exact: true }).click()
  await expect.poll(() => reading).toBe(true)
  await page.getByRole("button", { name: "Create new draft" }).click()
  await page.getByRole("button", { name: "Discard and leave" }).click()
  await page.getByTestId("dataset-select").click()
  await page
    .getByRole("option", { name: scenario.datasets[0].name, exact: true })
    .click()
  await page
    .getByLabel("Source text", { exact: true })
    .fill("Replacement draft")
  release()
  await expect.poll(() => delivered).toBe(true)
  await expect(page.getByLabel("Source text", { exact: true })).toBeEnabled()
  await expect(
    page.getByRole("button", { name: "Save draft", exact: true }),
  ).toBeEnabled()
  await expect(page.getByText(/Save outcome is unknown/)).not.toBeVisible()
})

test("an abandoned review reconciliation cannot lock the previous task", async ({
  page,
}) => {
  await page.addInitScript(
    ({ user, tasks }) =>
      sessionStorage.setItem(
        `fact-review-history:${user}`,
        JSON.stringify([tasks[1], tasks[0]]),
      ),
    { user: scenario.user_id, tasks: scenario.tasks },
  )
  const id = await review(page, "authored")
  const { gate, release } = responseGate()
  let reading = false
  let delivered = false
  await page.route(`**/review/fact-decomp/${id}`, async (route) => {
    if (route.request().method() === "POST") return route.abort("failed")
    reading = true
    await gate
    await route.abort("failed")
    delivered = true
  })
  await page.getByRole("button", { name: "Save and next" }).click()
  await expect.poll(() => reading).toBe(true)
  await page.getByRole("button", { name: "Previous example" }).click()
  await page.getByRole("button", { name: "Discard and leave" }).click()
  const coverage = page.getByLabel(
    "I checked the text for missing worthwhile claims",
  )
  await coverage.check()
  release()
  await expect.poll(() => delivered).toBe(true)
  await expect(coverage).toBeEnabled()
  await expect(coverage).toBeChecked()
  await expect(page.getByText(/Save outcome is unknown/)).not.toBeVisible()
})

test("a delayed queue response cannot discard edits on a different task", async ({
  page,
}) => {
  factFixture({ action: "add_task", task_id: scenario.tasks[0] })
  await page.addInitScript(
    ({ user, tasks }) =>
      sessionStorage.setItem(
        `fact-review-history:${user}`,
        JSON.stringify([tasks[1], tasks[0]]),
      ),
    { user: scenario.user_id, tasks: scenario.tasks },
  )
  await review(page, "authored")
  const { gate, release } = responseGate()
  let reading = false
  let delivered = false
  await page.route("**/review/next?**", async (route) => {
    const response = await route.fetch()
    reading = true
    await gate
    await route.fulfill({ response })
    delivered = true
  })
  await page.getByRole("button", { name: "Save and next" }).click()
  await expect.poll(() => reading).toBe(true)
  await page.getByRole("button", { name: "Previous example" }).click()
  const coverage = page.getByLabel(
    "I checked the text for missing worthwhile claims",
  )
  await coverage.check()
  release()
  await expect.poll(() => delivered).toBe(true)
  await expect(page).toHaveURL(new RegExp(`task_id=${scenario.tasks[1]}$`))
  await expect(coverage).toBeChecked()
})

for (const mode of ["draft", "authored", "model"] as const) {
  for (const phase of ["save", "recovery"] as const) {
    test(`${mode} ${phase} ends an expired session without discard confirmation`, async ({
      page,
    }) => {
      let endpoint: string
      let recoveryEndpoint: string
      if (mode === "draft") {
        await newDraft(page)
        endpoint = "**/create/fact-decomp/draft"
        recoveryEndpoint = "**/create/fact-decomp/receipts/*"
      } else {
        const id = await review(page, mode)
        endpoint = `**/review/fact-decomp/${id}${mode === "model" ? "/model-eval" : ""}`
        recoveryEndpoint = `**/review/fact-decomp/${id}`
      }
      let dialogs = 0
      page.on("dialog", async (dialog) => {
        dialogs += 1
        await dialog.dismiss()
      })
      await page.route(endpoint, async (route) => {
        if (route.request().method() !== "POST") return route.continue()
        if (phase === "save")
          await route.fulfill({
            status: 401,
            json: { detail: "Expired session" },
          })
        else await route.abort("failed")
      })
      if (phase === "recovery")
        await page.route(recoveryEndpoint, async (route) => {
          if (route.request().method() === "GET")
            await route.fulfill({
              status: 401,
              json: { detail: "Expired session" },
            })
          else await route.fallback()
        })
      await page
        .getByRole("button", {
          name: mode === "draft" ? "Save draft" : "Save and next",
          exact: true,
        })
        .click()
      await expect(page).toHaveURL(/login$/)
      expect(
        await page.evaluate(() => localStorage.getItem("access_token")),
      ).toBeNull()
      expect(dialogs).toBe(0)
    })
  }
}

test("a delayed reload cannot overwrite a replacement draft", async ({
  page,
}) => {
  await newDraft(page)
  const id = await saveDraft(page)
  await changeDraftElsewhere(page, id, "Other tab source")
  await page.getByLabel("Fact 1", { exact: true }).fill("Local version")
  await page.getByRole("button", { name: "Save draft", exact: true }).click()
  await expect(
    page.getByRole("button", { name: "Reload saved version" }),
  ).toBeVisible()
  const { gate, release } = responseGate()
  let reading = false
  let delivered = false
  await page.route(`**/create/fact-decomp/items/${id}`, async (route) => {
    const response = await route.fetch()
    reading = true
    await gate
    await route.fulfill({ response })
    delivered = true
  })
  await page.getByRole("button", { name: "Reload saved version" }).click()
  await page.getByRole("button", { name: "Discard and leave" }).click()
  await expect.poll(() => reading).toBe(true)
  await page.getByRole("button", { name: "Create new draft" }).click()
  await page.getByRole("button", { name: "Discard and leave" }).click()
  await page.getByTestId("dataset-select").click()
  await page
    .getByRole("option", { name: scenario.datasets[0].name, exact: true })
    .click()
  await page
    .getByLabel("Source text", { exact: true })
    .fill("Replacement content")
  release()
  await expect.poll(() => delivered).toBe(true)
  await expect(page.getByLabel("Source text", { exact: true })).toHaveValue(
    "Replacement content",
  )
  await expect(
    page.getByRole("button", { name: "Save draft", exact: true }),
  ).toBeEnabled()
  await expect(page).not.toHaveURL(/item_id=/)
})

test("an old account's delayed 401 does not end the replacement session", async ({
  page,
}) => {
  await newDraft(page)
  const { gate, release } = responseGate()
  let delivered = false
  await page.route("**/create/fact-decomp/draft", async (route) => {
    await gate
    await route.fulfill({
      status: 401,
      json: { detail: "Old session expired" },
    })
    delivered = true
  })
  await page.getByRole("button", { name: "Save draft", exact: true }).click()
  await page.getByTestId("user-menu").click()
  await page.getByRole("menuitem", { name: "Log Out" }).click()
  await page.getByRole("button", { name: "Discard and leave" }).click()
  await expect(page).toHaveURL(/login$/)
  // Stay in the SPA so the earlier response reaches the shared interceptor.
  await page.getByTestId("email-input").fill(scenario.reviewer)
  await page.getByTestId("password-input").fill("recovery-password")
  await page.getByRole("button", { name: "Log In", exact: true }).click()
  await page.waitForURL("/")
  const token = await page.evaluate(() => localStorage.getItem("access_token"))
  release()
  await expect.poll(() => delivered).toBe(true)
  await expect(
    page.getByRole("heading", { name: "Home", exact: true }),
  ).toBeVisible()
  expect(await page.evaluate(() => localStorage.getItem("access_token"))).toBe(
    token,
  )
})

test("a second reload cannot erase edits made after the first conflict reload", async ({
  page,
}) => {
  await newDraft(page)
  const id = await saveDraft(page)
  await changeDraftElsewhere(page, id, "New saved source")
  await page.getByLabel("Fact 1", { exact: true }).fill("Local conflict edit")
  await page.getByRole("button", { name: "Save draft", exact: true }).click()
  const reload = page.getByRole("button", { name: "Reload saved version" })
  await expect(reload).toBeVisible()

  const { gate, release } = responseGate()
  let reads = 0
  let reading = false
  let delivered = false
  await page.route(`**/create/fact-decomp/items/${id}`, async (route) => {
    reads += 1
    const response = await route.fetch()
    reading = true
    await gate
    await route.fulfill({ response })
    delivered = true
  })
  await reload.click()
  await page.getByRole("button", { name: "Discard and leave" }).click()
  await expect.poll(() => reading).toBe(true)
  await expect(reload).toBeDisabled()
  expect(reads).toBe(1)
  release()
  await expect.poll(() => delivered).toBe(true)
  await expect(page.getByLabel("Source text", { exact: true })).toHaveValue(
    "New saved source",
  )
  await page
    .getByLabel("Source text", { exact: true })
    .fill("Important new text after reload")
  await expect(page.getByLabel("Source text", { exact: true })).toHaveValue(
    "Important new text after reload",
  )
  expect(reads).toBe(1)
})

test("a held revision-one focus read cannot roll back a successful revision-two save", async ({
  page,
}) => {
  await newDraft(page)
  const id = await saveDraft(page)
  const original = await page
    .getByLabel("Source text", { exact: true })
    .inputValue()
  const { gate, release } = responseGate()
  let held = false
  let reading = false
  let delivered = false
  await page.route(`**/create/fact-decomp/items/${id}`, async (route) => {
    if (held) return route.continue()
    held = true
    const response = await route.fetch()
    reading = true
    await gate
    try {
      await route.fulfill({ response })
    } catch {
      // A successful save may cancel the superseded background read.
    }
    delivered = true
  })
  await page.evaluate(() => {
    window.dispatchEvent(new Event("visibilitychange"))
  })
  await expect.poll(() => reading).toBe(true)
  await page.getByLabel("Source text", { exact: true }).fill("New saved source")
  await page.getByRole("button", { name: "Save draft", exact: true }).click()
  await expect(
    page.getByText(`Draft saved as item ${id} (revision 2).`),
  ).toBeVisible()
  release()
  await expect.poll(() => delivered).toBe(true)
  await expect(page.getByLabel("Source text", { exact: true })).toHaveValue(
    "New saved source",
  )
  await expect(page.getByText(`Item ${id} · Revision 2`)).toBeVisible()
  const stored = await (
    await page.request.get(`${factApi}/api/v1/create/fact-decomp/items/${id}`, {
      headers: await factHeaders(page),
    })
  ).json()
  expect(stored.item_revision).toBe(2)
  expect(stored.source_text).toBe("New saved source")
  expect(stored.source_text).not.toBe(original)
})

for (const outcome of ["success", "error"] as const) {
  for (const change of ["reset", "edit"] as const) {
    test(`a delayed validation ${outcome} is ignored after ${change}`, async ({
      page,
    }) => {
      await newDraft(page)
      const { gate, release } = responseGate()
      let reading = false
      let delivered = false
      await page.route("**/create/fact-decomp/preview", async (route) => {
        const response = await route.fetch()
        expect(response.ok()).toBe(true)
        reading = true
        await gate
        if (outcome === "success") await route.fulfill({ response })
        else await route.abort("failed")
        delivered = true
      })
      await page.getByRole("button", { name: "Validate", exact: true }).click()
      await expect.poll(() => reading).toBe(true)
      if (change === "reset") {
        await page.getByRole("button", { name: "Create new draft" }).click()
        await page.getByRole("button", { name: "Discard and leave" }).click()
        await page.getByTestId("dataset-select").click()
        await page
          .getByRole("option", { name: scenario.datasets[0].name, exact: true })
          .click()
      }
      await page
        .getByLabel("Source text", { exact: true })
        .fill(
          change === "reset"
            ? "Replacement draft"
            : "Edited while validation was pending",
        )
      await expect(
        page.getByRole("button", { name: "Save draft", exact: true }),
      ).toBeEnabled()
      release()
      await expect.poll(() => delivered).toBe(true)
      await expect(page.getByRole("alert")).toHaveCount(0)
      await expect(
        page.getByText("Something went wrong. Please try again."),
      ).toHaveCount(0)
      await expect(page.getByLabel("Source text", { exact: true })).toHaveValue(
        change === "reset"
          ? "Replacement draft"
          : "Edited while validation was pending",
      )
    })
  }
}

test("a held reload for draft A cannot block or overwrite Back navigation to draft B", async ({
  page,
}) => {
  await newDraft(page)
  const bId = await saveDraft(page)
  const headers = await factHeaders(page)
  const aSave = await page.request.post(
    `${factApi}/api/v1/create/fact-decomp/draft`,
    {
      headers,
      data: {
        dataset_id: scenario.datasets[0].id,
        document_id: null,
        source_text: "Draft A original source",
        facts: [
          {
            fact_text: "Alpha is true.",
            polarity: "SHOULD_LIST",
            provenance_spans: [],
          },
          {
            fact_text: "Beta is true.",
            polarity: "SHOULD_NOT_LIST",
            provenance_spans: [],
          },
        ],
        request_id: crypto.randomUUID(),
      },
    },
  )
  expect(aSave.ok()).toBe(true)
  const aId = (await aSave.json()).id as number
  expect(aId).not.toBe(bId)
  await page.getByRole("link", { name: "My Work", exact: true }).click()
  await page
    .getByRole("link", { name: `Resume draft ${aId}`, exact: true })
    .click()
  await expect(page.getByLabel("Source text", { exact: true })).toHaveValue(
    "Draft A original source",
  )

  await changeDraftElsewhere(page, aId, "Draft A saved elsewhere")
  await page
    .getByLabel("Fact 1", { exact: true })
    .fill("Draft A local conflict")
  await page.getByRole("button", { name: "Save draft", exact: true }).click()
  await expect(
    page.getByRole("button", { name: "Reload saved version" }),
  ).toBeVisible()

  const { gate, release } = responseGate()
  let reading = false
  let delivered = false
  await page.route(`**/create/fact-decomp/items/${aId}`, async (route) => {
    const response = await route.fetch()
    reading = true
    await gate
    try {
      await route.fulfill({ response })
    } catch {
      // Navigation may cancel this superseded request.
    }
    delivered = true
  })
  await page.getByRole("button", { name: "Reload saved version" }).click()
  await page.getByRole("button", { name: "Discard and leave" }).click()
  await expect.poll(() => reading).toBe(true)
  await page.evaluate(() => history.go(-2))
  await page.getByRole("button", { name: "Discard and leave" }).click()
  await expect(page).toHaveURL(new RegExp(`item_id=${bId}$`))
  await expect(page.getByLabel("Source text", { exact: true })).toHaveValue(
    "Alpha is true. Unicode 😀 Café.",
  )
  await page
    .getByLabel("Source text", { exact: true })
    .fill("Draft B changed while A reload is pending")
  release()
  await expect.poll(() => delivered).toBe(true)
  await expect(page.getByLabel("Source text", { exact: true })).toHaveValue(
    "Draft B changed while A reload is pending",
  )
  await expect(page).toHaveURL(new RegExp(`item_id=${bId}$`))
})

for (const mode of ["authored", "model"] as const) {
  test(`${mode} reconnect preserves unfinished review until saved version is explicitly loaded`, async ({
    page,
  }) => {
    await loginFactUser(page, scenario.reviewer)
    const id = scenario.tasks[mode === "authored" ? 0 : 1]
    const readUrl = `${factApi}/api/v1/review/fact-decomp/${id}`
    await page.goto(`/review/fact-decomposition?task_id=${id}`)
    const duplicate = page.getByRole("button", {
      name: "Duplicate",
      exact: true,
    })
    await expect(duplicate).toBeEnabled()
    await duplicate.click()
    await expect(duplicate).toHaveAttribute("aria-pressed", "true")
    if (mode === "authored")
      await page
        .getByLabel("Comments", { exact: true })
        .fill("Important unfinished local explanation")

    const headers = await factHeaders(page)
    const current = await (await page.request.get(readUrl, { headers })).json()
    const savedBody =
      mode === "authored"
        ? {
            item_revision: current.item_revision,
            fact_calls: ["SHOULD_LIST"],
            duplicate_flags: [false],
            looks_good: [true],
            values: {
              independently_verifiable: "pass",
              noise_removed: "pass",
              deduplicated_ordered: "pass",
            },
            comments: "Saved in the other tab",
            confidence: "EASY_CALL",
          }
        : {
            item_revision: current.item_revision,
            rubric_id: "importance-v1",
            claim_reviews: [
              {
                position: 0,
                label: "vital",
                issue: null,
                duplicate: false,
                multiple_facts: false,
                looks_good: true,
              },
            ],
            human_claims: [],
            coverage_checked: true,
          }
    const saved = await page.request.post(
      `${readUrl}${mode === "model" ? "/model-eval" : ""}`,
      { headers, data: savedBody },
    )
    expect(saved.status()).toBe(200)

    const refreshed = page.waitForResponse(
      (response) =>
        response.url().endsWith(`/api/v1/review/fact-decomp/${id}`) &&
        response.request().method() === "GET",
    )
    await page.evaluate(() => window.dispatchEvent(new Event("offline")))
    await page.evaluate(() => window.dispatchEvent(new Event("online")))
    expect((await refreshed).ok()).toBe(true)
    const loadSaved = page.getByRole("button", { name: "Load saved review" })
    await expect(loadSaved).toBeVisible()
    await expect(duplicate).toHaveAttribute("aria-pressed", "true")
    if (mode === "authored")
      await expect(page.getByLabel("Comments", { exact: true })).toHaveValue(
        "Important unfinished local explanation",
      )

    const downloadEvent = page.waitForEvent("download")
    await page.getByRole("button", { name: "Download local copy" }).click()
    const download = await downloadEvent
    const localCopy = JSON.parse(await readFile(await download.path(), "utf8"))
    const serialized = JSON.stringify(localCopy)
    if (mode === "authored") {
      expect(localCopy.local.duplicateFlags).toEqual([true])
      expect(serialized).toContain("Important unfinished local explanation")
    } else expect(serialized).toContain('"duplicate":true')

    await loadSaved.click()
    await expect(page.getByRole("dialog")).toBeVisible()
    await page.getByRole("button", { name: "Stay", exact: true }).click()
    await expect(duplicate).toHaveAttribute("aria-pressed", "true")
    if (mode === "authored")
      await expect(page.getByLabel("Comments", { exact: true })).toHaveValue(
        "Important unfinished local explanation",
      )
    await loadSaved.click()
    await page.getByRole("button", { name: "Discard and leave" }).click()
    await expect(
      page.getByRole("button", { name: "Looks good", exact: true }),
    ).toHaveAttribute("aria-pressed", "true")
    await expect(duplicate).toHaveAttribute("aria-pressed", "false")
    await expect(duplicate).toBeDisabled()
    if (mode === "authored")
      await expect(page.getByLabel("Comments", { exact: true })).toHaveValue(
        "Saved in the other tab",
      )
    const persisted = await (
      await page.request.get(readUrl, { headers })
    ).json()
    expect(persisted.allowed_actions).toEqual([])
    expect(factFixture({ action: "task_state", task_id: id })).toEqual({
      count: 1,
      labels_count: 1,
    })
  })
}

test("a loading draft exposes exit confirmation or lets Home open immediately", async ({
  page,
}) => {
  await newDraft(page)
  const aId = await saveDraft(page)
  await page.getByRole("button", { name: "Create new draft" }).click()
  await page.getByTestId("dataset-select").click()
  await page
    .getByRole("option", { name: scenario.datasets[0].name, exact: true })
    .click()
  await page.getByLabel("Source text", { exact: true }).fill("Draft B source")
  await page.getByLabel("Fact 1", { exact: true }).fill("Alpha is true.")
  await page.getByLabel("Fact 2", { exact: true }).fill("Beta is true.")
  const bId = await saveDraft(page)
  expect(bId).not.toBe(aId)
  await page.getByLabel("Fact 1", { exact: true }).fill("Unfinished B edit")

  const { gate, release } = responseGate()
  let reading = false
  let delivered = false
  await page.route(`**/create/fact-decomp/items/${aId}`, async (route) => {
    const response = await route.fetch()
    reading = true
    await gate
    try {
      await route.fulfill({ response })
    } catch {
      // Leaving the loading draft can cancel its pending read.
    }
    delivered = true
  })
  await page.goBack()
  await page.getByRole("button", { name: "Discard and leave" }).click()
  await expect.poll(() => reading).toBe(true)
  await expect(page.getByText("Loading saved draft…")).toBeVisible()
  await page.getByRole("link", { name: "Home", exact: true }).click()
  await expect
    .poll(
      async () =>
        new URL(page.url()).pathname === "/" ||
        (await page.getByRole("dialog").isVisible()),
    )
    .toBe(true)
  if (new URL(page.url()).pathname !== "/") {
    await page.getByRole("button", { name: "Discard and leave" }).click()
  }
  await expect(page).toHaveURL("/")
  release()
  await expect.poll(() => delivered).toBe(true)
  await expect(page).toHaveURL("/")
})

for (const mode of ["authored", "model"] as const) {
  test(`${mode} reconnect read failure retains local review and keeps Save available`, async ({
    page,
  }) => {
    const id = await review(page, mode)
    await page.getByRole("button", { name: "Duplicate", exact: true }).click()
    if (mode === "authored")
      await page
        .getByLabel("Comments", { exact: true })
        .fill("Unfinished while the review read failed")
    const duplicate = page.getByRole("button", {
      name: "Duplicate",
      exact: true,
    })
    await expect(duplicate).toHaveAttribute("aria-pressed", "true")
    let attempted = false
    await page.route(`**/review/fact-decomp/${id}`, async (route) => {
      if (route.request().method() !== "GET") return route.continue()
      attempted = true
      await route.abort("failed")
    })
    await page.evaluate(() => window.dispatchEvent(new Event("offline")))
    await page.evaluate(() => window.dispatchEvent(new Event("online")))
    await expect.poll(() => attempted).toBe(true)
    await expect(
      page.getByText(
        "The latest review could not be checked. Your local edits are retained.",
      ),
    ).toBeVisible()
    await expect(duplicate).toHaveAttribute("aria-pressed", "true")
    if (mode === "authored")
      await expect(page.getByLabel("Comments", { exact: true })).toHaveValue(
        "Unfinished while the review read failed",
      )
    await expect(
      page.getByRole("button", { name: "Save and next" }),
    ).toBeEnabled()
    await expect(
      page.getByRole("button", { name: "Next example", exact: true }),
    ).toHaveCount(0)
  })
}

for (const mode of ["authored", "model"] as const) {
  test(`${mode} recovered submission stays saved after an older reconnect read arrives`, async ({
    page,
  }) => {
    const id = await review(page, mode)
    await cancelExit(page)
    await expect(
      page.getByRole("button", { name: "Looks good", exact: true }),
    ).toHaveAttribute("aria-pressed", "true")
    const readUrl = `${factApi}/api/v1/review/fact-decomp/${id}`
    const { gate, release } = responseGate()
    let held = false
    let oldReadReady = false
    let oldReadDelivered = false
    let committed = false
    await page.route(`**/review/fact-decomp/${id}**`, async (route) => {
      const request = route.request()
      if (request.method() === "GET" && !held) {
        held = true
        const response = await route.fetch()
        const oldPayload = await response.json()
        expect(oldPayload.existing_review).toBeNull()
        oldReadReady = true
        await gate
        try {
          await route.fulfill({ response })
        } catch {
          // Recovery may cancel the stale reconnect read.
        }
        oldReadDelivered = true
        return
      }
      if (request.method() === "POST") {
        const response = await route.fetch()
        expect(response.ok()).toBe(true)
        committed = true
        await route.abort("failed")
        return
      }
      await route.continue()
    })
    await page.evaluate(() => window.dispatchEvent(new Event("offline")))
    await page.evaluate(() => window.dispatchEvent(new Event("online")))
    await expect.poll(() => oldReadReady).toBe(true)
    await page.getByRole("button", { name: "Save and next" }).click()
    await expect.poll(() => committed).toBe(true)
    await expect(page.getByText("Saved review recovered.")).toBeVisible()
    await expect(page).toHaveURL(new RegExp(`task_id=${id}$`))
    const looksGood = page.getByRole("button", {
      name: "Looks good",
      exact: true,
    })
    await expect(looksGood).toHaveAttribute("aria-pressed", "true")
    await expect(looksGood).toBeDisabled()
    if (mode === "authored")
      await expect(page.getByLabel("Comments", { exact: true })).toHaveValue(
        "My local review",
      )
    else
      await expect(
        page.getByLabel("I checked the text for missing worthwhile claims"),
      ).toBeChecked()

    release()
    await expect.poll(() => oldReadDelivered).toBe(true)
    await expect(page.getByText("Saved review recovered.")).toBeVisible()
    await expect(looksGood).toHaveAttribute("aria-pressed", "true")
    await expect(looksGood).toBeDisabled()
    await expect(page).toHaveURL(new RegExp(`task_id=${id}$`))
    if (mode === "authored")
      await expect(page.getByLabel("Comments", { exact: true })).toHaveValue(
        "My local review",
      )
    else
      await expect(
        page.getByLabel("I checked the text for missing worthwhile claims"),
      ).toBeChecked()
    const stored = await (
      await page.request.get(readUrl, { headers: await factHeaders(page) })
    ).json()
    expect(stored.allowed_actions).toEqual([])
    expect(stored.existing_review).toBeTruthy()
    if (mode === "authored")
      expect(stored.existing_review.comment).toBe("My local review")
    else expect(stored.existing_review.coverage_checked).toBe(true)
    expect(factFixture({ action: "task_state", task_id: id })).toEqual({
      count: 1,
      labels_count: 1,
    })
    await page.reload()
    await expect(looksGood).toHaveAttribute("aria-pressed", "true")
    await expect(looksGood).toBeDisabled()
    await expect(
      page.getByRole("button", { name: "Next example", exact: true }),
    ).toBeVisible()
  })
}
