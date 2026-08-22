import { expect, test } from "@playwright/test"

test("admin can create datasets and source documents", async ({ page }) => {
  const suffix = Date.now()
  const datasetName = `pw-dataset-${suffix}`
  const datasetDisplayName = `PW Dataset ${suffix}`
  const documentTitle = `PW Source ${suffix}`
  const documentExternalId = `pw-source-${suffix}`
  const documentText = `This source document was created by Playwright ${suffix}. It has enough text for a document preview.`
  const importedExternalId = `pw-imported-source-${suffix}`
  const importedSourceUrl = `https://www.nice.org.uk/advice/example-${suffix}`
  const importArtifact = `${JSON.stringify({
    schema_version: 1,
    source: "nice",
    external_id: importedExternalId,
    title: `PW Imported Source ${suffix}`,
    url: importedSourceUrl,
    content: `Imported source content ${suffix}.`,
    section_count: 1,
    metadata: { ref: `EA${suffix}`, kind: "advice" },
  })}\n`

  await page.goto("/admin")

  await page.getByRole("tab", { name: "Datasets" }).click()
  await page.getByLabel("Display name").fill(datasetDisplayName)
  await page.getByLabel("System name").fill(datasetName)
  await page.getByTestId("dataset-eval-type").click()
  await page.getByRole("option", { name: "Retrieval" }).click()
  await page.getByLabel("Description").fill("Created by the admin E2E suite.")
  await page.getByRole("button", { name: "Create dataset" }).click()

  await expect(page.getByText(datasetDisplayName)).toBeVisible()
  await expect(page.getByText(datasetName)).toBeVisible()

  await page.getByRole("tab", { name: "Documents" }).click()
  await page.getByTestId("admin-document-dataset").click()
  await page.getByRole("option", { name: datasetDisplayName }).click()
  await page.getByLabel("Title").fill(documentTitle)
  await page.getByLabel("External ID").fill(documentExternalId)
  await page.getByLabel("Content").fill(documentText)
  await page.getByRole("button", { name: "Create document" }).click()

  const row = page.getByTestId(`admin-document-${documentExternalId}`)
  await expect(row).toBeVisible()
  await row.getByRole("button", { name: "Preview" }).click()
  await expect(page.getByText(documentText).first()).toBeVisible()
  await expect(page.getByText("Backing text 0")).toBeVisible()

  await row.getByRole("button", { name: "Deactivate" }).click()
  await expect(row.getByText("Inactive").first()).toBeVisible()

  await page.getByTestId("admin-document-import-dataset").click()
  await page.getByRole("option", { name: datasetDisplayName }).click()
  await page.getByLabel("JSONL artifact").setInputFiles({
    name: "documents.jsonl",
    mimeType: "application/x-ndjson",
    buffer: Buffer.from(importArtifact),
  })
  await page.getByRole("button", { name: "Validate artifact" }).click()
  await expect(page.getByText("Dry-run result")).toBeVisible()
  await expect(
    page.getByTestId(`admin-document-${importedExternalId}`),
  ).toHaveCount(0)

  await page.getByLabel("Dry run (no writes)").click()
  await page.getByRole("button", { name: "Import documents" }).click()
  await expect(page.getByText("Import result")).toBeVisible()
  const importedRow = page.getByTestId(`admin-document-${importedExternalId}`)
  await expect(importedRow).toBeVisible()
  await expect(
    importedRow.getByRole("link", { name: "Source URL" }),
  ).toHaveAttribute("href", importedSourceUrl)
})
