import { AxiosError } from "axios"
import type { ApiError } from "./client"

function detailMessage(detail: unknown): string | null {
  if (typeof detail === "string") {
    return detail
  }
  if (!Array.isArray(detail) || detail.length === 0) {
    return null
  }
  const messages = detail
    .map((entry) => {
      if (typeof entry === "string") {
        return entry
      }
      if (entry && typeof entry === "object") {
        if ("message" in entry) {
          return String((entry as { message: unknown }).message)
        }
        if ("msg" in entry) {
          return String((entry as { msg: unknown }).msg)
        }
      }
      return null
    })
    .filter((message): message is string => Boolean(message))

  return messages.length > 0 ? messages.join("\n") : null
}

export function apiErrorMessage(err: unknown): string {
  if (err instanceof AxiosError) {
    return err.message
  }

  if (err && typeof err === "object" && "body" in err) {
    const body = (err as { body?: { detail?: unknown } }).body
    const message = detailMessage(body?.detail)
    if (message) {
      return message
    }
  }
  if (err instanceof Error) {
    return err.message
  }
  return "Something went wrong."
}

/**
 * Return a persisted source URL when the backend supplied one.
 *
 * This accepts the shared summary/detail provenance shape and never infers a
 * source-specific URL from an external identifier.
 */
export function sourceDocumentUrl(
  document: { source_url?: string | null } | null | undefined,
): string | null {
  const url = document?.source_url
  return typeof url === "string" && url.length > 0 ? url : null
}

export const handleError = function (
  this: (msg: string) => void,
  err: ApiError,
) {
  const errorMessage = apiErrorMessage(err)
  this(errorMessage)
}

export const getInitials = (name: string): string => {
  return name
    .split(" ")
    .slice(0, 2)
    .map((word) => word[0])
    .join("")
    .toUpperCase()
}
