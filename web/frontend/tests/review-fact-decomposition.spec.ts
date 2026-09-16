import { createHash } from "node:crypto"
import { readFile } from "node:fs/promises"
import { expect, type Page, test } from "@playwright/test"

test("submits an authored fact-decomposition review", async ({ page }) => {
  await page.goto("/review/fact-decomposition")
  await expect(
    page.getByRole("heading", { name: "Fact Decomposition Review" }),
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
  await page.getByRole("button", { name: "Submit fact review" }).click()
  await expect(
    page.getByText(/Fact review submitted for item \d+\./),
  ).toBeVisible()
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

test("grades extraction, reloads it, and downloads exact prompt history", async ({
  page,
}, testInfo) => {
  await page.goto("/")
  const accessToken = await page.evaluate(() =>
    localStorage.getItem("access_token"),
  )
  expect(accessToken).not.toBeNull()
  const authorization = { Authorization: `Bearer ${accessToken}` }
  const apiBase = process.env.VITE_API_URL ?? "http://127.0.0.1:8000"
  const datasetsResponse = await page.request.get(
    `${apiBase}/api/v1/admin/datasets`,
    { headers: authorization },
  )
  const datasets = (await datasetsResponse.json()) as Array<{
    id: number
    name: string
  }>
  const dataset = datasets.find(
    (candidate) => candidate.name === "e2e-fact-decomposition",
  )
  expect(dataset).toBeDefined()

  const caseId = "e2e-response-only"
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
  const artifact = Buffer.from(
    `${JSON.stringify(firstRow)}\n${JSON.stringify(secondRow)}\n`,
  )
  const importArtifact = (dryRun: boolean) =>
    page.request.post(`${apiBase}/api/v1/admin/ingest`, {
      headers: authorization,
      multipart: {
        dataset_id: String(dataset?.id),
        dry_run: String(dryRun),
        file: {
          name: "fact-decomposition.jsonl",
          mimeType: "application/x-ndjson",
          buffer: artifact,
        },
      },
    })
  expect((await (await importArtifact(true)).json()).created).toBe(2)
  expect((await (await importArtifact(false)).json()).created).toBe(2)
  expect((await (await importArtifact(false)).json()).unchanged).toBe(2)

  await page.goto("/review/fact-decomposition")
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
  await firstClaim.getByLabel("Flag extraction").check()
  await firstClaim
    .getByLabel("Claim 1 extraction issue")
    .fill("This claim changes the stated causal relationship.")
  await secondLabels
    .getByRole("button", { name: "Unimportant", exact: true })
    .click()
  await secondClaim.getByLabel("Flag extraction").check()
  await secondClaim
    .getByLabel("Claim 2 extraction issue")
    .fill("The source leaves the subject ambiguous.")
  await expect(secondLabels.getByRole("button", { pressed: true })).toHaveCount(
    0,
  )

  await selectSourceText(page, "RAAS and efferent vasoconstriction")
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
  await expect(page.getByRole("button", { name: "Save review" })).toBeDisabled()
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
  await page.getByRole("button", { name: "Save review" }).click()
  await expect(page.getByRole("alert")).toBeVisible()
  await expect(
    firstLabels.getByRole("button", { name: "Unimportant" }),
  ).toHaveAttribute("aria-pressed", "true")
  await expect(secondClaim.getByLabel("Claim 2 extraction issue")).toHaveValue(
    "The source leaves the subject ambiguous.",
  )
  await page.getByRole("button", { name: "Save review" }).click()
  await expect(page.getByText("Review saved.")).toBeVisible()
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

  await page.goto("/review/fact-decomposition")
  await expect(page.getByText("No model claims proposed.")).toBeVisible()
  await expect(page.getByRole("button", { name: "Save review" })).toBeDisabled()
  await page
    .getByLabel("I checked the text for missing worthwhile claims")
    .check()
  await page.getByRole("button", { name: "Save review" }).click()
  await expect(page.getByText("Review saved.")).toBeVisible()

  await page.goto("/admin")
  await page.getByRole("tab", { name: "Export" }).click()
  await page.getByTestId("admin-export-dataset").click()
  await page.getByRole("option", { name: "E2E Fact Decomposition" }).click()
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
      arm_id?: string
      generator?: { prompt_text: string }
      correction_reviews?: Array<{
        claim_reviews: Array<{
          position: number
          label: string | null
          issue: string | null
        }>
        human_claims: Array<{
          claim_text: string
          label: string
          response_spans: Array<{ start: number; end: number; text: string }>
        }>
        coverage_checked: boolean
      }>
    }>
  }
  const history = Object.fromEntries(
    exported.items
      .filter((item) => item.arm_id)
      .map((item) => [item.arm_id, item.generator?.prompt_text]),
  )
  expect(history).toEqual({
    "e2e-arm-one": promptOne,
    "e2e-arm-two": promptTwo,
  })
  const reviewed = exported.items.find((item) => item.arm_id === "e2e-arm-one")
  expect(reviewed?.correction_reviews?.[0].claim_reviews).toEqual([
    {
      position: 0,
      label: "unimportant",
      issue: "This claim changes the stated causal relationship.",
    },
    {
      position: 1,
      label: null,
      issue: "The source leaves the subject ambiguous.",
    },
  ])
  expect(reviewed?.correction_reviews?.[0].human_claims).toEqual([
    {
      claim_text: "RAAS and efferent vasoconstriction are connected.",
      label: "vital",
      response_spans: [
        {
          start: codePointStart("RAAS and efferent vasoconstriction"),
          end:
            codePointStart("RAAS and efferent vasoconstriction") +
            Array.from("RAAS and efferent vasoconstriction").length,
          text: "RAAS and efferent vasoconstriction",
        },
      ],
    },
  ])
  expect(reviewed?.correction_reviews?.[0].coverage_checked).toBe(true)
})
