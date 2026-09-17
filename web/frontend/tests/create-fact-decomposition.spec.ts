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
      "Save outcome is unknown. Check save status, retry the same save, or download your local copy. A missing receipt does not prove that the save failed.",
    ),
  ).toBeVisible()
  expect(receiptRead).toBe(true)
  await expect(page.getByRole("button", { name: "Save draft" })).toBeDisabled()
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
      "The save receipt does not match this command. Download your local copy; this response cannot confirm your save.",
    ),
  ).toBeVisible()
  await expect(page.getByRole("button", { name: "Save draft" })).toBeDisabled()
  await expect(page.getByRole("button", { name: "Submit" })).toBeDisabled()
})

for (const revision of [1, 2] as const) {
  test(`recovers fact draft revision ${revision} after its response is lost`, async ({
    page,
  }) => {
    await fillRequiredDraft(page, `${Date.now()}`)
    let existingId: number | undefined
    if (revision === 2) {
      await page.getByRole("button", { name: "Save draft" }).click()
      await expect(page.getByText(/Draft saved as item/)).toBeVisible()
      existingId = Number(new URL(page.url()).searchParams.get("item_id"))
      await page
        .getByLabel("Fact 1")
        .fill("Updated fact for the second revision")
    }
    const expectedFact = await page.getByLabel("Fact 1").inputValue()
    let committed:
      | { id: number; item_revision: number; facts: { fact_text: string }[] }
      | undefined
    await page.route("**/api/v1/create/fact-decomp/draft", async (route) => {
      const response = await route.fetch()
      expect(response.ok()).toBe(true)
      committed = await response.json()
      await route.abort("failed")
    })
    await page.getByRole("button", { name: "Save draft" }).click()
    await expect(
      page.getByText(
        new RegExp(`Draft \\d+ was recovered at revision ${revision}\\.`),
      ),
    ).toBeVisible()
    expect(committed?.item_revision).toBe(revision)
    if (existingId !== undefined) expect(committed?.id).toBe(existingId)
    expect(Number(new URL(page.url()).searchParams.get("item_id"))).toBe(
      committed?.id,
    )
    expect(committed?.facts[0].fact_text).toBe(expectedFact)
    await expect(page.getByLabel("Fact 1")).toHaveValue(expectedFact)
    await expect(page.getByRole("button", { name: "Save draft" })).toBeEnabled()
    await expect(page.getByRole("button", { name: "Submit" })).toBeEnabled()
  })
}

test("a delayed committed response locks provenance and resume restores exact ordered content", async ({
  page,
}) => {
  await fillRequiredDraft(page, `delayed-${Date.now()}`)
  await selectEvidenceText(page, "Baker")
  let release: (() => void) | undefined
  const gate = new Promise<void>((resolve) => {
    release = resolve
  })
  let committed = false
  let saved: { id: number; facts: unknown; prompt_text: string } | undefined
  await page.route("**/create/fact-decomp/draft", async (route) => {
    const response = await route.fetch()
    expect(response.ok()).toBe(true)
    saved = await response.json()
    committed = true
    await gate
    await route.fulfill({ response })
  })
  await page.getByRole("button", { name: "Save draft", exact: true }).click()
  await expect.poll(() => committed).toBe(true)
  await expect(page.getByLabel("Source text", { exact: true })).toBeDisabled()
  await expect(
    page.getByRole("button", { name: "Remove evidence" }),
  ).toBeDisabled()
  await expect(
    page.getByRole("button", { name: "Use document text" }),
  ).toBeDisabled()
  await expect(page.locator("#fact-0-polarity")).toBeDisabled()
  await selectEvidenceText(page, "appears")
  await expect(
    page.getByRole("button", { name: "Remove evidence" }),
  ).toHaveCount(1)
  release!()
  await expect(page.getByText(/Draft saved as item/)).toBeVisible()
  await page.getByRole("link", { name: "My Work", exact: true }).click()
  await page
    .getByRole("link", { name: `Resume draft ${saved!.id}`, exact: true })
    .click()
  await page.reload()
  await expect(page.getByLabel("Source text", { exact: true })).toHaveValue(
    saved!.prompt_text,
  )
  await expect(
    page.getByRole("button", { name: "Remove evidence" }),
  ).toHaveCount(1)
  const token = await page.evaluate(() => localStorage.getItem("access_token"))
  const detail = await page.request.get(
    `${process.env.VITE_API_URL}/api/v1/create/fact-decomp/items/${saved!.id}`,
    { headers: { Authorization: `Bearer ${token}` } },
  )
  expect((await detail.json()).facts).toEqual(saved!.facts)
  await page
    .getByLabel("Fact 1", { exact: true })
    .fill("An unsaved edit after resume")
  await expect(page.getByText(/Draft saved as item/)).not.toBeVisible()
})
