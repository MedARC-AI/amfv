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
    "Reasoning 😀 shows dehydration activates RAAS and efferent vasoconstriction preserves filtration pressure." +
    "\n\nAdditional source context for long-document scrolling.".repeat(30)
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

  // Verify computed styles: class names alone do not catch omitted Tailwind sources.
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
  await expect(firstLabels.getByRole("button")).toHaveCount(3)
  await firstLabels.getByRole("button", { name: "incidental" }).click()

  const firstClaim = page.locator('[data-claim-position="0"]')
  const secondClaim = page.locator('[data-claim-position="1"]')
  await firstClaim.locator("summary").click()
  await expect(firstClaim).not.toHaveAttribute("open")
  await expect(firstClaim.locator("summary")).toContainText("incidental")
  await expect(secondClaim).toHaveAttribute("open")
  await firstHighlight.click()
  await expect(firstClaim).toHaveAttribute("open")
  await page.getByLabel("Hide model results").check()
  await expect(firstClaim).toBeHidden()
  await expect(source.locator("[data-owner-positions]")).toHaveCount(0)

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
  await page
    .getByRole("button", { name: "Create human claim", exact: true })
    .click()
  await expect(page.getByLabel("New human claim text")).toHaveValue(
    "RAAS and efferent vasoconstriction",
  )
  await page.getByLabel("New human claim text").fill("First human assertion")
  await expect(
    page.getByRole("button", { name: "Save review", exact: true }),
  ).toBeDisabled()
  await page
    .getByRole("button", { name: "Add human claim", exact: true })
    .click()
  const missing = page.locator('[data-claim-position="2"]')
  await missing
    .getByRole("group", { name: "Human claim 1 label" })
    .getByRole("button", { name: "borderline" })
    .click()
  await missing.locator("summary").click()
  await expect(missing).not.toHaveAttribute("open")
  await expect(missing.locator("summary")).toContainText("borderline")
  // Another human assertion may use the same passage without replacing model claims.
  await page
    .getByRole("button", { name: "Create human claim", exact: true })
    .click()
  await page.getByLabel("New human claim text").fill("Second human assertion")
  await page
    .getByRole("button", { name: "Add human claim", exact: true })
    .click()
  await expect(missing).not.toHaveAttribute("open")
  await expect(source.locator('[data-owner-positions="2,3"]')).toBeVisible()
  await page.getByLabel("Hide model results").uncheck()
  await expect(
    firstLabels.getByRole("button", { name: "incidental" }),
  ).toHaveAttribute("aria-pressed", "true")
  await expect(
    page.getByTestId("model-claims").locator("[data-claim-position]"),
  ).toHaveCount(2)
  await missing.locator("summary").click()
  await expect(missing.getByLabel("Human claim 1 text")).toHaveValue(
    "First human assertion",
  )
  await missing.getByRole("button", { name: "Use selected source" }).click()
  // Adding/removing a human draft leaves both model grades untouched.
  await page
    .getByRole("button", { name: "Create human claim", exact: true })
    .click()
  await page.getByLabel("New human claim text").fill("Discard this draft")
  await page.getByRole("button", { name: "Cancel", exact: true }).click()
  await expect(
    page.getByTestId("human-claims").locator("[data-claim-position]"),
  ).toHaveCount(2)

  await missing.getByLabel("Human claim 1 text").fill(" ")
  await expect(
    page.getByRole("button", { name: "Save review", exact: true }),
  ).toBeDisabled()
  await missing.getByLabel("Human claim 1 text").fill("First human assertion")
  await page
    .getByRole("button", { name: "Create human claim", exact: true })
    .click()
  await page
    .getByRole("button", { name: "Add human claim", exact: true })
    .click()
  await page
    .locator('[data-claim-position="4"]')
    .getByRole("button", { name: "Remove human claim" })
    .click()
  await expect(
    page.getByTestId("human-claims").locator("[data-claim-position]"),
  ).toHaveCount(2)

  await expect(
    page.getByRole("button", { name: "View source", exact: true }),
  ).toHaveCount(0)
  const scrollPositions = await page
    .locator("main.overflow-y-auto")
    .evaluate((element) => {
      const pane = element.querySelector(
        '[aria-label="Source and human claim editor"]',
      )!
      const claims = element.querySelector(
        '[data-testid="claim-correction-list"]',
      )!
      element.scrollTop = 250
      const first = {
        source: pane.getBoundingClientRect().top,
        claims: claims.getBoundingClientRect().top,
      }
      element.scrollTop = 400
      const second = {
        source: pane.getBoundingClientRect().top,
        claims: claims.getBoundingClientRect().top,
      }
      const sourcePane = pane as HTMLElement
      const hasSourceOverflow =
        sourcePane.scrollHeight > sourcePane.clientHeight
      sourcePane.scrollTop = 0
      sourcePane.scrollTop = 100
      return {
        first,
        second,
        hasSourceOverflow,
        sourceScroll: sourcePane.scrollTop,
        claimsAfterSourceScroll: claims.getBoundingClientRect().top,
      }
    })
  expect(
    Math.abs(scrollPositions.first.source - scrollPositions.second.source),
  ).toBeLessThan(2)
  expect(scrollPositions.second.claims).toBeLessThan(
    scrollPositions.first.claims,
  )

  expect(scrollPositions.hasSourceOverflow).toBe(true)
  expect(scrollPositions.sourceScroll).toBe(100)
  expect(scrollPositions.claimsAfterSourceScroll).toBe(
    scrollPositions.second.claims,
  )

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
  await page.getByRole("button", { name: "Save review" }).click()
  await expect(
    page.getByRole("button", { name: "Saving review" }),
  ).toBeDisabled()
  await expect(
    firstLabels.getByRole("button", { name: "incidental" }),
  ).toBeDisabled()
  await expect(
    missing.getByRole("button", { name: "incidental" }),
  ).toBeDisabled()
  await expect(
    page.getByRole("button", { name: "Create human claim" }),
  ).toBeDisabled()
  releaseSubmission()
  await expect(page.getByText("Review saved.")).toBeVisible()
  await expect(page.getByRole("button", { name: "Save review" })).toBeDisabled()

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
        model_labels?: string[]
        human_claims?: Array<{
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
  expect(exportedItem?.correction_reviews?.[0].model_labels).toEqual([
    "incidental",
    "substantive",
  ])
  const finalClaims = exportedItem?.correction_reviews?.[0].human_claims
  expect(finalClaims?.map((claim) => [claim.claim_text, claim.label])).toEqual([
    ["First human assertion", "borderline"],
    ["Second human assertion", "substantive"],
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
    firstLabels.getByRole("button", { name: "incidental" }),
  ).toBeDisabled()
  await expect(
    missing.getByRole("button", { name: "incidental" }),
  ).toBeDisabled()
  await expect(
    page.getByRole("button", { name: "Create human claim" }),
  ).toBeDisabled()
  await page.screenshot({
    path: "/tmp/amfv-claim-correction-submitted.png",
    fullPage: true,
  })
  expect(exportText).not.toContain("api_key")
  expect(exportText).not.toContain("raw_messages")
})
