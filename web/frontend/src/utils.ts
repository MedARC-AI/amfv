import type { ApiError } from "./client"

export class ProductMessageError extends Error {
  constructor(message: string) {
    super(message)
    this.name = "ProductMessageError"
  }
}

type ErrorResponse = {
  body?: unknown
  response?: { data?: unknown; status?: unknown }
  status?: unknown
}

function isErrorResponse(error: unknown): error is ErrorResponse {
  return Boolean(error) && typeof error === "object"
}

function responseBody(error: ErrorResponse): unknown {
  return error.body ?? error.response?.data
}

function responseStatus(error: ErrorResponse): number | null {
  const status = error.status ?? error.response?.status
  return typeof status === "number" ? status : null
}

/**
 * Identify an expected API status without exposing any response content to the
 * UI. Callers can use this for a documented workflow branch such as an empty
 * readonly assignment lookup.
 */
export function hasApiErrorStatus(error: unknown, status: number): boolean {
  return isErrorResponse(error) && responseStatus(error) === status
}

function validationMessage(body: unknown): string | null {
  if (!body || typeof body !== "object" || !("detail" in body)) {
    return null
  }
  const detail = (body as { detail?: unknown }).detail
  if (!Array.isArray(detail)) {
    return null
  }
  const messages = detail.flatMap((entry) => {
    if (!entry || typeof entry !== "object") {
      return []
    }
    const { loc, msg } = entry as { loc?: unknown; msg?: unknown }
    if (!Array.isArray(loc) || typeof msg !== "string") {
      return []
    }
    const field = loc
      .filter(
        (part): part is string | number =>
          (typeof part === "string" || typeof part === "number") &&
          part !== "body",
      )
      .join(".")
    return field ? [`${field}: ${msg}`] : [msg]
  })

  return messages.length > 0 ? messages.join("\n") : null
}

function statusMessage(status: number | null): string {
  if (status === 401) {
    return "Your session has expired. Sign in and try again."
  }
  if (status === 403) {
    return "You do not have permission to perform that action."
  }
  if (status === 404) {
    return "That item is no longer available. Refresh and try again."
  }
  if (status === 409) {
    return "This item changed elsewhere. Refresh it before trying again."
  }
  if (status === 422) {
    return "Some fields need attention."
  }
  if (status !== null && status >= 500) {
    return "The service could not complete the request. Please try again."
  }
  return "Something went wrong. Please try again."
}

/**
 * Map request failures to bounded product copy. Structured validation errors
 * retain their field names, while arbitrary response bodies never reach UI.
 */
export function apiErrorMessage(err: unknown): string {
  if (err instanceof ProductMessageError) {
    return err.message
  }
  if (isErrorResponse(err)) {
    const message = validationMessage(responseBody(err))
    if (message) {
      return message
    }
    return statusMessage(responseStatus(err))
  }
  return statusMessage(null)
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
