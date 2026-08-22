import { expect, test } from "@playwright/test"

test("submits a retrieval QA rubric review", async ({ page }, testInfo) => {
  await page.goto("/review/retrieval")

  await expect(
    page.getByRole("heading", { name: "Retrieval Review" }),
  ).toBeVisible()
  const claimReview = page.getByRole("button", {
    name: "Claim retrieval review",
  })
  await expect(claimReview).toBeVisible()
  await claimReview.click()
  await expect(
    page.getByText("Which selected answer appears in the E2E document?"),
  ).toBeVisible()
  await expect(page.getByText("Expected answer", { exact: true })).toBeVisible()
  await expect(
    page.getByRole("button", { name: "Baker", exact: true }),
  ).toBeVisible()

  await expect(
    page.locator("blockquote").filter({ hasText: "Baker" }),
  ).toBeVisible()
  await expect(
    page.getByRole("button", { name: "Submit review" }),
  ).toBeDisabled()
  await expect(
    page.getByRole("button", { name: "Search document text" }),
  ).toBeVisible()
  await expect(
    page.getByRole("button", { name: "Copy Markdown" }),
  ).toBeVisible()
  await expect(
    page.getByRole("button", { name: "Increase document text size" }),
  ).toBeVisible()
  await expect(
    page.getByRole("button", { name: "Decrease document text size" }),
  ).toBeVisible()
  await expect(page.getByRole("radio", { name: "Yes" })).toBeEnabled()
  await expect(page.getByRole("radio", { name: "No" })).toBeEnabled()

  await page.getByRole("button", { name: "Search document text" }).click()
  await page
    .getByRole("searchbox", { name: "Search document text" })
    .fill("Baker")
  await expect(page.locator('[data-search-active="true"]')).toHaveText("Baker")
  await page
    .getByRole("button", { name: "Increase document text size" })
    .click()
  await expect(page.locator("[data-chunk-id]").first()).toHaveClass(/text-lg/)
  await page.screenshot({
    path: testInfo.outputPath("retrieval-viewer-desktop.png"),
    fullPage: true,
  })
  await page.setViewportSize({ width: 390, height: 844 })
  await expect(page.getByText("E2E Retrieval Source")).toBeVisible()
  await page.screenshot({
    path: testInfo.outputPath("retrieval-viewer-mobile.png"),
    fullPage: true,
  })

  await page
    .getByTitle(
      "4 — Clear, unambiguous, answerable from the corpus, and meaningful",
    )
    .click()
  await page.getByTitle("4 — Relevant, sufficient, and concise").click()
  await expect(
    page.getByRole("radio", { name: "Answer correctness: 4", checked: true }),
  ).toBeVisible()
  await page
    .getByTitle(
      "4 — Every substantive claim is supported by the selected evidence",
    )
    .click()
  await expect(
    page.getByRole("radio", { name: "Yes", checked: true }),
  ).toBeVisible()
  await page.getByLabel("Notes").fill("Evidence matches the answer.")
  await page.getByRole("button", { name: "Submit review" }).click()

  await expect(page.getByText(/Review submitted for item \d+\./)).toBeVisible()
  await expect(
    page.getByRole("button", { name: "Submit review" }),
  ).toBeDisabled()
})
