import { OpenAPI } from "@/client"
import { getAccessToken } from "@/lib/auth"

export interface NiceDocument {
  document_id: number
  dataset_id: number
  reference: string
  title: string
  section_count: number
  char_count: number
  page_url: string
}

/**
 * Derive the canonical NICE guidance URL for a source document from its stored
 * `external_id`. NICE documents are materialized with an external id of
 * `nice-<reference>` (see `materialize_nice_document` in the backend), and the
 * overview page lives at `https://www.nice.org.uk/guidance/<reference>`. Returns
 * `null` for non-NICE documents so callers can fall back to plain text.
 */
export function niceDocumentUrl(externalId: string | undefined): string | null {
  if (!externalId?.startsWith("nice-")) {
    return null
  }
  const reference = externalId.slice("nice-".length)
  if (!reference) {
    return null
  }
  return `https://www.nice.org.uk/guidance/${reference}`
}

export async function createNiceDocumentFromUrl(
  datasetId: number | null,
  url: string,
): Promise<NiceDocument> {
  const token = getAccessToken() || ""
  const response = await fetch(
    `${OpenAPI.BASE}/api/v1/nice/recommendation-url`,
    {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
      body: JSON.stringify({ dataset_id: datasetId, url }),
    },
  )

  if (!response.ok) {
    let detail = `Request failed (${response.status})`
    try {
      const body = await response.json()
      if (body?.detail) detail = body.detail
    } catch {
      // non-JSON error body; keep the default message
    }
    throw new Error(detail)
  }

  return response.json()
}
