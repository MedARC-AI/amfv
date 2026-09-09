import * as React from "react"
import type { FinalModelClaim, ReviewModelClaim } from "@/client/types.gen"
import { Button } from "@/components/ui/button"
import { evalItemColor } from "@/lib/evalItemPalette"
import type { ClaimResponseSpan } from "./SelectableClaimResponse"

export const CLAIM_LABELS = ["substantive", "incidental", "borderline"] as const
export type ClaimLabel = (typeof CLAIM_LABELS)[number]
export const labelDescriptions: Record<ClaimLabel, string> = {
  substantive:
    "Correctness materially affects information, reasoning, conclusions, or actions",
  incidental: "Correctness has little bearing on the substantive content",
  borderline:
    "Context leaves verification relevance unclear, not factual truth",
}
export type ClaimGroup = {
  id: number
  original?: ReviewModelClaim
  finals: FinalModelClaim[]
}
export function originalFinal(claim: ReviewModelClaim): FinalModelClaim {
  return {
    original_position: claim.position,
    claim_text: claim.claim_text,
    response_spans: claim.response_spans,
    label: claim.proposed_label,
  }
}
function Labels({
  value,
  name,
  onChange,
}: {
  value: ClaimLabel
  name: string
  onChange: (label: ClaimLabel) => void
}) {
  return (
    <fieldset className="flex flex-wrap gap-1">
      <legend className="sr-only">{name} label</legend>
      {CLAIM_LABELS.map((label) => (
        <button
          key={label}
          type="button"
          aria-pressed={label === value}
          title={labelDescriptions[label]}
          className={`rounded border px-2 py-1 text-xs capitalize ${label === value ? "bg-primary text-primary-foreground" : "bg-background"}`}
          onClick={() => onChange(label)}
        >
          {label}
        </button>
      ))}
    </fieldset>
  )
}
function ClaimEditor({
  group,
  locked,
  stagedSpans,
  onChange,
  onEditing,
  onFocus,
}: {
  group: ClaimGroup
  locked: boolean
  stagedSpans: ClaimResponseSpan[]
  onChange: (claims: FinalModelClaim[]) => void
  onEditing: (editing: boolean) => void
  onFocus: () => void
}) {
  const [draft, setDraft] = React.useState<FinalModelClaim[] | null>(null)
  const name = group.original
    ? `Claim ${group.original.position + 1}`
    : "Missing claim"
  const begin = (split: boolean) => {
    const parts = group.finals.map((claim) => ({ ...claim }))
    if (split && parts.length) parts.push({ ...parts[0], claim_text: "" })
    setDraft(parts)
    onEditing(true)
  }
  const finish = () => {
    setDraft(null)
    onEditing(false)
  }
  const patch = (index: number, change: Partial<FinalModelClaim>) =>
    setDraft(
      (current) =>
        current?.map((claim, i) =>
          i === index ? { ...claim, ...change } : claim,
        ) ?? null,
    )
  return (
    <article
      id={`claim-position-${group.id}`}
      data-claim-position={group.id}
      className={`rounded-xl border border-l-4 bg-card p-4 space-y-3 ${evalItemColor(group.id).card}`}
    >
      <button type="button" onClick={onFocus} className="text-sm font-semibold">
        {name} · View source
      </button>
      {group.original && (
        <details>
          <summary className="cursor-pointer text-xs text-muted-foreground">
            Original model claim · Proposed: {group.original.proposed_label}
          </summary>
          <p className="mt-2 text-sm">{group.original.claim_text}</p>
        </details>
      )}
      <fieldset disabled={locked} className="space-y-3 disabled:opacity-70">
        {draft ? (
          <>
            {draft.map((claim, index) => (
              <div
                key={`${group.id}-${index}`}
                className="space-y-2 rounded border p-3"
              >
                <textarea
                  className="w-full rounded border p-2 text-sm"
                  aria-label={`${name} part ${index + 1} text`}
                  maxLength={20000}
                  value={claim.claim_text}
                  onChange={(event) =>
                    patch(index, { claim_text: event.target.value })
                  }
                />
                <Labels
                  name={`${name} part ${index + 1}`}
                  value={claim.label}
                  onChange={(label) => patch(index, { label })}
                />
                <p className="text-xs text-muted-foreground">
                  Source:{" "}
                  {claim.response_spans.map((span) => span.text).join(" … ")}
                </p>
                <Button
                  size="sm"
                  variant="outline"
                  disabled={!stagedSpans.length}
                  onClick={() =>
                    patch(index, { response_spans: [...stagedSpans] })
                  }
                >
                  Use selected source for part {index + 1}
                </Button>
                {draft.length > 1 && (
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() =>
                      setDraft(draft.filter((_, i) => i !== index))
                    }
                  >
                    Remove part {index + 1}
                  </Button>
                )}
              </div>
            ))}
            <div className="flex flex-wrap gap-2">
              <Button
                size="sm"
                variant="outline"
                onClick={() =>
                  setDraft([...draft, { ...draft[0], claim_text: "" }])
                }
              >
                Add split claim
              </Button>
              <Button
                size="sm"
                disabled={draft.some(
                  (claim) =>
                    !claim.claim_text.trim() || !claim.response_spans.length,
                )}
                onClick={() => {
                  onChange(draft)
                  finish()
                }}
              >
                Apply changes
              </Button>
              <Button size="sm" variant="ghost" onClick={finish}>
                Cancel
              </Button>
            </div>
          </>
        ) : (
          <>
            {group.finals.length === 0 ? (
              <p className="text-sm text-muted-foreground">
                Removed from final decomposition.
              </p>
            ) : (
              group.finals.map((claim, index) => (
                <div key={`${group.id}-${index}`} className="space-y-2">
                  {group.finals.length > 1 && (
                    <p className="text-xs font-medium">
                      Split claim {index + 1}
                    </p>
                  )}
                  <p className="whitespace-pre-wrap text-sm leading-6">
                    {claim.claim_text}
                  </p>
                  <Labels
                    name={
                      group.finals.length > 1
                        ? `${name} part ${index + 1}`
                        : name
                    }
                    value={claim.label}
                    onChange={(label) =>
                      onChange(
                        group.finals.map((part, i) =>
                          i === index ? { ...part, label } : part,
                        ),
                      )
                    }
                  />
                </div>
              ))
            )}
            <div className="flex flex-wrap gap-2">
              {group.finals.length > 0 && (
                <>
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => begin(false)}
                  >
                    Edit
                  </Button>
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => begin(true)}
                  >
                    Split
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => onChange([])}
                  >
                    Remove
                  </Button>
                </>
              )}
              {group.original && (
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => onChange([originalFinal(group.original!)])}
                >
                  Undo to original
                </Button>
              )}
            </div>
          </>
        )}
      </fieldset>
    </article>
  )
}
export function ClaimCorrectionList({
  groups,
  locked,
  stagedSpans,
  onChange,
  onEditing,
  onFocusClaim,
}: {
  groups: ClaimGroup[]
  locked: boolean
  stagedSpans: ClaimResponseSpan[]
  onChange: (id: number, claims: FinalModelClaim[]) => void
  onEditing: (id: number, editing: boolean) => void
  onFocusClaim: (position: number) => void
}) {
  return (
    <section
      className="space-y-3"
      aria-label="Claims and labels"
      data-testid="claim-correction-list"
    >
      <h2 className="text-xl font-semibold">Review each claim</h2>
      <dl className="rounded-xl border p-3 space-y-2 text-xs">
        {CLAIM_LABELS.map((label) => (
          <div key={label}>
            <dt className="font-semibold capitalize">{label}</dt>
            <dd className="text-muted-foreground">
              {labelDescriptions[label]}
            </dd>
          </div>
        ))}
      </dl>
      {groups.map((group) => (
        <ClaimEditor
          key={group.id}
          group={group}
          locked={locked}
          stagedSpans={stagedSpans}
          onChange={(claims) => onChange(group.id, claims)}
          onEditing={(editing) => onEditing(group.id, editing)}
          onFocus={() => onFocusClaim(group.id)}
        />
      ))}
    </section>
  )
}
