import { expect, test } from "@playwright/test"

test("review-relevance is unavailable and links to fact decomposition", async ({
  page,
}) => {
  await page.goto("/review/relevance")
  await expect(
    page.getByText(
      "Retrieval and relevance reviews are temporarily unavailable.",
      { exact: false },
    ),
  ).toBeVisible()
  const link = page.getByRole("link", {
    name: "Review fact decomposition",
    exact: true,
  })
  await expect(link).toHaveAttribute("href", "/review/fact-decomposition")
  await link.click()
  await expect(page).toHaveURL(/fact-decomposition/)
})
