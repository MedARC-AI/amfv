import { describe, expect, test } from "bun:test"

import {
  apiErrorMessage,
  hasApiErrorStatus,
  isAuthoritativeClientError,
  ProductMessageError,
} from "./utils"

describe("bounded product error messages", () => {
  test("keeps structured field validation useful without exposing the raw response", () => {
    expect(
      apiErrorMessage({
        status: 422,
        body: {
          detail: [
            { loc: ["body", "question"], msg: "Field required" },
            { loc: ["body", "evidence", 0], msg: "Invalid span" },
          ],
        },
      }),
    ).toBe("question: Field required\nevidence.0: Invalid span")
  })

  test("never renders arbitrary response bodies or transport messages", () => {
    expect(
      apiErrorMessage({
        status: 500,
        body: { detail: "database password: do not display" },
      }),
    ).toBe("The service could not complete the request. Please try again.")
    expect(apiErrorMessage(new Error("untrusted network detail"))).toBe(
      "Something went wrong. Please try again.",
    )
  })

  test("allows explicit, local recovery guidance", () => {
    expect(
      apiErrorMessage(new ProductMessageError("Saved state is unknown.")),
    ).toBe("Saved state is unknown.")
    expect(
      apiErrorMessage(
        new ProductMessageError(
          "Submission was not recorded. It was not retried automatically.",
        ),
      ),
    ).toBe("Submission was not recorded. It was not retried automatically.")
  })

  test("recognizes a documented empty-state status without reading its body", () => {
    const emptyReadonlyLookup = {
      status: 404,
      body: "No existing review assignment",
    }

    expect(hasApiErrorStatus(emptyReadonlyLookup, 404)).toBe(true)
    expect(hasApiErrorStatus(emptyReadonlyLookup, 409)).toBe(false)
  })

  test("treats only known 4xx responses as authoritative rejections", () => {
    expect(isAuthoritativeClientError({ status: 422 })).toBe(true)
    expect(isAuthoritativeClientError({ status: 500 })).toBe(false)
    expect(isAuthoritativeClientError(new Error("missing status"))).toBe(false)
  })
})
