import { readFileSync } from "node:fs"
import { join } from "node:path"

const sdkPath = join(import.meta.dir, "..", "src", "client", "sdk.gen.ts")
const sdk = readFileSync(sdkPath, "utf8")
const requiredServices = ["HomeService", "ReviewService", "CreateService", "AdminService"]

const missing = requiredServices.filter(
  (serviceName) => !sdk.includes(`export class ${serviceName}`),
)

if (missing.length > 0) {
  throw new Error(`Generated client is missing services: ${missing.join(", ")}`)
}
