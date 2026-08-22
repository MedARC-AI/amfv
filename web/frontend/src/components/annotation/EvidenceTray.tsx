import { ArrowDown, ArrowUp, ExternalLink, X } from "lucide-react"

import type { EvidenceSpan } from "@/client"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"

export type EvidenceTraySpan = EvidenceSpan & {
  id: string
  label?: string
  factUuid?: string
}

type EvidenceTrayProps = {
  spans: EvidenceTraySpan[]
  title?: string
  emptyLabel?: string
  onLocate?: (span: EvidenceTraySpan) => void
  onRemove: (id: string) => void
  onMove: (id: string, direction: "up" | "down") => void
  className?: string
}

export function EvidenceTray({
  spans,
  title = "Evidence",
  emptyLabel = "No evidence selected",
  onLocate,
  onRemove,
  onMove,
  className,
}: EvidenceTrayProps) {
  return (
    <section className={cn("space-y-3", className)}>
      <div className="text-sm font-medium">{title}</div>
      {spans.length === 0 ? (
        <div className="rounded-md border border-dashed p-3 text-sm text-muted-foreground">
          {emptyLabel}
        </div>
      ) : (
        <ol className="space-y-2">
          {spans.map((span, index) => (
            <li
              key={span.id}
              className="grid grid-cols-[1fr_auto] gap-3 rounded-md border p-3"
            >
              <div className="min-w-0 space-y-1">
                <div className="text-xs text-muted-foreground">
                  {span.label ?? `Chunk ${span.chunk_id}`} · {span.start}-
                  {span.end}
                </div>
                <blockquote className="break-words text-sm">
                  {span.text}
                </blockquote>
              </div>
              <div className="flex items-start gap-1">
                {onLocate ? (
                  <Button
                    aria-label="Show evidence in document"
                    onClick={() => onLocate(span)}
                    size="icon-sm"
                    type="button"
                    variant="ghost"
                  >
                    <ExternalLink />
                  </Button>
                ) : null}
                <Button
                  aria-label="Move evidence up"
                  disabled={index === 0}
                  onClick={() => onMove(span.id, "up")}
                  size="icon-sm"
                  type="button"
                  variant="ghost"
                >
                  <ArrowUp />
                </Button>
                <Button
                  aria-label="Move evidence down"
                  disabled={index === spans.length - 1}
                  onClick={() => onMove(span.id, "down")}
                  size="icon-sm"
                  type="button"
                  variant="ghost"
                >
                  <ArrowDown />
                </Button>
                <Button
                  aria-label="Remove evidence"
                  onClick={() => onRemove(span.id)}
                  size="icon-sm"
                  type="button"
                  variant="ghost"
                >
                  <X />
                </Button>
              </div>
            </li>
          ))}
        </ol>
      )}
    </section>
  )
}
