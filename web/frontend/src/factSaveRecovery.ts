import type {
  FactDecompSaveCommand,
  FactDecompSaveReceiptResponse,
} from "@/client"

export type FactSaveCommandKind = "draft" | "submit"

function valuesMatch(left: unknown, right: unknown): boolean {
  if (Object.is(left, right)) {
    return true
  }
  if (Array.isArray(left) || Array.isArray(right)) {
    return (
      Array.isArray(left) &&
      Array.isArray(right) &&
      left.length === right.length &&
      left.every((value, index) => valuesMatch(value, right[index]))
    )
  }
  if (
    typeof left !== "object" ||
    left === null ||
    typeof right !== "object" ||
    right === null
  ) {
    return false
  }
  const leftRecord = left as Record<string, unknown>
  const rightRecord = right as Record<string, unknown>
  const leftKeys = Object.keys(leftRecord).sort()
  const rightKeys = Object.keys(rightRecord).sort()
  return (
    leftKeys.length === rightKeys.length &&
    leftKeys.every(
      (key, index) =>
        key === rightKeys[index] &&
        valuesMatch(leftRecord[key], rightRecord[key]),
    )
  )
}

function receiptRequest(command: FactDecompSaveCommand) {
  return {
    dataset_id: command.dataset_id,
    document_id: command.document_id ?? null,
    expected_item_revision: command.expected_item_revision ?? null,
    facts: command.facts,
    item_id: command.item_id ?? null,
    source_text: command.source_text,
  }
}

/** Verify a durable receipt before presenting an uncertain fact write as saved. */
export function factSaveReceiptMatchesCommand(
  receipt: FactDecompSaveReceiptResponse,
  command: FactDecompSaveCommand,
  commandKind: FactSaveCommandKind,
): boolean {
  const expectedRevision =
    command.expected_item_revision === null ||
    command.expected_item_revision === undefined
      ? 1
      : command.expected_item_revision + 1
  const expectedStatus = commandKind === "draft" ? "DRAFT" : "SUBMITTED"
  const expectedItemId = command.item_id ?? null
  const response = receipt.response

  return (
    receipt.request_id === command.request_id &&
    receipt.command === commandKind &&
    valuesMatch(receipt.request, receiptRequest(command)) &&
    response.dataset_id === command.dataset_id &&
    response.document_id === (command.document_id ?? null) &&
    response.eval_type === "FACT_DECOMP" &&
    response.status === expectedStatus &&
    response.prompt_text === command.source_text &&
    valuesMatch(response.facts, command.facts) &&
    response.item_revision === expectedRevision &&
    (expectedItemId === null || response.id === expectedItemId)
  )
}
