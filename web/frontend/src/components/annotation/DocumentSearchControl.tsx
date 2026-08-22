import { ChevronDown, ChevronUp, Search, X } from "lucide-react"
import * as React from "react"

import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"

type DocumentSearchControlProps = {
  activeIndex: number
  onActiveIndexChange: (index: number) => void
  onOpenChange?: (open: boolean) => void
  onQueryChange: (query: string) => void
  open?: boolean
  query: string
  totalMatches: number
}

export function DocumentSearchControl({
  activeIndex,
  onActiveIndexChange,
  onOpenChange,
  onQueryChange,
  open,
  query,
  totalMatches,
}: DocumentSearchControlProps) {
  const [draftQuery, setDraftQuery] = React.useState(query)
  const [internalOpen, setInternalOpen] = React.useState(false)
  const inputRef = React.useRef<HTMLInputElement>(null)
  const searchOpen = open ?? internalOpen
  const setSearchOpen = onOpenChange ?? setInternalOpen

  React.useEffect(() => {
    setDraftQuery(query)
  }, [query])

  React.useEffect(() => {
    if (!searchOpen) {
      return
    }
    window.setTimeout(() => inputRef.current?.focus(), 0)
  }, [searchOpen])

  React.useEffect(() => {
    if (draftQuery === query) {
      return
    }
    const timeout = window.setTimeout(() => {
      onQueryChange(draftQuery)
      onActiveIndexChange(0)
    }, 150)
    return () => window.clearTimeout(timeout)
  }, [draftQuery, onActiveIndexChange, onQueryChange, query])

  const hasMatches = totalMatches > 0
  const currentDisplay = hasMatches ? activeIndex + 1 : 0

  const goToNext = React.useCallback(() => {
    if (!hasMatches) {
      return
    }
    onActiveIndexChange((activeIndex + 1) % totalMatches)
  }, [activeIndex, hasMatches, onActiveIndexChange, totalMatches])

  const goToPrevious = React.useCallback(() => {
    if (!hasMatches) {
      return
    }
    onActiveIndexChange((activeIndex - 1 + totalMatches) % totalMatches)
  }, [activeIndex, hasMatches, onActiveIndexChange, totalMatches])

  const clearAndClose = React.useCallback(() => {
    setDraftQuery("")
    onQueryChange("")
    onActiveIndexChange(0)
    setSearchOpen(false)
  }, [onActiveIndexChange, onQueryChange, setSearchOpen])

  return (
    <div className="relative">
      <Button
        aria-expanded={searchOpen}
        aria-label="Search document text"
        onClick={() => setSearchOpen(!searchOpen)}
        size="icon-sm"
        type="button"
        variant={draftQuery ? "secondary" : "outline"}
      >
        <Search />
      </Button>
      {searchOpen ? (
        <div className="absolute right-10 top-0 z-20 flex w-80 items-center gap-1 rounded-md border bg-background p-2 shadow-lg">
          <Search
            aria-hidden
            className="size-4 shrink-0 text-muted-foreground"
          />
          <Input
            aria-label="Search document text"
            className="h-8 min-w-0 flex-1"
            onChange={(event) => {
              setDraftQuery(event.target.value)
            }}
            onKeyDown={(event) => {
              if (event.key === "Enter") {
                event.preventDefault()
                if (draftQuery !== query) {
                  onQueryChange(draftQuery)
                  onActiveIndexChange(0)
                  return
                }
                if (event.shiftKey) {
                  goToPrevious()
                } else {
                  goToNext()
                }
              }
              if (event.key === "Escape") {
                event.preventDefault()
                clearAndClose()
              }
            }}
            placeholder="Search text..."
            ref={inputRef}
            type="search"
            value={draftQuery}
          />
          <div className="min-w-10 text-center text-xs tabular-nums text-muted-foreground">
            {currentDisplay}/{totalMatches}
          </div>
          <Button
            aria-label="Previous search match"
            disabled={!hasMatches}
            onClick={goToPrevious}
            size="icon-sm"
            type="button"
            variant="ghost"
          >
            <ChevronUp />
          </Button>
          <Button
            aria-label="Next search match"
            disabled={!hasMatches}
            onClick={goToNext}
            size="icon-sm"
            type="button"
            variant="ghost"
          >
            <ChevronDown />
          </Button>
          <Button
            aria-label="Clear document search"
            onClick={clearAndClose}
            size="icon-sm"
            type="button"
            variant="ghost"
          >
            <X />
          </Button>
        </div>
      ) : null}
    </div>
  )
}
