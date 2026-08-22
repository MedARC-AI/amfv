import { mkdtemp, mkdir, readdir, readFile, rm } from "node:fs/promises"
import { tmpdir } from "node:os"
import { join } from "node:path"

const mode = process.argv[2]
const rootDir = join(import.meta.dir, "..")
const sourceDir = join(rootDir, "backend/app/email-templates/src")
const buildDir = join(rootDir, "backend/app/email-templates/build")

if (mode !== "build" && mode !== "check") {
  throw new Error("Usage: bun ./scripts/email-templates.mjs <build|check>")
}

const sources = (await readdir(sourceDir))
  .filter((name) => name.endsWith(".mjml"))
  .sort()

if (sources.length === 0) {
  throw new Error("No MJML sources were found")
}

const outputName = (sourceName) => `${sourceName.slice(0, -5)}.html`

async function render(sourceName, outputDir) {
  const child = Bun.spawn(
    [
      process.execPath,
      "x",
      "--bun",
      "mjml",
      join(sourceDir, sourceName),
      "--output",
      join(outputDir, outputName(sourceName)),
    ],
    { stderr: "inherit", stdout: "inherit" },
  )
  if ((await child.exited) !== 0) {
    throw new Error(`MJML compilation failed for ${sourceName}`)
  }
}

if (mode === "build") {
  await mkdir(buildDir, { recursive: true })
  await Promise.all(sources.map((sourceName) => render(sourceName, buildDir)))
} else {
  const checkDir = await mkdtemp(join(tmpdir(), "amfv-mjml-"))
  try {
    await Promise.all(sources.map((sourceName) => render(sourceName, checkDir)))
    const expectedOutputs = new Set(sources.map(outputName))
    const buildOutputs = (await readdir(buildDir))
      .filter((name) => name.endsWith(".html"))
      .sort()
    const unexpectedOutputs = buildOutputs.filter(
      (output) => !expectedOutputs.has(output),
    )
    if (unexpectedOutputs.length > 0) {
      throw new Error(
        `Unexpected generated email templates: ${unexpectedOutputs.join(", ")}`,
      )
    }
    for (const sourceName of sources) {
      const output = outputName(sourceName)
      const [generated, tracked] = await Promise.all([
        readFile(join(checkDir, output)),
        readFile(join(buildDir, output)),
      ])
      if (!generated.equals(tracked)) {
        throw new Error(
          `Generated email template is stale: backend/app/email-templates/build/${output}`,
        )
      }
    }
  } finally {
    await rm(checkDir, { force: true, recursive: true })
  }
}
