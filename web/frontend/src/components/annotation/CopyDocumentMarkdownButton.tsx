import { Check, Copy } from "lucide-react"
import * as React from "react"

import type { ChunkSummary, DocumentDetail } from "@/client"
import { Button } from "@/components/ui/button"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"

type CopyDocumentMarkdownButtonProps = {
  chunks: ChunkSummary[]
  document?: DocumentDetail
  sourceUrl?: string | null
}

type CopiedValue = string | null
type CopyFn = (text: string) => Promise<boolean>

function formatDocumentMarkdown({
  chunks,
  document,
  sourceUrl,
}: CopyDocumentMarkdownButtonProps): string {
  const title = document?.title?.trim() || "Retrieval source"
  const sections = [`# ${title}`]

  if (sourceUrl) {
    sections.push(`[Source](${sourceUrl})`)
  }

  sections.push(
    ...chunks
      .slice()
      .sort((first, second) => first.position - second.position)
      .map((chunk) => {
        const label = `## Chunk ${chunk.position + 1}`
        return `${label}\n\n${chunk.text.trim()}`
      }),
  )

  return `${sections.join("\n\n")}\n`
}

export function CopyDocumentMarkdownButton({
  chunks,
  document,
  sourceUrl,
}: CopyDocumentMarkdownButtonProps) {
  const [copiedText, setCopiedText] = React.useState<CopiedValue>(null)
  const copyToClipboard: CopyFn = React.useCallback(async (text) => {
    if (!navigator?.clipboard) {
      console.warn("Clipboard not supported")
      return false
    }

    try {
      await navigator.clipboard.writeText(text)
      setCopiedText(text)
      setTimeout(() => setCopiedText(null), 2000)
      return true
    } catch (error) {
      console.warn("Copy failed", error)
      setCopiedText(null)
      return false
    }
  }, [])
  const markdown = React.useMemo(
    () => formatDocumentMarkdown({ chunks, document, sourceUrl }),
    [chunks, document, sourceUrl],
  )
  const copied = copiedText === markdown
  const disabled = chunks.length === 0
  const label = copied ? "Copied Markdown" : "Copy Markdown"

  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Button
          aria-label={label}
          disabled={disabled}
          onClick={() => {
            void copyToClipboard(markdown)
          }}
          size="icon-sm"
          type="button"
          variant={copied ? "secondary" : "outline"}
        >
          {copied ? <Check /> : <Copy />}
        </Button>
      </TooltipTrigger>
      <TooltipContent side="left">{label}</TooltipContent>
    </Tooltip>
  )
}
