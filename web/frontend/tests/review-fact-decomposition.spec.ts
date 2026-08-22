import { expect, test } from "@playwright/test"

test("submits a fact-decomposition review with fact calls and rubric", async ({
  page,
}) => {
  await page.goto("/review/fact-decomposition")

  await expect(
    page.getByRole("heading", { name: "Fact Decomposition Review" }),
  ).toBeVisible()
  await expect(
    page.getByText("Baker appears in the E2E source. Café appears too."),
  ).toBeVisible()
  const orderedFacts = page.getByTestId("ordered-facts")
  await expect(
    orderedFacts.getByText("Baker appears in the E2E source.", { exact: true }),
  ).toBeVisible()
  await expect(
    orderedFacts.getByText("A distractor answer should not be listed."),
  ).toBeVisible()

  await page.getByTestId("rubric-independently_verifiable").click()
  await page.getByRole("option", { name: "Pass" }).click()
  await page.getByTestId("rubric-noise_removed").click()
  await page.getByRole("option", { name: "Pass" }).click()
  await page.getByTestId("rubric-deduplicated_ordered").click()
  await page.getByRole("option", { name: "Pass" }).click()
  await page.getByLabel("Comments").fill("Facts are ordered and verifiable.")
  await page.getByRole("button", { name: "Submit fact review" }).click()

  await expect(
    page.getByText(/Fact review submitted for item \d+\./),
  ).toBeVisible()
  await expect(
    page.getByRole("button", { name: "Submit fact review" }),
  ).toBeDisabled()
})
