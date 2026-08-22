import { expect, type Page, test } from "@playwright/test"

async function chooseDataset(page: Page) {
  await page.goto("/create/retrieval")
  await page.getByTestId("dataset-select").click()
  await page.getByRole("option", { name: "E2E Retrieval" }).click()
  await expect(
    page.getByRole("button", { name: /^Select document/ }),
  ).toBeVisible()
}

async function selectEvidenceText(page: Page, selectedText: string) {
  const paragraph = page
    .locator("[data-chunk-id]")
    .filter({ hasText: "The selected answer is Baker." })
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

test("previews and submits one retrieval batch with selected gold evidence", async ({
  page,
}, testInfo) => {
  await chooseDataset(page)
  const documentPicker = page.getByRole("button", { name: /^Select document/ })
  const sourceDocument = page.getByRole("button", {
    name: "E2E Retrieval Source",
  })
  await documentPicker.click()
  if (!(await sourceDocument.isVisible())) {
    await page
      .getByRole("checkbox", { name: "Show documents I've already used" })
      .click()
    await expect(documentPicker).toBeVisible()
    await documentPicker.click()
  }
  await sourceDocument.click()

  await expect(
    page.locator("[data-chunk-id]").filter({ hasText: "Baker" }).first(),
  ).toBeVisible()
  await expect(
    page.getByRole("button", { name: "Search document text" }),
  ).toBeVisible()
  await expect(
    page.getByRole("button", { name: "Copy Markdown" }),
  ).toBeVisible()
  await page.getByRole("button", { name: "Search document text" }).click()
  await page
    .getByRole("searchbox", { name: "Search document text" })
    .fill("Baker")
  await expect(page.locator('[data-search-active="true"]')).toHaveText("Baker")
  await page.screenshot({
    path: testInfo.outputPath("retrieval-create-desktop.png"),
    fullPage: true,
  })
  await page.setViewportSize({ width: 390, height: 844 })
  await page.locator("[data-chunk-id]").first().scrollIntoViewIfNeeded()
  await page.screenshot({
    path: testInfo.outputPath("retrieval-create-mobile.png"),
    fullPage: true,
  })
  await page.setViewportSize({ width: 1280, height: 720 })

  await selectEvidenceText(page, "Baker")
  await expect(page.getByLabel("Expected answer")).toHaveValue("Baker")
  await expect(
    page.locator("blockquote").filter({ hasText: "Baker" }),
  ).toBeVisible()

  await page
    .getByLabel("Question")
    .fill(`Which selected answer appears in the source? ${Date.now()}`)

  await page.getByRole("button", { name: "Add eval item" }).click()
  await page.getByRole("button", { name: "Validate batch" }).click()
  await expect(page.getByText("Server validation completed.")).toBeVisible()
  let submissionRequest: { items?: Array<Record<string, unknown>> } | undefined
  await page.route("**/api/v1/create/retrieval/batch", async (route) => {
    submissionRequest = route.request().postDataJSON() as {
      items?: Array<Record<string, unknown>>
    }
    await route.continue()
  })
  await page.getByRole("button", { name: "Submit" }).click()
  await expect(page.getByText("Submitted 1 item.")).toBeVisible()
  expect(submissionRequest?.items).toHaveLength(1)
  expect(submissionRequest?.items?.[0]).not.toHaveProperty("status")
})

test("recovers a persisted retrieval batch after the browser loses its response", async ({
  page,
}) => {
  await chooseDataset(page)
  const documentPicker = page.getByRole("button", { name: /^Select document/ })
  const sourceDocument = page.getByRole("button", {
    name: "E2E Retrieval Source",
  })
  await documentPicker.click()
  if (!(await sourceDocument.isVisible())) {
    await page
      .getByRole("checkbox", { name: "Show documents I've already used" })
      .click()
    await documentPicker.click()
  }
  await sourceDocument.click()
  await selectEvidenceText(page, "Baker")
  await page
    .getByLabel("Question")
    .fill(`Which answer survives a lost response? ${Date.now()}`)
  await page.getByRole("button", { name: "Add eval item" }).click()
  await page.getByRole("button", { name: "Validate batch" }).click()
  await expect(page.getByText("Server validation completed.")).toBeVisible()

  let persistedBatchRequest = false
  await page.route("**/api/v1/create/retrieval/batch", async (route) => {
    const response = await route.fetch()
    persistedBatchRequest = response.ok()
    await route.abort("failed")
  })
  await page.getByRole("button", { name: "Submit" }).click()

  await expect(
    page.getByText("Recovered submission receipt for 1 item."),
  ).toBeVisible()
  expect(persistedBatchRequest).toBe(true)
})

test("hides adversarial retrieval item controls", async ({ page }) => {
  await chooseDataset(page)

  await expect(page.getByRole("button", { name: "Trap evidence" })).toHaveCount(
    0,
  )
  await page.getByTestId("category-select").click()
  await expect(
    page.getByRole("option", { name: "Adversarial unanswerable" }),
  ).toHaveCount(0)
  await expect(page.getByLabel("Why not answerable")).toHaveCount(0)
})

test("keeps mobile evidence controls keyboard-operable in block mode", async ({
  page,
}, testInfo) => {
  await chooseDataset(page)
  const documentPicker = page.getByRole("button", { name: /^Select document/ })
  const sourceDocument = page.getByRole("button", {
    name: "E2E Retrieval Source",
  })
  await documentPicker.click()
  if (!(await sourceDocument.isVisible())) {
    await page
      .getByRole("checkbox", { name: "Show documents I've already used" })
      .click()
    await documentPicker.click()
  }
  await sourceDocument.click()
  await page.setViewportSize({ width: 390, height: 844 })

  const toolbarButtons = [
    "Undo evidence change",
    "Search document text",
    "Copy Markdown",
    "Increase document text size",
    "Decrease document text size",
  ]
  for (const name of toolbarButtons) {
    const button = page.getByRole("button", { name })
    await expect(button).toBeVisible()
    const box = await button.boundingBox()
    expect(box).not.toBeNull()
    expect(box?.x).toBeGreaterThanOrEqual(0)
    expect((box?.x ?? 0) + (box?.width ?? 0)).toBeLessThanOrEqual(390)
  }

  const searchButton = page.getByRole("button", {
    name: "Search document text",
  })
  await searchButton.focus()
  await page.keyboard.press("Enter")
  await page
    .getByRole("searchbox", { name: "Search document text" })
    .fill("Baker")
  await expect(page.locator('[data-search-active="true"]')).toHaveText("Baker")
  await page.keyboard.press("Escape")

  await selectEvidenceText(page, "Baker")
  await expect(page.getByLabel("Expected answer")).toHaveValue("Baker")
  const undoButton = page.getByRole("button", { name: "Undo evidence change" })
  await expect(undoButton).toBeEnabled()
  await undoButton.focus()
  await page.keyboard.press("Enter")
  await expect(page.getByLabel("Expected answer")).toHaveValue("")

  await page.getByTestId("category-select").click()
  await page.getByRole("option", { name: "Multiple chunks" }).click()
  const evidenceBlocks = page.getByRole("button", {
    name: /^Select evidence block:/,
  })
  await expect(evidenceBlocks).toHaveCount(2)
  await expect(evidenceBlocks.first()).toHaveAttribute("aria-pressed", "false")

  await evidenceBlocks.first().focus()
  await page.keyboard.press("Enter")
  await expect(evidenceBlocks.first()).toHaveAttribute("aria-pressed", "true")
  await evidenceBlocks.nth(1).focus()
  await page.keyboard.press("Space")
  await expect(evidenceBlocks.nth(1)).toHaveAttribute("aria-pressed", "true")

  const confirm = page.getByRole("button", {
    name: "Confirm selected evidence",
  })
  await expect(confirm).toBeVisible()
  await confirm.focus()
  await page.keyboard.press("Enter")
  await expect(confirm).toHaveCount(0)
  await page.screenshot({
    path: testInfo.outputPath("retrieval-create-mobile-keyboard.png"),
    fullPage: true,
  })
})
