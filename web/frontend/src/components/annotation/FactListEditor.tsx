import { ArrowDown, ArrowUp, Plus, Trash2 } from "lucide-react"

import type { FactDraft, FactPolarity } from "@/client"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { cn } from "@/lib/utils"

type FactListEditorProps = {
  facts: FactDraft[]
  onChange: (facts: FactDraft[]) => void
  selectedFactIndex?: number | null
  onSelectedFactIndexChange?: (index: number | null) => void
  disabled?: boolean
  className?: string
}

/** Keep an index-based selection attached to the same row after reordering. */
export function selectedIndexAfterMove(
  selectedIndex: number | null | undefined,
  index: number,
  targetIndex: number,
): number | null | undefined {
  if (selectedIndex === undefined || selectedIndex === null)
    return selectedIndex
  if (selectedIndex === index) return targetIndex
  if (selectedIndex === targetIndex) return index
  return selectedIndex
}

/** Shift selection past a removed row, or select the next remaining row. */
export function selectedIndexAfterRemove(
  selectedIndex: number | null | undefined,
  removedIndex: number,
  remainingCount: number,
): number | null | undefined {
  if (selectedIndex === undefined || selectedIndex === null)
    return selectedIndex
  if (remainingCount === 0) return null
  if (selectedIndex < removedIndex) return selectedIndex
  if (selectedIndex > removedIndex) return selectedIndex - 1
  return Math.min(removedIndex, remainingCount - 1)
}

const polarityLabels: Record<FactPolarity, string> = {
  SHOULD_LIST: "Should list",
  SHOULD_NOT_LIST: "Should not list",
}

function newFact(): FactDraft {
  return {
    fact_text: "",
    polarity: "SHOULD_LIST",
    provenance_spans: [],
  }
}

export function FactListEditor({
  facts,
  onChange,
  onSelectedFactIndexChange,
  selectedFactIndex,
  className,
  disabled = false,
}: FactListEditorProps) {
  const orderedFacts = facts

  const updateFact = (index: number, patch: Partial<FactDraft>) => {
    onChange(
      orderedFacts.map((fact, factIndex) =>
        factIndex === index ? { ...fact, ...patch } : fact,
      ),
    )
  }

  const moveFact = (index: number, direction: "up" | "down") => {
    const targetIndex = direction === "up" ? index - 1 : index + 1
    if (index < 0 || targetIndex < 0 || targetIndex >= orderedFacts.length) {
      return
    }
    const nextFacts = [...orderedFacts]
    const [fact] = nextFacts.splice(index, 1)
    nextFacts.splice(targetIndex, 0, fact)
    onSelectedFactIndexChange?.(
      selectedIndexAfterMove(selectedFactIndex, index, targetIndex) ?? null,
    )
    onChange(nextFacts)
  }

  const removeFact = (index: number) => {
    const nextFacts = orderedFacts.filter(
      (_fact, factIndex) => factIndex !== index,
    )
    onSelectedFactIndexChange?.(
      selectedIndexAfterRemove(selectedFactIndex, index, nextFacts.length) ??
        null,
    )
    onChange(nextFacts)
  }

  return (
    <section className={cn("space-y-3", className)}>
      <div className="flex items-center justify-between gap-3">
        <div className="text-sm font-medium">Facts</div>
        <Button
          disabled={disabled}
          onClick={() => onChange([...orderedFacts, newFact()])}
          size="sm"
          type="button"
          variant="outline"
        >
          <Plus />
          Add fact
        </Button>
      </div>

      {orderedFacts.length === 0 ? (
        <div className="rounded-md border border-dashed p-3 text-sm text-muted-foreground">
          No facts added
        </div>
      ) : (
        <ol className="space-y-3">
          {orderedFacts.map((fact, index) => (
            <li key={index} className="rounded-md border p-3">
              <div className="grid gap-3 md:grid-cols-[1fr_12rem_auto]">
                <div className="space-y-2">
                  <Label htmlFor={`fact-${index}-text`}>Fact {index + 1}</Label>
                  <Input
                    disabled={disabled}
                    id={`fact-${index}-text`}
                    onChange={(event) =>
                      updateFact(index, {
                        fact_text: event.target.value,
                      })
                    }
                    value={fact.fact_text}
                  />
                </div>
                <div className="space-y-2">
                  <Label htmlFor={`fact-${index}-polarity`}>Polarity</Label>
                  <Select
                    disabled={disabled}
                    onValueChange={(polarity: FactPolarity) =>
                      updateFact(index, { polarity })
                    }
                    value={fact.polarity}
                  >
                    <SelectTrigger id={`fact-${index}-polarity`}>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {Object.entries(polarityLabels).map(([value, label]) => (
                        <SelectItem key={value} value={value}>
                          {label}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
                <div className="flex items-end gap-1">
                  <Button
                    aria-label="Move fact up"
                    disabled={disabled || index === 0}
                    onClick={() => moveFact(index, "up")}
                    size="icon-sm"
                    type="button"
                    variant="ghost"
                  >
                    <ArrowUp />
                  </Button>
                  <Button
                    aria-label="Move fact down"
                    disabled={disabled || index === orderedFacts.length - 1}
                    onClick={() => moveFact(index, "down")}
                    size="icon-sm"
                    type="button"
                    variant="ghost"
                  >
                    <ArrowDown />
                  </Button>
                  <Button
                    aria-label="Remove fact"
                    disabled={disabled}
                    onClick={() => removeFact(index)}
                    size="icon-sm"
                    type="button"
                    variant="ghost"
                  >
                    <Trash2 />
                  </Button>
                </div>
              </div>
            </li>
          ))}
        </ol>
      )}
    </section>
  )
}
