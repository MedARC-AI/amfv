import { expect, type Request, test } from "@playwright/test"

import { randomEmail } from "./utils/random"

type InviteBootstrapAudit = {
  replacements: Array<{ at: number; url: string }>
  requestStarts: Array<{ at: number; url: string }>
}

// A normal Playwright trace records request bodies. This focused security test
// attaches only its redacted audit counts so invite and password secrets never
// enter test artifacts.
test.use({ trace: "off" })

test("admin-issued invite is scrubbed before body-only token requests", async ({
  browser,
  page,
}, testInfo) => {
  await page.goto("/admin")
  await page.getByRole("button", { name: "Create invite" }).click()

  const inviteLink = await page.getByLabel("Signup link").inputValue()
  const issuedUrl = new URL(inviteLink)
  const inviteToken = new URLSearchParams(issuedUrl.hash.slice(1)).get("token")
  expect(Boolean(inviteToken)).toBe(true)
  expect(issuedUrl.pathname).toBe("/signup")
  expect(issuedUrl.search).toBe("")

  const context = await browser.newContext({
    storageState: { cookies: [], origins: [] },
  })
  const signupPage = await context.newPage()
  const requests: Request[] = []
  signupPage.on("request", (request) => requests.push(request))
  await signupPage.addInitScript(() => {
    const audit: InviteBootstrapAudit = {
      replacements: [],
      requestStarts: [],
    }
    Object.defineProperty(window, "__inviteBootstrapAudit", { value: audit })

    const replaceState = window.history.replaceState.bind(window.history)
    window.history.replaceState = (...args) => {
      audit.replacements.push({
        at: performance.now(),
        url: String(args[2] ?? window.location.href),
      })
      return replaceState(...args)
    }

    const originalFetch = window.fetch.bind(window)
    Object.defineProperty(window, "fetch", {
      configurable: true,
      value: (input: RequestInfo | URL, init?: RequestInit) => {
        audit.requestStarts.push({
          at: performance.now(),
          url: String(input),
        })
        return originalFetch(input, init)
      },
    })

    const requestUrls = new WeakMap<XMLHttpRequest, string>()
    const originalOpen = XMLHttpRequest.prototype.open
    Object.defineProperty(XMLHttpRequest.prototype, "open", {
      configurable: true,
      value: function (...args: unknown[]) {
        const [, url] = args as [string, string | URL]
        requestUrls.set(this as XMLHttpRequest, String(url))
        return Reflect.apply(originalOpen, this, args)
      },
    })
    const originalSend = XMLHttpRequest.prototype.send
    Object.defineProperty(XMLHttpRequest.prototype, "send", {
      configurable: true,
      value: function (...args: unknown[]) {
        audit.requestStarts.push({
          at: performance.now(),
          url: requestUrls.get(this as XMLHttpRequest) ?? "",
        })
        return Reflect.apply(originalSend, this, args)
      },
    })
  })

  await signupPage.goto(inviteLink)
  await expect(
    signupPage.getByRole("heading", { name: "Create your account" }),
  ).toBeVisible()
  expect(signupPage.url()).toBe(`${issuedUrl.origin}/signup`)

  const bootstrapAudit = await signupPage.evaluate(
    () =>
      (
        window as unknown as Window & {
          __inviteBootstrapAudit: InviteBootstrapAudit
        }
      ).__inviteBootstrapAudit,
  )
  const cleanReplacement = bootstrapAudit.replacements.find(
    ({ url }) => url === "/signup",
  )
  const previewStart = bootstrapAudit.requestStarts.find(({ url }) =>
    url.endsWith("/api/v1/auth/invites/preview"),
  )
  expect(Boolean(cleanReplacement)).toBe(true)
  expect(Boolean(previewStart)).toBe(true)
  expect(
    (cleanReplacement?.at ?? Number.POSITIVE_INFINITY) <
      (previewStart?.at ?? 0),
  ).toBe(true)

  const password = `Invite-${Date.now()}-password`
  await signupPage.getByPlaceholder("user@example.com").fill(randomEmail())
  await signupPage.getByPlaceholder("Password").first().fill(password)
  await signupPage.getByPlaceholder("Password").last().fill(password)
  await signupPage.getByRole("combobox", { name: "Profession" }).click()
  await signupPage.getByRole("option", { name: "Doctor / physician" }).click()
  await signupPage.getByRole("button", { name: "Create account" }).click()
  await signupPage.waitForURL("/")

  const requestFacts = await Promise.all(
    requests.map(async (request) => ({
      method: request.method(),
      url: request.url(),
      headers: await request.allHeaders(),
      tokenInBody: request.postData()?.includes(inviteToken ?? "") ?? false,
    })),
  )
  const redactedAudit = {
    tokenInUrlCount: requestFacts.filter(({ url }) =>
      url.includes(inviteToken ?? ""),
    ).length,
    tokenInHeaderCount: requestFacts.filter(({ headers }) =>
      Object.values(headers).some((value) => value.includes(inviteToken ?? "")),
    ).length,
    tokenInBodyCount: requestFacts.filter(({ tokenInBody }) => tokenInBody)
      .length,
    bodyOnlyPostCount: requestFacts.filter(
      ({ method, tokenInBody, url }) =>
        tokenInBody &&
        method === "POST" &&
        (url.endsWith("/api/v1/auth/invites/preview") ||
          url.endsWith("/api/v1/auth/signup")),
    ).length,
    fragmentScrubbedBeforePreview: Boolean(
      cleanReplacement && previewStart && cleanReplacement.at < previewStart.at,
    ),
  }

  expect(redactedAudit).toEqual({
    tokenInUrlCount: 0,
    tokenInHeaderCount: 0,
    tokenInBodyCount: 2,
    bodyOnlyPostCount: 2,
    fragmentScrubbedBeforePreview: true,
  })
  await testInfo.attach("redacted-invite-bootstrap-audit.json", {
    body: JSON.stringify(redactedAudit, null, 2),
    contentType: "application/json",
  })
  await context.close()
})
