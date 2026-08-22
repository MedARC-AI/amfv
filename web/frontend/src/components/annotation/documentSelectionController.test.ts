import { describe, expect, test } from "bun:test"

import {
  DocumentSelectionController,
  isSearchSelectionGesture,
} from "./documentSelectionController"

describe("document selection controller", () => {
  test("keeps simultaneous viewer drag sessions isolated", () => {
    const first = new DocumentSelectionController()
    const second = new DocumentSelectionController()

    expect(first.begin("1:paragraph:0")).toBe(true)
    expect(second.visit("2:paragraph:0")).toBe(false)
    expect(second.begin("2:paragraph:0")).toBe(true)
    expect(first.isDragging).toBe(true)
    expect(second.isDragging).toBe(true)
  })

  test("resets dedupe state after a drag ends so the next gesture may reselect", () => {
    const controller = new DocumentSelectionController()

    expect(controller.begin("1:paragraph:0")).toBe(true)
    expect(controller.visit("1:paragraph:0")).toBe(false)
    controller.end()
    expect(controller.isDragging).toBe(false)
    expect(controller.begin("1:paragraph:0")).toBe(true)
  })

  test("recognizes the keyboard-relevant Alt search gesture without mutating drag state", () => {
    const controller = new DocumentSelectionController()

    expect(isSearchSelectionGesture({ altKey: true })).toBe(true)
    expect(isSearchSelectionGesture({ altKey: false })).toBe(false)
    expect(controller.isDragging).toBe(false)
  })
})
