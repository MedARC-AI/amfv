import { RotateCcw } from "lucide-react"
import * as React from "react"
import type { DocumentDetail, EvidenceSpan } from "@/client"
import { DocumentEvidenceViewer } from "@/components/annotation/DocumentEvidenceViewer"
import type { BlockSelection } from "@/components/annotation/SelectableChunk"
import { SelectableChunk } from "@/components/annotation/SelectableChunk"
import { Button } from "@/components/ui/button"
import type {
  CommittedEvidenceSpan,
  RetrievalEvidenceSpan,
} from "./retrievalTypes"

const EMPTY_EVIDENCE_SPANS: EvidenceSpan[] = []

type RetrievalEvidenceDocumentProps = {
  canUndoEvidence: boolean
  committedSpansByChunk: Map<number, CommittedEvidenceSpan[]>
  document: DocumentDetail | undefined
  onCommittedSelect: (itemId: string) => void
  onConfirmEvidenceSelection?: () => void
  onRemoveEvidence: (span: EvidenceSpan & { id?: string }) => void
  onSelectBlockEvidence: (selection: BlockSelection) => boolean | undefined
  onSelectEvidence: (span: EvidenceSpan) => boolean | undefined
  onUndoEvidence: () => void
  selectedSpansByChunk: Map<number, RetrievalEvidenceSpan[]>
  selectionInstruction: string
  selectionMode: "exact" | "block"
}

/** Retrieval-authoring controls supplied to the shared document presentation. */
export const RetrievalEvidenceDocument = React.memo(
  function RetrievalEvidenceDocument({
    canUndoEvidence,
    committedSpansByChunk,
    document,
    onCommittedSelect,
    onConfirmEvidenceSelection,
    onRemoveEvidence,
    onSelectBlockEvidence,
    onSelectEvidence,
    onUndoEvidence,
    selectedSpansByChunk,
    selectionInstruction,
    selectionMode,
  }: RetrievalEvidenceDocumentProps) {
    if (!document) {
      return (
        <div className="rounded-md border border-dashed p-4 text-sm text-muted-foreground">
          Select a source document to highlight evidence.
        </div>
      )
    }

    return (
      <DocumentEvidenceViewer
        chunks={document.chunks ?? []}
        document={document}
        emptyMessage="No chunks are available for this source document."
        instructions={selectionInstruction}
        toolbarAction={
          <Button
            aria-label="Undo evidence change"
            disabled={!canUndoEvidence}
            onClick={onUndoEvidence}
            size="icon-sm"
            type="button"
            variant="outline"
          >
            <RotateCcw />
          </Button>
        }
      >
        {({
          activeSearchMatchIndex,
          onSearchSelect,
          searchMatchesByChunkId,
          textSizeClass,
        }) =>
          (document.chunks ?? []).map((chunk) => (
            <SelectableChunk
              activeSearchMatchIndex={activeSearchMatchIndex}
              className={textSizeClass}
              chunk={chunk}
              committedSpans={
                committedSpansByChunk.get(chunk.id) ?? EMPTY_EVIDENCE_SPANS
              }
              key={chunk.id}
              onBlockSelect={onSelectBlockEvidence}
              onCommittedSelect={onCommittedSelect}
              onConfirmSelection={onConfirmEvidenceSelection}
              onRemove={onRemoveEvidence}
              onSearchSelect={onSearchSelect}
              onSelect={onSelectEvidence}
              searchMatches={searchMatchesByChunkId.get(chunk.id)}
              selectedSpans={
                selectedSpansByChunk.get(chunk.id) ?? EMPTY_EVIDENCE_SPANS
              }
              selectionMode={selectionMode}
            />
          ))
        }
      </DocumentEvidenceViewer>
    )
  },
)
