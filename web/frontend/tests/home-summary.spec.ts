import { expect, test } from "@playwright/test"

test("home and my work render backend summary data", async ({ page }) => {
  await page.goto("/")

  await expect(page.getByRole("heading", { name: "Home" })).toBeVisible()
  await expect(page.getByText("Queue unavailable")).not.toBeVisible()
  await expect(page.getByText("Recommended Review")).toBeVisible()
  await expect(page.getByText("Retrieval Reviews")).toBeVisible()
  await expect(page.getByText("Fact Reviews")).toBeVisible()
  await expect(page.getByText("Relevance Reviews")).toBeVisible()

  const content = page.locator("main").last()
  const reviewLink = content.getByRole("link", { name: "Review" })
  await expect(reviewLink).toHaveAttribute(
    "href",
    /\/review(\/(retrieval|fact-decomposition|relevance))?$/,
  )
  await expect(content.getByRole("link", { name: "Create" })).toHaveAttribute(
    "href",
    "/create",
  )

  await page.goto("/my-work")

  await expect(page.getByRole("heading", { name: "My Work" })).toBeVisible()
  await expect(page.getByText("Drafts")).toBeVisible()
  await expect(page.getByText("Submitted")).toBeVisible()
  await expect(page.getByText("Reviewed")).toBeVisible()
  await expect(page.getByText("Authored")).toBeVisible()
})
