import { expect, type Page, test } from "@playwright/test"

async function chooseDataset(page: Page) {
  await page.goto("/create/fact-decomposition")
  await page.getByTestId("dataset-select").click()
  await page.getByRole("option", { name: "E2E Fact Decomposition" }).click()
}

async function chooseSourceDocument(page: Page) {
  await page.getByTestId("document-select").click()
  await page.getByRole("option", { name: "E2E Fact Source" }).click()
  await expect(page.getByText("Baker appears in the E2E source.")).toBeVisible()
}

async function selectEvidenceText(page: Page, selectedText: string) {
  const paragraph = page
    .locator("[data-chunk-id]")
    .filter({ hasText: "Baker appears in the E2E source." })
    .locator("p")
    .first()

  await paragraph.evaluate((node, text) => {
    const walker = document.createTreeWalker(node, NodeFilter.SHOW_TEXT)
    let current = walker.nextNode()

    while (current) {
      const value = current.textContent ?? ""
      const index = value.indexOf(text)
      if (index >= 0) {
        const range = document.createRange()
        range.setStart(current, index)
        range.setEnd(current, index + text.length)
        const selection = window.getSelection()
        selection?.removeAllRanges()
        selection?.addRange(range)
        node.dispatchEvent(new MouseEvent("mouseup", { bubbles: true }))
        return
      }
      current = walker.nextNode()
    }

    throw new Error(`Could not find text: ${text}`)
  }, selectedText)
}

async function fillRequiredDraft(page: Page, suffix: string) {
  await chooseDataset(page)
  await chooseSourceDocument(page)
  await page.getByRole("button", { name: "Use document text" }).click()
  await page
    .getByLabel("Source text")
    .fill(`Baker appears in the E2E source. Fact recovery ${suffix}.`)
  await page.getByLabel("Fact 1").fill(`Baker is saved in ${suffix}.`)
  await page
    .getByLabel("Fact 2")
    .fill("A distractor answer should not be listed.")
}

test("saves one fact-decomposition draft twice, then submits that same item", async ({
  page,
}) => {
  const runSuffix = `${Date.now()}`
  await chooseDataset(page)

  await chooseSourceDocument(page)
  await page.getByRole("button", { name: "Use document text" }).click()
  await expect(page.getByLabel("Source text")).toHaveValue(
    /Baker appears in the E2E source\./,
  )
  await page
    .getByLabel("Source text")
    .fill(
      `Baker appears in the E2E source. Café and emoji 😀 are available for offset checks. Run ${runSuffix}.`,
    )

  const draftRequests: Array<Record<string, unknown>> = []
  let submissionRequest: Record<string, unknown> | undefined
  await page.route("**/api/v1/create/fact-decomp/draft", async (route) => {
    draftRequests.push(
      route.request().postDataJSON() as Record<string, unknown>,
    )
    await route.continue()
  })
  await page.route("**/api/v1/create/fact-decomp/submit", async (route) => {
    submissionRequest = route.request().postDataJSON() as Record<
      string,
      unknown
    >
    await route.continue()
  })

  await page.getByLabel("Fact 1").fill("Baker appears in the E2E source.")
  await page
    .getByLabel("Fact 2")
    .fill("A distractor answer should not be listed.")

  await selectEvidenceText(page, "Baker")
  await expect(
    page.locator("blockquote").filter({ hasText: "Baker" }),
  ).toBeVisible()

  await page.getByRole("button", { name: "Save draft" }).click()
  const firstSave = page.getByText(/Draft saved as item \d+ \(revision 1\)\./)
  await expect(firstSave).toBeVisible()
  const firstItemId = (await firstSave.textContent())?.match(/item (\d+)/)?.[1]
  expect(firstItemId).toBeTruthy()

  await page
    .getByLabel("Fact 1")
    .fill("Baker is retained in the revised source.")
  await page.getByRole("button", { name: "Save draft" }).click()
  await expect(
    page.getByText(
      new RegExp(`Draft saved as item ${firstItemId} \\(revision 2\\)\\.`),
    ),
  ).toBeVisible()

  await page.getByRole("button", { name: "Submit" }).click()
  await expect(
    page.getByText(new RegExp(`Submitted item ${firstItemId}\\.`)),
  ).toBeVisible()
  expect(draftRequests).toHaveLength(2)
  for (const request of draftRequests) {
    expect(request).not.toHaveProperty("status")
    expect(request).toHaveProperty("request_id", expect.any(String))
  }
  expect(submissionRequest).not.toHaveProperty("status")
  expect(submissionRequest).toHaveProperty("request_id", expect.any(String))
  expect(
    new Set([
      ...draftRequests.map((request) => request.request_id),
      submissionRequest?.request_id,
    ]).size,
  ).toBe(3)
})

test("keeps provenance on the selected fact after move and preceding-row removal", async ({
  page,
}) => {
  await chooseDataset(page)
  await chooseSourceDocument(page)
  await page.getByRole("button", { name: "Use document text" }).click()

  await page.getByLabel("Fact 1").fill("Remove this preceding fact.")
  await page
    .getByLabel("Fact 2")
    .fill("Baker does not appear in the E2E source.")
  await page.getByRole("button", { name: "Add fact" }).click()
  await page.getByLabel("Fact 3").fill("Baker appears in the E2E source.")

  await page.getByTestId("fact-select").click()
  await page
    .getByRole("option", {
      name: "Fact 2: Baker does not appear in the E2E source.",
    })
    .click()

  const intendedRow = page.getByLabel("Fact 2").locator("xpath=ancestor::li")
  await intendedRow.getByRole("button", { name: "Move fact down" }).click()

  const precedingRow = page.getByLabel("Fact 1").locator("xpath=ancestor::li")
  await precedingRow.getByRole("button", { name: "Remove fact" }).click()

  await expect(page.getByTestId("fact-select")).toContainText(
    "Fact 2: Baker does not appear in the E2E source.",
  )
  await selectEvidenceText(page, "Baker")

  let submissionRequest: Record<string, unknown> | undefined
  await page.route("**/api/v1/create/fact-decomp/submit", async (route) => {
    submissionRequest = route.request().postDataJSON() as Record<
      string,
      unknown
    >
    await route.continue()
  })
  await page.getByRole("button", { name: "Validate" }).click()
  await expect(page.getByText("Validation passed")).toBeVisible()
  await page.getByRole("button", { name: "Save draft" }).click()
  await expect(
    page.getByText(/Draft saved as item \d+ \(revision 1\)\./),
  ).toBeVisible()
  await page.getByRole("button", { name: "Submit" }).click()
  await expect(page.getByText(/Submitted item \d+\./)).toBeVisible()

  const submittedFacts = submissionRequest?.facts as
    | Array<Record<string, unknown>>
    | undefined
  expect(submittedFacts?.[1]).toMatchObject({
    fact_text: "Baker does not appear in the E2E source.",
    provenance_spans: [expect.objectContaining({ text: "Baker" })],
  })
  expect(submittedFacts?.[0]).toMatchObject({
    fact_text: "Baker appears in the E2E source.",
    provenance_spans: [],
  })
})

test("does not report a first fact draft as saved when the request fails before commit", async ({
  page,
}) => {
  await fillRequiredDraft(page, `${Date.now()}`)
  await page.route("**/api/v1/create/fact-decomp/draft", async (route) => {
    await route.abort("failed")
  })

  await page.getByRole("button", { name: "Save draft" }).click()

  await expect(
    page.getByText(
      "Draft save outcome is unknown. It was not retried automatically; do not assume the draft was saved.",
    ),
  ).toBeVisible()
  await expect(page.getByText(/Draft saved as item \d+/)).toHaveCount(0)
  await expect(page.getByRole("button", { name: "Save draft" })).toBeDisabled()
  await expect(page.getByRole("button", { name: "Submit" })).toBeDisabled()
})

test("checks the receipt after a server failure before reporting a fact draft outcome", async ({
  page,
}) => {
  await fillRequiredDraft(page, `${Date.now()}`)
  let receiptRead = false
  await page.route("**/api/v1/create/fact-decomp/draft", async (route) => {
    await route.fulfill({
      body: JSON.stringify({ detail: "untrusted server detail" }),
      contentType: "application/json",
      status: 500,
    })
  })
  await page.route("**/api/v1/create/fact-decomp/receipts/*", async (route) => {
    receiptRead = true
    await route.continue()
  })

  await page.getByRole("button", { name: "Save draft" }).click()

  await expect(
    page.getByText(
      "Draft save outcome is unknown. It was not retried automatically; do not assume the draft was saved.",
    ),
  ).toBeVisible()
  expect(receiptRead).toBe(true)
  await expect(page.getByRole("button", { name: "Save draft" })).toBeDisabled()
})

test("recovers a first fact save after its response is lost", async ({
  page,
}) => {
  await fillRequiredDraft(page, `${Date.now()}`)
  let persistedResponse: { id?: number; item_revision?: number } | undefined
  await page.route("**/api/v1/create/fact-decomp/draft", async (route) => {
    const response = await route.fetch()
    expect(response.ok()).toBe(true)
    persistedResponse = await response.json()
    await route.abort("failed")
  })

  await page.getByRole("button", { name: "Save draft" }).click()

  await expect(
    page.getByText(/Draft \d+ was recovered at revision 1\./),
  ).toBeVisible()
  expect(persistedResponse).toMatchObject({
    id: expect.any(Number),
    item_revision: 1,
  })
  await expect(
    page.getByText(
      new RegExp(
        `Draft ${persistedResponse?.id} was recovered at revision ${persistedResponse?.item_revision}\\.`,
      ),
    ),
  ).toBeVisible()
  await expect(page.getByRole("button", { name: "Save draft" })).toBeEnabled()
  await expect(page.getByRole("button", { name: "Submit" })).toBeEnabled()
})

test("keeps an uncertain fact draft locked when its receipt does not match", async ({
  page,
}) => {
  await fillRequiredDraft(page, `${Date.now()}`)
  await page.route("**/api/v1/create/fact-decomp/draft", async (route) => {
    const response = await route.fetch()
    expect(response.ok()).toBe(true)
    await route.abort("failed")
  })
  await page.route("**/api/v1/create/fact-decomp/receipts/*", async (route) => {
    const response = await route.fetch()
    expect(response.ok()).toBe(true)
    const receipt = await response.json()
    await route.fulfill({
      response,
      json: { ...receipt, request_id: "mismatched-receipt" },
    })
  })

  await page.getByRole("button", { name: "Save draft" }).click()

  await expect(
    page.getByText(
      "Draft save outcome is unknown. It was not retried automatically; do not assume the draft was saved.",
    ),
  ).toBeVisible()
  await expect(page.getByRole("button", { name: "Save draft" })).toBeDisabled()
  await expect(page.getByRole("button", { name: "Submit" })).toBeDisabled()
})

test("recovers a later fact save after its response is lost", async ({
  page,
}) => {
  const suffix = `${Date.now()}`
  await fillRequiredDraft(page, suffix)
  await page.getByRole("button", { name: "Save draft" }).click()
  const firstSave = page.getByText(/Draft saved as item \d+ \(revision 1\)\./)
  await expect(firstSave).toBeVisible()
  const firstItemId = (await firstSave.textContent())?.match(/item (\d+)/)?.[1]
  expect(firstItemId).toBeTruthy()

  const revisedFact = `Baker is the exact later save ${suffix}.`
  await page.getByLabel("Fact 1").fill(revisedFact)

  let persistedResponse:
    | {
        facts?: Array<{ fact_text?: string }>
        item_revision?: number
        prompt_text?: string
      }
    | undefined
  await page.route("**/api/v1/create/fact-decomp/draft", async (route) => {
    const response = await route.fetch()
    expect(response.ok()).toBe(true)
    persistedResponse = await response.json()
    await route.abort("failed")
  })

  await page.getByRole("button", { name: "Save draft" }).click()

  await expect(
    page.getByText(
      new RegExp(
        `Draft ${firstItemId ?? "\\d+"} was recovered at revision 2\\.`,
      ),
    ),
  ).toBeVisible()
  await expect(page.getByRole("button", { name: "Save draft" })).toBeEnabled()
  await expect(page.getByRole("button", { name: "Submit" })).toBeEnabled()
  expect(persistedResponse).toMatchObject({
    item_revision: 2,
    prompt_text: `Baker appears in the E2E source. Fact recovery ${suffix}.`,
  })
  expect(persistedResponse?.facts).toEqual(
    expect.arrayContaining([
      expect.objectContaining({ fact_text: revisedFact }),
    ]),
  )
})
