import { createHash, randomUUID } from "node:crypto"
import { expect, test } from "@playwright/test"

import { factFixture } from "./utils/factFixtures.ts"

const createdDatasets: number[] = []

test.afterEach(() => {
  if (createdDatasets.length > 0) {
    factFixture({ action: "deactivate", datasets: createdDatasets.splice(0) })
  }
})

test("admin can read a reviewer's grades on the Grades tab", async ({
  page,
}) => {
  // An import, a review, and three admin views in one run; give it room.
  test.slow()

  // Import one model-correction row through the ingest endpoint, the way a
  // real decomposition run arrives, so the export can serve it back.
  await page.goto("/")
  const accessToken = await page.evaluate(() =>
    localStorage.getItem("access_token"),
  )
  expect(accessToken).not.toBeNull()
  const headers = { Authorization: `Bearer ${accessToken}` }
  const apiBase = process.env.VITE_API_URL ?? "http://127.0.0.1:8000"
  const displayName = `Grades ${randomUUID()}`
  const created = await page.request.post(`${apiBase}/api/v1/admin/datasets`, {
    headers,
    data: {
      name: displayName,
      display_name: displayName,
      eval_type: "FACT_DECOMP",
    },
  })
  expect(created.ok()).toBe(true)
  const dataset = (await created.json()) as { id: number }
  createdDatasets.push(dataset.id)
  const caseId = `grades-${randomUUID()}`
  const armId = "e2e-arm"
  const passage = "Dehydration activates RAAS"
  const row = {
    schema_version: 2,
    eval_type: "FACT_DECOMP",
    external_id: createHash("sha256")
      .update(`${caseId}\0${armId}`)
      .digest("hex"),
    case_id: caseId,
    source: "LLM",
    user_prompt: "Why does filtration pressure hold?",
    assistant_response: `${passage}, and filtration pressure holds.`,
    arm_id: armId,
    generator: {
      model_id: "openai/gpt-oss-20b",
      model_revision: null,
      prompt_text: "Test",
      pydantic_ai_version: "2.33.0",
      generation: {},
    },
    claims: [
      {
        claim: "Dehydration activates RAAS.",
        spans: [{ start: 0, end: passage.length, text: passage }],
        label: "vital",
      },
    ],
  }
  const imported = await page.request.post(`${apiBase}/api/v1/admin/ingest`, {
    headers,
    multipart: {
      dataset_id: String(dataset.id),
      file: {
        name: "fact-decomposition.jsonl",
        mimeType: "application/x-ndjson",
        buffer: Buffer.from(`${JSON.stringify(row)}\n`),
      },
    },
  })
  expect(imported.ok()).toBe(true)
  const claimed = await page.request.post(`${apiBase}/api/v1/review/claim`, {
    headers,
    data: {
      dataset_id: dataset.id,
      eval_type: "FACT_DECOMP",
      mode: "ITEM_AUDIT",
    },
  })
  expect(claimed.ok()).toBe(true)
  const { task_id: taskId } = (await claimed.json()) as { task_id: number }

  // Review the claim: lower its grade and flag an extraction issue.
  await page.goto(`/review/fact-decomposition?task_id=${taskId}`)
  const claim = page.locator('[data-claim-position="0"]')
  await claim
    .getByRole("group", { name: "Claim 1 label", exact: true })
    .getByRole("button", { name: "Semi-important", exact: true })
    .click()
  await claim
    .getByRole("button", { name: "Extraction issue", exact: true })
    .click()
  await claim
    .getByLabel("Claim 1 extraction issue", { exact: true })
    .fill("Leaves out the condition.")
  await page
    .getByLabel("I checked the text for missing worthwhile claims")
    .check()
  const saved = page.waitForResponse(
    (response) =>
      response.url().includes("/api/v1/review/fact-decomp") &&
      response.request().method() !== "GET",
  )
  await page.getByRole("button", { name: "Save and next" }).click()
  expect((await saved).ok()).toBe(true)

  // Load that dataset's grades.
  await page.goto("/admin")
  await page.getByRole("tab", { name: "Grades", exact: true }).click()
  await expect(
    page.getByRole("heading", { name: "Grades", exact: true }),
  ).toBeVisible()
  await page.getByTestId("admin-grades-dataset").click()
  await page.getByRole("option", { name: displayName }).click()
  await page.getByRole("button", { name: "Load grades" }).click()
  await expect(page.getByText(/1 records loaded at/)).toBeVisible()

  // Overview: one task, one claim, one human assessment that disagreed.
  await expect(page.getByText("1 of 1 TASKS reviewed")).toBeVisible()
  await expect(
    page.getByText("1 of 1 assessed claim GRADES differ from the model"),
  ).toBeVisible()
  await expect(
    page.getByText("0 graded up (toward vital), 1 graded down"),
  ).toBeVisible()
  await expect(page.getByText("1 Extraction issue")).toBeVisible()
  await expect(
    page.getByRole("cell", { name: "Why does filtration pressure hold?" }),
  ).toBeVisible()

  // Task view: the claim, the reviewer's choices, and the issue note.
  await page.getByRole("tab", { name: "Task view", exact: true }).click()
  await expect(page.getByText("1 review", { exact: true })).toBeVisible()
  await expect(
    page.getByRole("cell", {
      name: "Dehydration activates RAAS.",
      exact: true,
    }),
  ).toBeVisible()
  await page
    .getByRole("button", { name: "Extraction issue", exact: true })
    .click()
  await expect(page.getByText("Leaves out the condition.")).toBeVisible()
  await page.keyboard.press("Escape")

  // Analysis: the extraction card reads the same single flag.
  await page.getByRole("tab", { name: "Analysis", exact: true }).click()
  await expect(
    page.getByRole("heading", {
      name: "Did the model extract claims from the source text properly?",
    }),
  ).toBeVisible()
  await expect(
    page.getByText("claims were flagged by human reviewers as having an"),
  ).toBeVisible()
})
