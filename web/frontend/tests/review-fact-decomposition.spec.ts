import { createHash, randomUUID } from "node:crypto"
import { readFile } from "node:fs/promises"
import { expect, type Page, test } from "@playwright/test"
import { factFixture } from "./utils/factFixtures"

const createdDatasets: number[] = []
test.afterEach(() => {
  if (createdDatasets.length)
    factFixture({ action: "deactivate", datasets: createdDatasets.splice(0) })
})

test("submits an authored fact-decomposition review", async ({
  page,
}, testInfo) => {
  await page.goto("/review/fact-decomposition")
  await expect(
    page.getByRole("heading", { name: "Ordered Facts" }),
  ).toBeVisible()
  await expect(
    page.getByText("Baker appears in the E2E source. Café appears too."),
  ).toBeVisible()
  await page.getByTestId("rubric-independently_verifiable").click()
  await page.getByRole("option", { name: "Pass" }).click()
  await page.getByTestId("rubric-noise_removed").click()
  await page.getByRole("option", { name: "Pass" }).click()
  await page.getByTestId("rubric-deduplicated_ordered").click()
  await page.getByRole("option", { name: "Pass" }).click()
  await expect(
    page.getByRole("button", { name: "Save and next" }),
  ).toBeDisabled()
  for (const button of await page
    .getByRole("button", { name: "Looks good", exact: true })
    .all())
    await button.click()
  const firstDuplicate = page
    .getByRole("button", { name: "Duplicate", exact: true })
    .first()
  await firstDuplicate.click()
  await expect(
    page.getByRole("button", { name: "Looks good", exact: true }).first(),
  ).toHaveAttribute("aria-pressed", "false")
  await page
    .getByRole("button", { name: "Multiple Facts", exact: true })
    .first()
    .click()
  const taskText = await page.getByText(/^Task \d+$/).innerText()
  await page.screenshot({
    path: testInfo.outputPath("authored-duplicate.png"),
    fullPage: true,
  })
  await page.getByRole("button", { name: "Save and next" }).click()
  await expect(
    page.getByRole("heading", { name: "All caught up" }),
  ).toBeVisible()
  await page.getByRole("button", { name: "Previous example" }).click()
  await page.goto(
    `/review/fact-decomposition?task_id=${taskText.replace("Task ", "")}`,
  )
  await expect(
    page.getByRole("button", { name: "Duplicate", exact: true }).first(),
  ).toHaveAttribute("aria-pressed", "true")
  await expect(
    page.getByRole("button", { name: "Multiple Facts", exact: true }).first(),
  ).toHaveAttribute("aria-pressed", "true")
  await expect(
    page.getByRole("button", { name: "Duplicate", exact: true }).first(),
  ).toBeDisabled()
  await expect(
    page.getByRole("button", { name: "Save and next" }),
  ).toBeDisabled()
})

async function selectSourceText(page: Page, target: string) {
  await page.getByTestId("claim-response").evaluate((element, selectedText) => {
    const walker = document.createTreeWalker(element, NodeFilter.SHOW_TEXT)
    const nodes: Text[] = []
    let joined = ""
    while (walker.nextNode()) {
      const node = walker.currentNode as Text
      nodes.push(node)
      joined += node.data
    }
    const targetStart = joined.indexOf(selectedText)
    if (targetStart < 0) throw new Error("Selection target is missing")
    const targetEnd = targetStart + selectedText.length
    let consumed = 0
    let startNode: Text | null = null
    let endNode: Text | null = null
    let startOffset = 0
    let endOffset = 0
    for (const node of nodes) {
      const next = consumed + node.data.length
      if (
        startNode === null &&
        targetStart >= consumed &&
        targetStart <= next
      ) {
        startNode = node
        startOffset = targetStart - consumed
      }
      if (targetEnd >= consumed && targetEnd <= next) {
        endNode = node
        endOffset = targetEnd - consumed
        break
      }
      consumed = next
    }
    if (startNode === null || endNode === null)
      throw new Error("Could not resolve selection boundaries")
    const range = document.createRange()
    range.setStart(startNode, startOffset)
    range.setEnd(endNode, endOffset)
    const selection = window.getSelection()
    selection?.removeAllRanges()
    selection?.addRange(range)
    element.dispatchEvent(new MouseEvent("mouseup", { bubbles: true }))
  }, target)
}

function modelRows(caseId: string) {
  const response =
    "Reasoning 😀 shows dehydration activates RAAS and efferent vasoconstriction preserves filtration pressure."
  const firstText = "dehydration activates RAAS"
  const secondText =
    "RAAS and efferent vasoconstriction preserves filtration pressure"
  const codePointStart = (text: string) =>
    Array.from(response.slice(0, response.indexOf(text))).length
  const promptOne = "First instructions.\r\nUnicode 😀\n"
  const promptTwo = "Second instructions.\n"
  const makeRow = (armId: string, promptText: string, claims: object[]) => ({
    schema_version: 2,
    eval_type: "FACT_DECOMP",
    external_id: createHash("sha256")
      .update(`${caseId}\0${armId}`)
      .digest("hex"),
    case_id: caseId,
    source: "LLM",
    user_prompt: null,
    assistant_response: response,
    arm_id: armId,
    generator: {
      model_id: "openai/gpt-oss-20b",
      model_revision: null,
      prompt_text: promptText,
      pydantic_ai_version: "2.33.0",
      generation: {},
    },
    claims,
  })
  const firstRow = makeRow("e2e-arm-one", promptOne, [
    {
      claim: "Dehydration activates RAAS.",
      spans: [
        {
          start: codePointStart(firstText),
          end: codePointStart(firstText) + Array.from(firstText).length,
          text: firstText,
        },
      ],
      label: "vital",
    },
    {
      claim: "Efferent vasoconstriction preserves filtration pressure.",
      spans: [
        {
          start: codePointStart(secondText),
          end: codePointStart(secondText) + Array.from(secondText).length,
          text: secondText,
        },
      ],
      label: "unimportant",
    },
  ])
  const secondRow = makeRow("e2e-arm-two", promptTwo, [])
  return [firstRow, secondRow]
}

async function importModelRows(
  page: Page,
  scenario: string,
  rows: ReturnType<typeof modelRows>,
) {
  await page.goto("/")
  const accessToken = await page.evaluate(() =>
    localStorage.getItem("access_token"),
  )
  expect(accessToken).not.toBeNull()
  const headers = { Authorization: `Bearer ${accessToken}` }
  const apiBase = process.env.VITE_API_URL ?? "http://127.0.0.1:8000"
  const displayName = `Fact review ${scenario} ${randomUUID()}`
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
  const imported = await page.request.post(`${apiBase}/api/v1/admin/ingest`, {
    headers,
    multipart: {
      dataset_id: String(dataset.id),
      file: {
        name: "fact-decomposition.jsonl",
        mimeType: "application/x-ndjson",
        buffer: Buffer.from(
          `${rows.map((row) => JSON.stringify(row)).join("\n")}\n`,
        ),
      },
    },
  })
  expect(imported.ok()).toBe(true)
  expect((await imported.json()).created).toBe(rows.length)
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
  return { apiBase, headers, taskId, displayName }
}

test("grades model claims and saves human selections through a failed request and reload", async ({
  page,
}, testInfo) => {
  const rows = modelRows("e2e-grading")
  const { apiBase, headers, taskId } = await importModelRows(page, "grading", [
    rows[0],
  ])
  const response = rows[0].assistant_response
  const codePointStart = (text: string) =>
    Array.from(response.slice(0, response.indexOf(text))).length
  await page.goto(`/review/fact-decomposition?task_id=${taskId}`)
  await expect(
    page.getByRole("heading", { name: "Review extraction and importance" }),
  ).toBeVisible()
  await expect(page).toHaveURL(/task_id=\d+/)
  await expect(
    page.getByText("Model label: vital", { exact: true }),
  ).toBeVisible()
  const source = page.getByTestId("claim-response")
  const claimList = page.getByTestId("claim-correction-list")
  const firstHighlight = source.locator('[data-owner-positions="0"]').first()
  const secondHighlight = source.locator('[data-owner-positions="1"]').first()
  const firstColor = await firstHighlight.evaluate(
    (element) => getComputedStyle(element).backgroundColor,
  )
  const secondColor = await secondHighlight.evaluate(
    (element) => getComputedStyle(element).backgroundColor,
  )
  expect(firstColor).not.toBe("rgba(0, 0, 0, 0)")
  expect(secondColor).not.toBe("rgba(0, 0, 0, 0)")
  expect(firstColor).not.toBe(secondColor)
  const cardColors = await claimList
    .locator("[data-claim-position]")
    .evaluateAll((elements) =>
      elements.map((element) => getComputedStyle(element).borderLeftColor),
    )
  expect(cardColors[0]).not.toBe(cardColors[1])
  await firstHighlight.click()
  await expect(claimList.locator('[data-claim-position="0"]')).toHaveClass(
    /ring-2/,
  )
  const instructions = page.getByRole("button", {
    name: "Grading instructions",
    exact: true,
  })
  await instructions.click()
  const instructionsOpen = await instructions.getAttribute("aria-expanded")
  await page.reload()
  await expect(instructions).toHaveCount(1)
  await expect(instructions).toHaveAttribute("aria-expanded", instructionsOpen!)
  if (instructionsOpen === "true") await instructions.click()
  const firstLabels = page.getByRole("group", {
    name: "Claim 1 label",
    exact: true,
  })
  const secondLabels = page.getByRole("group", {
    name: "Claim 2 label",
    exact: true,
  })
  await expect(
    firstLabels.getByRole("button", { name: "Vital", exact: true }),
  ).toHaveAttribute("aria-pressed", "true")
  await expect(
    secondLabels.getByRole("button", { name: "Unimportant", exact: true }),
  ).toHaveAttribute("aria-pressed", "true")

  const decisions = page.getByRole("group", { name: "Claim 1 review decision" })
  await expect(page.locator('[data-claim-position="0"] summary')).toContainText(
    "Needs review",
  )
  await decisions
    .getByRole("button", { name: "Looks good", exact: true })
    .click()
  await expect(
    decisions.getByRole("button", { name: "Looks good", exact: true }),
  ).toHaveAttribute("aria-pressed", "true")
  await decisions
    .getByRole("button", { name: "Duplicate", exact: true })
    .click()
  await expect(
    decisions.getByRole("button", { name: "Looks good", exact: true }),
  ).toHaveAttribute("aria-pressed", "false")
  await decisions
    .getByRole("button", { name: "Duplicate", exact: true })
    .click()
  await expect(page.locator('[data-claim-position="0"] summary')).toContainText(
    "Needs review",
  )
  await decisions
    .getByRole("button", { name: "Duplicate", exact: true })
    .click()
  const multipleFacts = decisions.getByRole("button", {
    name: "Multiple Facts",
    exact: true,
  })
  await multipleFacts.click()
  await expect(multipleFacts).toHaveAttribute("aria-pressed", "true")
  await decisions
    .getByRole("button", { name: "Looks good", exact: true })
    .click()
  await expect(multipleFacts).toHaveAttribute("aria-pressed", "false")
  await multipleFacts.click()
  await decisions
    .getByRole("button", { name: "Duplicate", exact: true })
    .click()
  await firstLabels.getByRole("button", { name: "Unimportant" }).click()
  const firstClaim = page.locator('[data-claim-position="0"]')
  const secondClaim = page.locator('[data-claim-position="1"]')
  await firstClaim.locator("summary").click()
  await expect(firstClaim).not.toHaveAttribute("open")
  await expect(firstClaim.locator("summary")).toContainText("unimportant")
  await expect(secondClaim).toHaveAttribute("open")
  await firstHighlight.click()
  await expect(firstClaim).toHaveAttribute("open")
  await page.getByLabel("Hide model results").check()
  await expect(firstClaim).toBeHidden()
  await expect(source.locator("[data-owner-positions]")).toHaveCount(0)
  await page.getByLabel("Hide model results").uncheck()
  await expect(firstClaim).toBeVisible()
  await expect(firstHighlight).toBeVisible()
  await firstClaim.screenshot({
    path: testInfo.outputPath("duplicate-review-controls.png"),
  })
  await firstClaim
    .getByRole("button", { name: "Extraction issue", exact: true })
    .click()
  await firstClaim
    .getByLabel("Claim 1 extraction issue")
    .fill("This claim changes the stated causal relationship.")
  await secondLabels
    .getByRole("button", { name: "Unimportant", exact: true })
    .click()
  await secondClaim
    .getByRole("button", { name: "Extraction issue", exact: true })
    .click()
  await secondClaim
    .getByLabel("Claim 2 extraction issue")
    .fill("The source leaves the subject ambiguous.")
  await expect(secondLabels.getByRole("button", { pressed: true })).toHaveCount(
    0,
  )

  await selectSourceText(
    page,
    "shows dehydration activates RAAS and efferent vasoconstriction",
  )
  await page.getByRole("button", { name: "Add missing claim" }).click()
  await page
    .getByLabel("New human claim text")
    .fill("RAAS and efferent vasoconstriction are connected.")
  await page
    .getByRole("group", { name: "New human claim label" })
    .getByRole("button", { name: "Vital" })
    .click()
  await page.getByRole("button", { name: "Add human claim" }).click()
  const missingClaim = page.locator('[data-claim-position="2"]')
  await expect(missingClaim.getByLabel("Human claim 1 text")).toHaveValue(
    "RAAS and efferent vasoconstriction are connected.",
  )
  await missingClaim.getByLabel("Human claim 1 text").fill(" ")
  await expect(
    page.getByRole("button", { name: "Save and next" }),
  ).toBeDisabled()
  await missingClaim
    .getByLabel("Human claim 1 text")
    .fill("RAAS and efferent vasoconstriction are connected.")

  // Human claims can overlap the same source passage and be removed independently.
  await page.getByRole("button", { name: "Add missing claim" }).click()
  await page.getByLabel("New human claim text").fill("Second human assertion")
  await page
    .getByRole("group", { name: "New human claim label" })
    .getByRole("button", { name: "Semi-important" })
    .click()
  await page.getByRole("button", { name: "Add human claim" }).click()
  const overlappingOwners = await source
    .locator("[data-owner-positions]")
    .evaluateAll((elements) =>
      elements.map((element) =>
        (element.getAttribute("data-owner-positions") ?? "").split(","),
      ),
    )
  expect(
    overlappingOwners.some(
      (owners) => owners.includes("2") && owners.includes("3"),
    ),
  ).toBe(true)
  await page
    .locator('[data-claim-position="3"]')
    .getByRole("button", { name: "Remove human claim" })
    .click()
  await expect(
    page.getByTestId("human-claims").locator("[data-claim-position]"),
  ).toHaveCount(1)
  await expect(
    firstLabels.getByRole("button", { name: "Unimportant" }),
  ).toHaveAttribute("aria-pressed", "true")
  await expect(secondClaim.getByLabel("Claim 2 extraction issue")).toHaveValue(
    "The source leaves the subject ambiguous.",
  )
  await page
    .getByLabel("I checked the text for missing worthwhile claims")
    .check()
  await page.locator("main.overflow-y-auto").evaluate((element) => {
    element.scrollTop = 0
  })
  await page.screenshot({
    path: testInfo.outputPath("fact-decomposition-grading-top.png"),
    fullPage: true,
  })

  let failOnce = true
  await page.route("**/review/fact-decomp/*/model-eval", async (route) => {
    if (failOnce) {
      failOnce = false
      await route.abort("failed")
    } else {
      await route.continue()
    }
  })
  await page.getByRole("button", { name: "Save and next" }).click()
  await expect(page.getByRole("alert")).toBeVisible()
  await expect(
    firstLabels.getByRole("button", { name: "Unimportant" }),
  ).toHaveAttribute("aria-pressed", "true")
  await expect(secondClaim.getByLabel("Claim 2 extraction issue")).toHaveValue(
    "The source leaves the subject ambiguous.",
  )
  await page.getByRole("button", { name: "Retry save" }).click()
  await expect(
    page.getByRole("heading", { name: "All caught up" }),
  ).toBeVisible()
  await page.getByRole("button", { name: "Previous example" }).click()
  await page.screenshot({
    path: testInfo.outputPath("fact-decomposition-grading.png"),
    fullPage: true,
  })

  await page.reload()
  await expect(secondLabels.getByRole("button", { pressed: true })).toHaveCount(
    0,
  )
  await expect(
    firstLabels.getByRole("button", { name: "Unimportant" }),
  ).toHaveAttribute("aria-pressed", "true")
  await expect(
    firstLabels.getByRole("button", { name: "Unimportant" }),
  ).toBeDisabled()
  await expect(
    page.getByLabel("I checked the text for missing worthwhile claims"),
  ).toBeChecked()
  await expect(secondClaim.getByLabel("Claim 2 extraction issue")).toHaveValue(
    "The source leaves the subject ambiguous.",
  )

  const savedResponse = await page.request.get(
    `${apiBase}/api/v1/review/fact-decomp/${taskId}`,
    { headers },
  )
  expect(savedResponse.ok()).toBe(true)
  const saved = await savedResponse.json()
  expect(saved.existing_review.claim_reviews).toEqual([
    {
      position: 0,
      label: "unimportant",
      issue: "This claim changes the stated causal relationship.",
      duplicate: true,
      multiple_facts: true,
      looks_good: false,
    },
    {
      position: 1,
      label: null,
      issue: "The source leaves the subject ambiguous.",
      duplicate: false,
      multiple_facts: false,
      looks_good: false,
    },
  ])
  expect(saved.existing_review.human_claims).toEqual([
    {
      claim_text: "RAAS and efferent vasoconstriction are connected.",
      label: "vital",
      response_spans: [
        {
          start: codePointStart(
            "shows dehydration activates RAAS and efferent vasoconstriction",
          ),
          end:
            codePointStart(
              "shows dehydration activates RAAS and efferent vasoconstriction",
            ) +
            Array.from(
              "shows dehydration activates RAAS and efferent vasoconstriction",
            ).length,
          text: "shows dehydration activates RAAS and efferent vasoconstriction",
        },
      ],
    },
  ])
  expect(saved.existing_review.coverage_checked).toBe(true)
})

test("reviews a response with no model claims", async ({ page }) => {
  const rows = modelRows("e2e-zero-claims")
  const { taskId } = await importModelRows(page, "zero claims", [rows[1]])
  await page.goto(`/review/fact-decomposition?task_id=${taskId}`)
  await expect(page.getByText("No model claims proposed.")).toBeVisible()
  await expect(
    page.getByRole("button", { name: "Save and next" }),
  ).toBeDisabled()
  await page
    .getByLabel("I checked the text for missing worthwhile claims")
    .check()
  await page.getByRole("button", { name: "Save and next" }).click()
  await expect(
    page.getByRole("heading", { name: "All caught up" }),
  ).toBeVisible()
  await page.getByRole("button", { name: "Previous example" }).click()
  await page.reload()
  await expect(
    page.getByLabel("I checked the text for missing worthwhile claims"),
  ).toBeChecked()
  await expect(
    page.getByRole("button", { name: "Save and next" }),
  ).toBeDisabled()
})

test("downloads exact prompt history and saved corrections", async ({
  page,
}, testInfo) => {
  const rows = modelRows("e2e-export")
  const { apiBase, headers, taskId, displayName } = await importModelRows(
    page,
    "export",
    rows,
  )
  const review = {
    rubric_id: "importance-v1",
    claim_reviews: [
      {
        position: 0,
        label: "vital",
        issue: null,
        duplicate: false,
        multiple_facts: false,
        looks_good: true,
      },
      {
        position: 1,
        label: null,
        issue: "Missing context.",
        duplicate: false,
        multiple_facts: false,
        looks_good: false,
      },
    ],
    human_claims: [],
    coverage_checked: true,
  }
  const submitted = await page.request.post(
    `${apiBase}/api/v1/review/fact-decomp/${taskId}/model-eval`,
    {
      headers,
      data: { ...review, item_revision: 1 },
    },
  )
  expect(submitted.ok()).toBe(true)
  await page.goto("/admin")
  await page.getByRole("tab", { name: "Export" }).click()
  await page.getByTestId("admin-export-dataset").click()
  await page.getByRole("option", { name: displayName }).click()
  await page.getByRole("button", { name: "Load export page" }).click()
  await expect(
    page.locator("pre").filter({ hasText: "First instructions." }),
  ).toBeVisible()
  await expect(
    page.locator("pre").filter({ hasText: "Second instructions." }),
  ).toBeVisible()
  const downloadPromise = page.waitForEvent("download")
  await page
    .getByRole("button", { name: "Download current export page" })
    .click()
  const download = await downloadPromise
  const exportPath = testInfo.outputPath("fact-decomposition-export.json")
  await download.saveAs(exportPath)
  const exported = JSON.parse(await readFile(exportPath, "utf8")) as {
    items: Array<{
      arm_id: string
      generator: { prompt_text: string }
      correction_reviews: Array<typeof review>
    }>
  }
  expect(
    Object.fromEntries(
      exported.items.map((item) => [item.arm_id, item.generator.prompt_text]),
    ),
  ).toEqual(
    Object.fromEntries(
      rows.map((row) => [row.arm_id, row.generator.prompt_text]),
    ),
  )
  const saved = exported.items.find((item) => item.arm_id === rows[0].arm_id)
    ?.correction_reviews[0]
  expect(saved).toMatchObject(review)
})

test("advances after saving and navigates previous examples without reopening stale recommendations", async ({
  page,
}, testInfo) => {
  const { taskId } = await importModelRows(page, "navigation", [
    modelRows("e2e-navigation-first")[1],
    modelRows("e2e-navigation-second")[1],
    modelRows("e2e-navigation-third")[1],
  ])
  await page.goto("/review/fact-decomposition")
  await expect(page).toHaveURL(new RegExp(`task_id=${taskId}$`))
  const firstUrl = page.url()
  const coverage = page.getByLabel(
    "I checked the text for missing worthwhile claims",
  )
  await coverage.check()

  // Saving succeeds, but a failed queue request must leave a retryable saved review.
  await page.route("**/review/next?**", (route) => route.abort("failed"))
  await page.getByRole("button", { name: "Save and next" }).click()
  await expect(page.getByText("Review saved.")).toBeVisible()
  await expect(page.getByRole("alert")).toBeVisible()
  await expect(page).toHaveURL(firstUrl)
  await expect(
    page.getByRole("button", { name: "Save and next" }),
  ).toBeDisabled()
  await page.unroute("**/review/next?**")
  await page.getByRole("button", { name: "Next example", exact: true }).click()
  await expect(page).not.toHaveURL(firstUrl)
  const secondUrl = page.url()
  await expect(coverage).not.toBeChecked()
  await expect(coverage).toBeEnabled()

  await page.goBack()
  await expect(page).toHaveURL(firstUrl)
  await expect(coverage).toBeChecked()
  await expect(coverage).toBeDisabled()
  await page.getByRole("button", { name: "Next example", exact: true }).click()
  await expect(page).toHaveURL(secondUrl)
  await coverage.check()
  await page.getByRole("button", { name: "Save and next" }).click()
  await expect(page).not.toHaveURL(secondUrl)
  const thirdUrl = page.url()
  await expect(coverage).not.toBeChecked()
  await expect(coverage).toBeEnabled()
  await coverage.check()
  await page.getByRole("button", { name: "Save and next" }).click()
  await expect(
    page.getByRole("heading", { name: "All caught up" }),
  ).toBeVisible()
  await page.screenshot({
    path: testInfo.outputPath("review-all-caught-up.png"),
    fullPage: true,
  })
  await page.getByRole("button", { name: "Previous example" }).click()
  await expect(page).toHaveURL(thirdUrl)
  await page.getByRole("button", { name: "Previous example" }).click()
  await expect(page).toHaveURL(secondUrl)
  await expect(coverage).toBeChecked()
  await expect(coverage).toBeDisabled()
  await page.getByRole("button", { name: "Previous example" }).click()
  await expect(page).toHaveURL(firstUrl)
  await page.screenshot({
    path: testInfo.outputPath("review-previous-example.png"),
    fullPage: true,
  })
  await page.getByRole("button", { name: "Next example", exact: true }).click()
  await expect(page).toHaveURL(secondUrl)

  // Keep the SPA query cache alive, then re-enter while the queue response is delayed.
  await page.getByRole("link", { name: "Home", exact: true }).click()
  await page.route("**/review/next?**", async (route) => {
    await new Promise((resolve) => setTimeout(resolve, 500))
    await route.continue()
  })
  await page.getByRole("link", { name: "Review", exact: true }).first().click()
  await page.getByRole("link", { name: /Fact Decomposition/ }).click()
  await expect(
    page.getByRole("heading", { name: "All caught up" }),
  ).toBeVisible()
  await expect(page).not.toHaveURL(/task_id=/)
})

test("waits for a fresh recommendation when returning from home after saving", async ({
  page,
}) => {
  const { taskId } = await importModelRows(page, "home navigation", [
    modelRows("e2e-home-first")[1],
    modelRows("e2e-home-second")[1],
  ])
  await page.goto("/review/fact-decomposition")
  await expect(page).toHaveURL(new RegExp(`task_id=${taskId}$`))
  const firstUrl = page.url()
  await page
    .getByLabel("I checked the text for missing worthwhile claims")
    .check()
  await page.route("**/review/next?**", (route) => route.abort("failed"))
  await page.getByRole("button", { name: "Save and next" }).click()
  await expect(page.getByRole("alert")).toBeVisible()
  await page.getByRole("link", { name: "Home", exact: true }).click()
  await page.unroute("**/review/next?**")
  await page.route("**/review/next?**", async (route) => {
    await new Promise((resolve) => setTimeout(resolve, 500))
    await route.continue()
  })
  await page.getByRole("link", { name: "Review", exact: true }).first().click()
  await page.getByRole("link", { name: /Fact Decomposition/ }).click()
  await expect(
    page.getByLabel("I checked the text for missing worthwhile claims"),
  ).toBeEnabled()
  await expect(page).toHaveURL(/task_id=\d+$/)
  await expect(page).not.toHaveURL(firstUrl)
  await expect(
    page.getByLabel("I checked the text for missing worthwhile claims"),
  ).not.toBeChecked()
})
