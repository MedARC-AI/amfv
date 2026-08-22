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
    await expect(documentPicker).toBeVisible()
    await documentPicker.click()
  }
  await sourceDocument.click()

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
  await page.getByRole("button", { name: "Submit" }).click()
  await expect(page.getByText("Submitted 1 item.")).toBeVisible()
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
