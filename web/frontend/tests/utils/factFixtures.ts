import { execFileSync } from "node:child_process"
import path from "node:path"
import { fileURLToPath } from "node:url"
import type { Page } from "@playwright/test"

const directory = path.dirname(fileURLToPath(import.meta.url))
export function factFixture(args: Record<string, unknown>) {
  return JSON.parse(
    execFileSync(
      "uv",
      [
        "run",
        "python",
        "-c",
        "import runpy, sys; runpy.run_path(sys.argv[1])",
        path.join(directory, "factFixtures.py"),
      ],
      {
        cwd: path.resolve(directory, "../../../backend"),
        input: JSON.stringify(args),
        encoding: "utf8",
        env: process.env,
      },
    ),
  ) as {
    reviewer: string
    author: string
    user_id: string
    datasets: { id: number; name: string }[]
    tasks: number[]
  }
}
export async function loginFactUser(page: Page, email: string) {
  await page.goto("/login")
  // A fresh context has no identity. Tests using the default admin context clear it first.
  if (!page.url().includes("/login")) {
    await page.evaluate(() => localStorage.removeItem("access_token"))
    await page.goto("/login")
  }
  await page.getByTestId("email-input").fill(email)
  await page.getByTestId("password-input").fill("recovery-password")
  await page.getByRole("button", { name: "Log In", exact: true }).click()
  await page.waitForURL("/")
}
export async function factHeaders(page: Page) {
  const token = await page.evaluate(() => localStorage.getItem("access_token"))
  return { Authorization: `Bearer ${token}` }
}
export const factApi = process.env.VITE_API_URL ?? "http://127.0.0.1:8000"
