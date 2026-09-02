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
        label: "vital",
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
        label: "supporting",
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
    page.getByRole("heading", { name: "Correct the model labels" }),
  ).toBeVisible()
  await expect(page.getByText("Proposed: vital")).toBeVisible()
  await expect(page.getByText("Proposed: supporting")).toBeVisible()
  await expect(page.getByText("User prompt", { exact: true })).toHaveCount(0)

  const source = page.getByTestId("claim-response")
  const claimList = page.getByTestId("claim-correction-list")
  const desktopSourceBox = await source.boundingBox()
  const desktopClaimBox = await claimList.boundingBox()
  expect(desktopSourceBox).not.toBeNull()
  expect(desktopClaimBox).not.toBeNull()
  expect(desktopClaimBox?.x).toBeGreaterThan(desktopSourceBox?.x ?? 0)

  const firstLabels = page.getByRole("group", { name: "Claim 1 label" })
  await expect(firstLabels.getByRole("button")).toHaveCount(4)
  await firstLabels.getByRole("button", { name: "peripheral" }).click()

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
  await page.getByRole("button", { name: "Add missing claim" }).click()
  const missing = page.getByTestId("missing-claim-missing-0")
  await expect(missing.getByLabel("Missing claim text")).toHaveValue(
    "RAAS and efferent vasoconstriction",
  )
  await missing
    .getByRole("group", { name: "Missing claim label" })
    .getByRole("button", { name: "duplicate" })
    .click()

  await page.setViewportSize({ width: 390, height: 844 })
  const mobileSourceBox = await source.boundingBox()
  const mobileClaimBox = await claimList.boundingBox()
  expect(mobileSourceBox).not.toBeNull()
  expect(mobileClaimBox).not.toBeNull()
  expect(mobileClaimBox?.y).toBeGreaterThan(mobileSourceBox?.y ?? 0)

  await page.getByRole("button", { name: "Submit correction" }).click()
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
        final_labels?: string[]
        missing_claims?: Array<{ claim_text: string; label: string }>
      }>
    }>
  }
  const exportedItem = exported.items.find((item) => item.case_id === caseId)
  expect(exportedItem?.claims?.map((claim) => claim.proposed_label)).toEqual([
    "vital",
    "supporting",
  ])
  expect(exportedItem?.correction_reviews?.[0].final_labels).toEqual([
    "peripheral",
    "supporting",
  ])
  expect(exportedItem?.correction_reviews?.[0].missing_claims).toEqual([
    {
      claim_text: "RAAS and efferent vasoconstriction",
      response_spans: [
        {
          start: secondStart,
          end:
            secondStart +
            Array.from("RAAS and efferent vasoconstriction").length,
          text: "RAAS and efferent vasoconstriction",
        },
      ],
      label: "duplicate",
    },
  ])
  expect(exportText).not.toContain("api_key")
  expect(exportText).not.toContain("raw_messages")
})
