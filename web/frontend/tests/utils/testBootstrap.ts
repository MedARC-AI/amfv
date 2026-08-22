import { execFileSync } from "node:child_process"
import path from "node:path"
import { fileURLToPath } from "node:url"

type BrowserTestUser = {
  id: string
  email: string
  full_name: string
}

const currentDirectory = path.dirname(fileURLToPath(import.meta.url))
const backendDirectory = path.resolve(currentDirectory, "../../../backend")

export const createUser = async ({
  email,
  password,
}: {
  email: string
  password: string
}): Promise<BrowserTestUser> => {
  const output = execFileSync(
    "uv",
    [
      "run",
      "python",
      "-m",
      "app.scripts.seed_e2e",
      "create-user",
      "--email",
      email,
      "--password",
      password,
    ],
    {
      cwd: backendDirectory,
      encoding: "utf8",
      env: process.env,
    },
  )
  return JSON.parse(output) as BrowserTestUser
}
