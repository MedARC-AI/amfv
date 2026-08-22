import { expect, test } from "@playwright/test"

test("review and create landings expose workflow links and live counters", async ({
  page,
}) => {
  await page.goto("/review")
  const reviewContent = page.locator("main").last()

  await expect(
    reviewContent.getByRole("heading", { name: "Review" }),
  ).toBeVisible()
  await expect(reviewContent.getByText("Retrieval")).toBeVisible()
  await expect(reviewContent.getByText("Fact Decomposition")).toBeVisible()
  await expect(reviewContent.getByText("Relevance")).toBeVisible()
  await expect(
    reviewContent.getByRole("link", { name: /Retrieval/ }),
  ).toHaveAttribute("href", "/review/retrieval")
  await expect(
    reviewContent.getByRole("link", { name: /Fact Decomposition/ }),
  ).toHaveAttribute("href", "/review/fact-decomposition")
  await expect(
    reviewContent.getByRole("link", { name: /Relevance/ }),
  ).toHaveAttribute("href", "/review/relevance")

  await page.goto("/create")
  const createContent = page.locator("main").last()

  await expect(
    createContent.getByRole("heading", { name: "Create" }),
  ).toBeVisible()
  await expect(createContent.getByText("Drafts")).toBeVisible()
  await expect(createContent.getByText("Submitted")).toBeVisible()
  await expect(createContent.getByText("Authored")).toBeVisible()
  await expect(
    createContent.getByRole("link", { name: /Retrieval/ }),
  ).toHaveAttribute("href", "/create/retrieval")
  await expect(
    createContent.getByRole("link", { name: /Fact Decomposition/ }),
  ).toHaveAttribute("href", "/create/fact-decomposition")
})
