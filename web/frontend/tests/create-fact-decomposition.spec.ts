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

test("creates a fact-decomposition draft with ordered facts and provenance", async ({
  page,
}) => {
  await chooseDataset(page)

  await page.getByRole("button", { name: "Validate" }).click()
  await expect(page.getByText("Validation Error")).toBeVisible()

  await chooseSourceDocument(page)
  await page.getByRole("button", { name: "Use document text" }).click()
  await expect(page.getByLabel("Source text")).toHaveValue(
    /Baker appears in the E2E source\./,
  )

  await page.getByLabel("Fact 1").fill("Baker appears in the E2E source.")
  await page
    .getByLabel("Fact 2")
    .fill("A distractor answer should not be listed.")

  await selectEvidenceText(page, "Baker")
  await expect(
    page.locator("blockquote").filter({ hasText: "Baker" }),
  ).toBeVisible()

  await page.getByRole("button", { name: "Save draft" }).click()
  await expect(page.getByText(/Draft saved as item \d+\./)).toBeVisible()
})
