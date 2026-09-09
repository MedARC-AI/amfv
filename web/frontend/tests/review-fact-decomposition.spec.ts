import { createHash } from "node:crypto"
import { expect, test } from "@playwright/test"

test("submits a fact-decomposition review with fact calls and rubric", async ({
  page,
}) => {
  await page.goto("/review/fact-decomposition")

  await expect(
    page.getByRole("heading", { name: "Fact Decomposition Review" }),
  ).toBeVisible()
  await expect(
    page.getByText("Baker appears in the E2E source. Café appears too."),
  ).toBeVisible()
  const orderedFacts = page.getByTestId("ordered-facts")
  await expect(
    orderedFacts.getByText("Baker appears in the E2E source.", { exact: true }),
  ).toBeVisible()
  await expect(
    orderedFacts.getByText("A distractor answer should not be listed."),
  ).toBeVisible()
  await expect(
    page.getByRole("button", { name: "Submit fact review" }),
  ).toBeEnabled()

  await page.getByTestId("rubric-independently_verifiable").click()
  await page.getByRole("option", { name: "Pass" }).click()
  await page.getByTestId("rubric-noise_removed").click()
  await page.getByRole("option", { name: "Pass" }).click()
  await page.getByTestId("rubric-deduplicated_ordered").click()
  await page.getByRole("option", { name: "Pass" }).click()
  await page.getByLabel("Comments").fill("Facts are ordered and verifiable.")
  await page.getByRole("button", { name: "Submit fact review" }).click()

  await expect(
    page.getByText(/Fact review submitted for item \d+\./),
  ).toBeVisible()
  await expect(
    page.getByRole("button", { name: "Submit fact review" }),
  ).toBeDisabled()

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
  expect(datasetsResponse.ok()).toBe(true)
  const datasets = (await datasetsResponse.json()) as Array<{
    id: number
    name: string
  }>
  const dataset = datasets.find(
    (candidate) => candidate.name === "e2e-fact-decomposition",
  )
  expect(dataset).toBeDefined()
  const caseId = "e2e-response-only"
  const armId = "e2e-gpt-oss"
  const response =
    "Reasoning 😀 shows dehydration activates RAAS and efferent vasoconstriction preserves filtration pressure."
  const firstText = "dehydration activates RAAS"
  const secondText =
    "RAAS and efferent vasoconstriction preserves filtration pressure"
  const firstStart = Array.from(
    response.slice(0, response.indexOf(firstText)),
  ).length
  const secondStart = Array.from(
    response.slice(0, response.indexOf(secondText)),
  ).length
  const row = {
    schema_version: 1,
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
      prompt_id: "e2e",
      prompt_hash: "0".repeat(64),
      pydantic_ai_version: "2.33.0",
      generation: {},
    },
    claims: [
      {
        claim: "Dehydration activates RAAS.",
        spans: [
          {
            start: firstStart,
            end: firstStart + Array.from(firstText).length,
            text: firstText,
          },
        ],
        label: "substantive",
      },
      {
        claim: "Efferent vasoconstriction preserves filtration pressure.",
        spans: [
          {
            start: secondStart,
            end: secondStart + Array.from(secondText).length,
            text: secondText,
          },
        ],
        label: "substantive",
      },
    ],
  }
  const artifact = Buffer.from(`${JSON.stringify(row)}\n`)
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
  const preview = await importArtifact(true)
  expect(preview.ok()).toBe(true)
  expect((await preview.json()).created).toBe(1)
  const imported = await importArtifact(false)
  expect(imported.ok()).toBe(true)
  expect((await imported.json()).created).toBe(1)
  const replay = await importArtifact(false)
  expect(replay.ok()).toBe(true)
  expect((await replay.json()).unchanged).toBe(1)

  await page.goto("/review/fact-decomposition")
  await expect(
    page.getByRole("heading", { name: "Review verification relevance" }),
  ).toBeVisible()
  await expect(page.getByText("Proposed: substantive")).toHaveCount(2)
  await expect(
    page.getByText(
      /Substantive and borderline claims are included in verification/,
    ),
  ).toBeVisible()
  await expect(page.getByText("User prompt", { exact: true })).toHaveCount(0)

  const source = page.getByTestId("claim-response")
  const claimList = page.getByTestId("claim-correction-list")
  const desktopSourceBox = await source.boundingBox()
  const desktopClaimBox = await claimList.boundingBox()
  expect(desktopSourceBox).not.toBeNull()
  expect(desktopClaimBox).not.toBeNull()
  expect(desktopClaimBox?.x).toBeGreaterThan(desktopSourceBox?.x ?? 0)

  const firstLabels = page.getByRole("group", { name: "Claim 1 label" })
  await expect(firstLabels.getByRole("button")).toHaveCount(3)
  await firstLabels.getByRole("button", { name: "incidental" }).click()

  const firstClaim = page.locator('[data-claim-position="0"]')
  const secondClaim = page.locator('[data-claim-position="1"]')
  await firstClaim.getByRole("button", { name: "Remove", exact: true }).click()
  await expect(
    firstClaim.getByText("Removed from final decomposition."),
  ).toBeVisible()
  await firstClaim.getByRole("button", { name: "Undo to original" }).click()
  await expect(
    firstLabels.getByRole("button", { name: "substantive" }),
  ).toHaveAttribute("aria-pressed", "true")
  await firstClaim.getByRole("button", { name: "Edit", exact: true }).click()
  await firstClaim.getByLabel("Claim 1 part 1 text").fill("Discard this edit")
  await firstClaim.getByRole("button", { name: "Cancel", exact: true }).click()
  await expect(firstClaim.getByText("Discard this edit")).toHaveCount(0)
  await firstClaim.getByRole("button", { name: "Split", exact: true }).click()
  await expect(
    page.getByRole("button", { name: "Submit correction" }),
  ).toBeDisabled()
  await firstClaim
    .getByLabel("Claim 1 part 1 text")
    .fill("First corrected assertion")
  await firstClaim
    .getByLabel("Claim 1 part 2 text")
    .fill("Second corrected assertion")
  await firstClaim
    .getByRole("group", { name: "Claim 1 part 2 label" })
    .getByRole("button", { name: "borderline" })
    .click()
  await firstClaim.getByRole("button", { name: "Apply changes" }).click()
  await secondClaim.getByRole("button", { name: "Remove", exact: true }).click()

  await source.evaluate((element) => {
    const target = "RAAS and efferent vasoconstriction"
    const walker = document.createTreeWalker(element, NodeFilter.SHOW_TEXT)
    const nodes: Text[] = []
    let joined = ""
    while (walker.nextNode()) {
      const node = walker.currentNode as Text
      nodes.push(node)
      joined += node.data
    }
    const targetStart = joined.indexOf(target)
    if (targetStart < 0) throw new Error("Selection target is missing")
    const targetEnd = targetStart + target.length
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
    if (startNode === null || endNode === null) {
      throw new Error("Could not resolve selection boundaries")
    }
    const range = document.createRange()
    range.setStart(startNode, startOffset)
    range.setEnd(endNode, endOffset)
    const selection = window.getSelection()
    selection?.removeAllRanges()
    selection?.addRange(range)
    element.dispatchEvent(new MouseEvent("mouseup", { bubbles: true }))
  })
  await firstClaim.getByRole("button", { name: "Edit", exact: true }).click()
  await firstClaim
    .getByRole("button", { name: "Use selected source for part 2" })
    .click()
  await firstClaim.getByRole("button", { name: "Apply changes" }).click()
  await page.getByRole("button", { name: "Add missing claim" }).click()
  const missing = page.locator('[data-claim-position="2"]')
  await expect(
    missing.getByText("RAAS and efferent vasoconstriction", { exact: true }),
  ).toBeVisible()
  await missing
    .getByRole("group", { name: "Missing claim label" })
    .getByRole("button", { name: "borderline" })
    .click()

  await page.setViewportSize({ width: 390, height: 844 })
  const mobileSourceBox = await source.boundingBox()
  const mobileClaimBox = await claimList.boundingBox()
  expect(mobileSourceBox).not.toBeNull()
  expect(mobileClaimBox).not.toBeNull()
  expect(mobileClaimBox?.y).toBeGreaterThan(mobileSourceBox?.y ?? 0)

  // Hold the real request to inspect the pending state, then let it reach the backend.
  let releaseSubmission = () => {}
  const submissionGate = new Promise<void>((resolve) => {
    releaseSubmission = resolve
  })
  await page.route("**/review/fact-decomp/*/model-eval", async (route) => {
    await submissionGate
    await route.continue()
  })
  await page.getByRole("button", { name: "Submit correction" }).click()
  await expect(
    page.getByRole("button", { name: "Submitting correction" }),
  ).toBeDisabled()
  await expect(
    firstClaim.getByRole("button", { name: "Edit", exact: true }),
  ).toBeDisabled()
  await expect(
    missing.getByRole("button", { name: "incidental" }),
  ).toBeDisabled()
  await expect(
    page.getByRole("button", { name: "Add missing claim" }),
  ).toBeDisabled()
  releaseSubmission()
  await expect(page.getByText("Correction submitted.")).toBeVisible()
  await expect(
    page.getByRole("button", { name: "Submit correction" }),
  ).toBeDisabled()

  const exportResponse = await page.request.get(
    `${apiBase}/api/v1/admin/export?dataset_id=${dataset?.id}`,
    { headers: authorization },
  )
  expect(exportResponse.ok()).toBe(true)
  const exportText = await exportResponse.text()
  const exported = JSON.parse(exportText) as {
    items: Array<{
      case_id?: string
      claims?: Array<{ proposed_label: string }>
      correction_reviews?: Array<{
        final_claims?: Array<{
          original_position: number | null
          claim_text: string
          label: string
          response_spans: Array<{ start: number; end: number; text: string }>
        }>
      }>
    }>
  }
  const exportedItem = exported.items.find((item) => item.case_id === caseId)
  expect(exportedItem?.claims?.map((claim) => claim.proposed_label)).toEqual([
    "substantive",
    "substantive",
  ])
  const finalClaims = exportedItem?.correction_reviews?.[0].final_claims
  expect(
    finalClaims?.map((claim) => [
      claim.original_position,
      claim.claim_text,
      claim.label,
    ]),
  ).toEqual([
    [0, "First corrected assertion", "substantive"],
    [0, "Second corrected assertion", "borderline"],
    [null, "RAAS and efferent vasoconstriction", "borderline"],
  ])
  expect(finalClaims?.[1].response_spans).toEqual([
    {
      start: secondStart,
      end:
        secondStart + Array.from("RAAS and efferent vasoconstriction").length,
      text: "RAAS and efferent vasoconstriction",
    },
  ])
  await expect(
    firstClaim.getByRole("button", { name: "Edit", exact: true }),
  ).toBeDisabled()
  await expect(
    missing.getByRole("button", { name: "incidental" }),
  ).toBeDisabled()
  await expect(
    page.getByRole("button", { name: "Add missing claim" }),
  ).toBeDisabled()
  await page.screenshot({
    path: "/tmp/amfv-claim-correction-submitted.png",
    fullPage: true,
  })
  expect(exportText).not.toContain("api_key")
  expect(exportText).not.toContain("raw_messages")
})
