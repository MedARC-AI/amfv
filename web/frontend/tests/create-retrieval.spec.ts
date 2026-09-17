import { expect, test } from "@playwright/test"

test("create-retrieval is unavailable and links to fact decomposition", async ({
  page,
}) => {
  await page.goto("/create/retrieval")
  await expect(
    page.getByText("Retrieval creation is temporarily unavailable.", {
      exact: false,
    }),
  ).toBeVisible()
  const link = page.getByRole("link", {
    name: "Create fact decomposition",
    exact: true,
  })
  await expect(link).toHaveAttribute("href", "/create/fact-decomposition")
  await link.click()
  await expect(page).toHaveURL(/fact-decomposition/)
})
