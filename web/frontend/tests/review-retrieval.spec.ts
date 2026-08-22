import { expect, test } from "@playwright/test"

test("submits a retrieval QA rubric review", async ({ page }) => {
  await page.goto("/review/retrieval")

  await expect(
    page.getByRole("heading", { name: "Retrieval Review" }),
  ).toBeVisible()
  await expect(
    page.getByText("Which selected answer appears in the E2E document?"),
  ).toBeVisible()
  await expect(page.getByText("Expected answer")).toBeVisible()
  await expect(page.getByText("Baker")).toBeVisible()

  await expect(
    page.locator("blockquote").filter({ hasText: "Baker" }),
  ).toBeVisible()
  await expect(
    page.getByRole("button", { name: "Submit review" }),
  ).toBeDisabled()

  await page.getByRole("radio", { name: "Question validity: 4" }).click()
  await page.getByRole("radio", { name: "Evidence quality: 4" }).click()
  await expect(
    page.getByRole("radio", { name: "Answer correctness: 4", checked: true }),
  ).toBeVisible()
  await page.getByRole("radio", { name: "Answer grounding: 4" }).click()
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
