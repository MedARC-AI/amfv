import * as React from "react"

/**
 * Owns one document's pointer-drag session. It is deliberately instance based:
 * separate viewers must never leak a drag or a deduped block into one another.
 */
export class DocumentSelectionController {
  #dragging = false
  #visitedKeys = new Set<string>()

  constructor(readonly documentId: number | undefined = undefined) {}

  begin(key: string): boolean {
    this.#dragging = true
    this.#visitedKeys.clear()
    return this.visit(key)
  }

  visit(key: string): boolean {
    if (!this.#dragging || this.#visitedKeys.has(key)) {
      return false
    }
    this.#visitedKeys.add(key)
    return true
  }

  end(): void {
    this.#dragging = false
    this.#visitedKeys.clear()
  }

  get isDragging(): boolean {
    return this.#dragging
  }
}

const DocumentSelectionControllerContext =
  React.createContext<DocumentSelectionController | null>(null)

export function DocumentSelectionControllerProvider({
  children,
  controller,
}: {
  children: React.ReactNode
  controller: DocumentSelectionController
}) {
  return React.createElement(
    DocumentSelectionControllerContext.Provider,
    { value: controller },
    children,
  )
}

export function useDocumentSelectionController(): DocumentSelectionController | null {
  return React.useContext(DocumentSelectionControllerContext)
}

/** Alt is reserved for document search selection on pointer and keyboard input. */
export function isSearchSelectionGesture(event: unknown): boolean {
  return (
    event !== null &&
    typeof event === "object" &&
    "altKey" in event &&
    Boolean((event as { altKey?: unknown }).altKey)
  )
}
