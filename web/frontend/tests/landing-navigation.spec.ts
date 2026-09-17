import { expect, test } from "@playwright/test"

test("review and create landings expose workflow links and live counters", async ({
  page,
}) => {
  await page.goto("/review")
  const reviewContent = page.locator("main").last()

  await expect(
    page.getByRole("heading", { name: "Review", exact: true }),
  ).toBeVisible()
  await expect(reviewContent.getByText("Fact Decomposition")).toBeVisible()

  await expect(
    reviewContent.getByRole("link", { name: /Fact Decomposition/ }),
  ).toHaveAttribute("href", "/review/fact-decomposition")

  await page.goto("/create")
  const createContent = page.locator("main").last()

  await expect(
    page.getByRole("heading", { name: "Create", exact: true }),
  ).toBeVisible()
  await expect(createContent.getByText("Drafts")).toBeVisible()
  await expect(createContent.getByText("Submitted")).toBeVisible()
  await expect(createContent.getByText("Authored")).toBeVisible()

  await expect(
    createContent.getByRole("link", { name: /Fact Decomposition/ }),
  ).toHaveAttribute("href", "/create/fact-decomposition")
})
