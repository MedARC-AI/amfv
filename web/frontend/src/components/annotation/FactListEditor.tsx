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
  className?: string
}

const polarityLabels: Record<FactPolarity, string> = {
  SHOULD_LIST: "Should list",
  SHOULD_NOT_LIST: "Should not list",
}

function newFact(position: number): FactDraft {
  return {
    fact_uuid:
      globalThis.crypto?.randomUUID?.() ??
      `fact-${Date.now().toString(36)}-${position}`,
    fact_text: "",
    polarity: "SHOULD_LIST",
    position,
    provenance_spans: [],
  }
}

function normalizePositions(facts: FactDraft[]): FactDraft[] {
  return facts.map((fact, position) => ({ ...fact, position }))
}

export function FactListEditor({
  facts,
  onChange,
  className,
}: FactListEditorProps) {
  const orderedFacts = [...facts].sort(
    (left, right) => left.position - right.position,
  )

  const updateFact = (factUuid: string, patch: Partial<FactDraft>) => {
    onChange(
      normalizePositions(
        orderedFacts.map((fact) =>
          fact.fact_uuid === factUuid ? { ...fact, ...patch } : fact,
        ),
      ),
    )
  }

  const moveFact = (factUuid: string, direction: "up" | "down") => {
    const index = orderedFacts.findIndex((fact) => fact.fact_uuid === factUuid)
    const targetIndex = direction === "up" ? index - 1 : index + 1
    if (index < 0 || targetIndex < 0 || targetIndex >= orderedFacts.length) {
      return
    }
    const nextFacts = [...orderedFacts]
    const [fact] = nextFacts.splice(index, 1)
    nextFacts.splice(targetIndex, 0, fact)
    onChange(normalizePositions(nextFacts))
  }

  const removeFact = (factUuid: string) => {
    onChange(
      normalizePositions(
        orderedFacts.filter((fact) => fact.fact_uuid !== factUuid),
      ),
    )
  }

  return (
    <section className={cn("space-y-3", className)}>
      <div className="flex items-center justify-between gap-3">
        <div className="text-sm font-medium">Facts</div>
        <Button
          onClick={() =>
            onChange([...orderedFacts, newFact(orderedFacts.length)])
          }
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
            <li key={fact.fact_uuid} className="rounded-md border p-3">
              <div className="grid gap-3 md:grid-cols-[1fr_12rem_auto]">
                <div className="space-y-2">
                  <Label htmlFor={`${fact.fact_uuid}-text`}>
                    Fact {index + 1}
                  </Label>
                  <Input
                    id={`${fact.fact_uuid}-text`}
                    onChange={(event) =>
                      updateFact(fact.fact_uuid, {
                        fact_text: event.target.value,
                      })
                    }
                    value={fact.fact_text}
                  />
                </div>
                <div className="space-y-2">
                  <Label htmlFor={`${fact.fact_uuid}-polarity`}>Polarity</Label>
                  <Select
                    onValueChange={(polarity: FactPolarity) =>
                      updateFact(fact.fact_uuid, { polarity })
                    }
                    value={fact.polarity}
                  >
                    <SelectTrigger id={`${fact.fact_uuid}-polarity`}>
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
                    disabled={index === 0}
                    onClick={() => moveFact(fact.fact_uuid, "up")}
                    size="icon-sm"
                    type="button"
                    variant="ghost"
                  >
                    <ArrowUp />
                  </Button>
                  <Button
                    aria-label="Move fact down"
                    disabled={index === orderedFacts.length - 1}
                    onClick={() => moveFact(fact.fact_uuid, "down")}
                    size="icon-sm"
                    type="button"
                    variant="ghost"
                  >
                    <ArrowDown />
                  </Button>
                  <Button
                    aria-label="Remove fact"
                    onClick={() => removeFact(fact.fact_uuid)}
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
