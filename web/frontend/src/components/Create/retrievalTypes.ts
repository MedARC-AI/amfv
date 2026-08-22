import type { RetrievalCategory } from "@/client"
import type { EvidenceTraySpan } from "@/components/annotation/EvidenceTray"

export type RetrievalEvidenceSpan = EvidenceTraySpan & {
  sourceDocumentId: number
  confirmed?: boolean
}

export type CommittedEvidenceSpan = RetrievalEvidenceSpan & {
  itemId?: string
  markClass: string
  blockClass: string
}

export type RetrievalFormValues = {
  question: string
  expectedAnswer: string
  whyNotAnswerable: string
}

export type AddedRetrievalEvalItem = RetrievalFormValues & {
  id: string
  category: RetrievalCategory
  documentIds: number[]
  goldSpans: RetrievalEvidenceSpan[]
  trapSpans: RetrievalEvidenceSpan[]
}
