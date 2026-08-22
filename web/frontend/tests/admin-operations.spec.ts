import { expect, test } from "@playwright/test"

test("admin can moderate items, generate tasks, and preview exports", async ({
  page,
}) => {
  await page.goto("/admin")

  await page.getByRole("tab", { name: "Moderation" }).click()
  await expect(
    page.getByRole("heading", { name: "Moderation", exact: true }),
  ).toBeVisible()

  const submittedPrompt = "Which answer should admin moderation approve?"
  const submittedItem = page.locator('[data-testid^="admin-item-"]').filter({
    hasText: submittedPrompt,
  })
  await expect(submittedItem).toBeVisible()
  await submittedItem.getByRole("button", { name: "Approve" }).click()
  await expect(submittedItem).not.toBeVisible()

  await page.getByTestId("admin-moderation-status").click()
  await page.getByRole("option", { name: "Active" }).click()
  await expect(page.getByText(submittedPrompt)).toBeVisible()

  await page.getByRole("tab", { name: "Tasks" }).click()
  await page.getByTestId("admin-task-dataset").click()
  await page.getByRole("option", { name: "E2E Fact Decomposition" }).click()
  await page.getByRole("button", { name: "Generate tasks" }).click()
  await expect(page.getByText("Generation result")).toBeVisible()
  await expect(page.getByText("Existing")).toBeVisible()

  await page.getByRole("tab", { name: "Export" }).click()
  await page.getByTestId("admin-export-dataset").click()
  await page.getByRole("option", { name: "E2E Retrieval" }).click()
  await page.getByRole("button", { name: "Load export page" }).click()
  await expect(
    page.getByRole("heading", { name: "Export page preview", exact: true }),
  ).toBeVisible()
  await expect(
    page.getByText("Which selected answer appears in the E2E document?"),
  ).toBeVisible()
  await expect(
    page.getByRole("button", { name: "Download current export page" }),
  ).toBeVisible()
  await expect(
    page.getByRole("navigation", { name: "Export pages" }),
  ).toBeVisible()
  await expect(page.locator("textarea")).toHaveCount(0)
})
