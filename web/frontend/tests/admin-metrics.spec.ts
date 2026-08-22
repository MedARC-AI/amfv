import { expect, test } from "@playwright/test"

import { firstSuperuser } from "./config.ts"

test("admin can inspect user metrics and agreement sections", async ({
  page,
}) => {
  await page.goto("/admin")

  await page.getByRole("tab", { name: "Metrics" }).click()
  await expect(
    page.getByRole("heading", { name: "Metrics", exact: true }),
  ).toBeVisible()
  await expect(
    page.getByRole("cell", { name: firstSuperuser, exact: true }),
  ).toBeVisible()
  await expect(
    page.getByRole("heading", { name: "User metrics", exact: true }),
  ).toBeVisible()
  await expect(page.getByText(/Showing 1-\d+ of \d+ users/)).toBeVisible()
  await expect(
    page.getByRole("navigation", { name: "User metrics pages" }),
  ).toBeVisible()
  await expect(
    page.getByRole("heading", { name: "Agreement", exact: true }),
  ).toBeVisible()
  await expect(
    page.getByRole("heading", { name: "Inter-user agreement", exact: true }),
  ).toBeVisible()
  await expect(page.getByText("No agreement rows yet.")).toBeVisible()
  await expect(
    page.getByText("No inter-user agreement rows yet."),
  ).toBeVisible()

  await page.getByTestId("admin-metrics-dataset").click()
  await page.getByRole("option", { name: "E2E Retrieval" }).click()
  await expect(
    page.getByRole("cell", { name: firstSuperuser, exact: true }),
  ).toBeVisible()

  await page.getByTestId("admin-metrics-reviewer-kind").click()
  await page.getByRole("option", { name: "Human" }).click()
  await expect(
    page.getByRole("heading", { name: "Agreement", exact: true }),
  ).toBeVisible()

  await page.getByLabel("Minimum overlap").fill("2")
  await expect(page.getByLabel("Minimum overlap")).toHaveValue("2")
})
