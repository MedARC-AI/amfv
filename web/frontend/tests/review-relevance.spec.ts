import { expect, test } from "@playwright/test"

test("submits a relevance review for a retrieved passage", async ({ page }) => {
  await page.goto("/review/relevance")

  await expect(
    page.getByRole("heading", { name: "Relevance Review" }),
  ).toBeVisible()
  await expect(
    page.getByText("Which selected answer appears in the E2E document?"),
  ).toBeVisible()
  await expect(page.getByText("The selected answer is Baker.")).toBeVisible()

  await page.getByTestId("grade-select").click()
  await page.getByRole("option", { name: "Highly relevant" }).click()
  await page.getByTestId("confidence-select").click()
  await page.getByRole("option", { name: "Easy call" }).click()
  await page.getByRole("button", { name: "Submit relevance" }).click()

  await expect(
    page.getByText(/Relevance review submitted for item \d+\./),
  ).toBeVisible()
  await expect(
    page.getByRole("button", { name: "Submit relevance" }),
  ).toBeDisabled()
})
