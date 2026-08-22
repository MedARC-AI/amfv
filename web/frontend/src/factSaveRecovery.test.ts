import { expect, test } from "bun:test"

import type {
  FactDecompSaveCommand,
  FactDecompSaveReceiptResponse,
} from "@/client"
import { factSaveReceiptMatchesCommand } from "./factSaveRecovery"

function command(
  overrides: Partial<FactDecompSaveCommand> = {},
): FactDecompSaveCommand {
  return {
    dataset_id: 9,
    document_id: null,
    expected_item_revision: null,
    facts: [
      {
        fact_uuid: "wanted",
        fact_text: "Baker appears in the source.",
        polarity: "SHOULD_LIST",
        position: 0,
        provenance_spans: [],
      },
    ],
    item_id: null,
    request_id: "fact-save-1",
    source_text: "Baker appears in the source.",
    ...overrides,
  }
}

function receipt(
  saveCommand: FactDecompSaveCommand,
): FactDecompSaveReceiptResponse {
  return {
    command: "draft",
    replayed: true,
    request: {
      dataset_id: saveCommand.dataset_id,
      document_id: saveCommand.document_id ?? null,
      expected_item_revision: saveCommand.expected_item_revision ?? null,
      facts: saveCommand.facts,
      item_id: saveCommand.item_id ?? null,
      source_text: saveCommand.source_text,
    },
    request_hash: "stable-hash",
    request_id: saveCommand.request_id,
    response: {
      dataset_id: saveCommand.dataset_id,
      document_id: saveCommand.document_id ?? null,
      eval_type: "FACT_DECOMP",
      facts: saveCommand.facts,
      id: saveCommand.item_id ?? 44,
      item_revision:
        saveCommand.expected_item_revision === null ||
        saveCommand.expected_item_revision === undefined
          ? 1
          : saveCommand.expected_item_revision + 1,
      prompt_text: saveCommand.source_text,
      status: "DRAFT",
      validation: { flags: [], ok: true },
    },
  }
}

test("accepts only an exact authoritative fact-save receipt", () => {
  const saveCommand = command()

  expect(
    factSaveReceiptMatchesCommand(receipt(saveCommand), saveCommand, "draft"),
  ).toBe(true)
})

test("rejects a mismatched request, content, command, or revision", () => {
  const saveCommand = command({ item_id: 44, expected_item_revision: 2 })
  const savedReceipt = receipt(saveCommand)

  expect(
    factSaveReceiptMatchesCommand(
      { ...savedReceipt, request_id: "different-request" },
      saveCommand,
      "draft",
    ),
  ).toBe(false)
  expect(
    factSaveReceiptMatchesCommand(
      {
        ...savedReceipt,
        request: { ...savedReceipt.request, source_text: "other text" },
      },
      saveCommand,
      "draft",
    ),
  ).toBe(false)
  expect(
    factSaveReceiptMatchesCommand(
      {
        ...savedReceipt,
        response: {
          ...savedReceipt.response,
          facts: [
            {
              ...savedReceipt.response.facts[0],
              fact_text: "different authoritative content",
            },
          ],
        },
      },
      saveCommand,
      "draft",
    ),
  ).toBe(false)
  expect(
    factSaveReceiptMatchesCommand(
      {
        ...savedReceipt,
        response: { ...savedReceipt.response, item_revision: 9 },
      },
      saveCommand,
      "draft",
    ),
  ).toBe(false)
  expect(
    factSaveReceiptMatchesCommand(savedReceipt, saveCommand, "submit"),
  ).toBe(false)
})
