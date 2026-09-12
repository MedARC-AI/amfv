import * as React from "react"
import type { HumanClaim, ReviewModelClaim } from "@/client/types.gen"
import { Button } from "@/components/ui/button"
import { evalItemColor } from "@/lib/evalItemPalette"
import { cn } from "@/lib/utils"
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
  claim: HumanClaim
}
export function initialModelClaim(claim: ReviewModelClaim): HumanClaim {
  return {
    claim_text: claim.claim_text,
    response_spans: claim.response_spans,
    label: claim.proposed_label,
  }
}
export function Labels({
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
          className={cn(
            "rounded border px-2 py-1 text-xs capitalize",
            label === value
              ? "bg-primary text-primary-foreground"
              : "bg-background",
          )}
          onClick={() => onChange(label)}
        >
          {label}
        </button>
      ))}
    </fieldset>
  )
}
function ClaimRow({
  group,
  name,
  active,
  locked,
  stagedSpans,
  onChange,
  onRemove,
  onFocus,
}: {
  group: ClaimGroup
  name: string
  active: boolean
  locked: boolean
  stagedSpans: ClaimResponseSpan[]
  onChange: (claim: HumanClaim) => void
  onRemove: () => void
  onFocus: () => void
}) {
  const [open, setOpen] = React.useState(true)
  const { claim, original } = group
  return (
    <details
      open={open}
      onToggle={(event) => setOpen(event.currentTarget.open)}
      id={`claim-position-${group.id}`}
      data-claim-position={group.id}
      className={cn(
        "rounded-xl border border-l-4 bg-card p-4",
        evalItemColor(group.id).card,
        active && "ring-2 ring-primary/60",
      )}
    >
      <summary className="cursor-pointer text-sm font-semibold">
        <span
          aria-hidden="true"
          className={cn(
            "mx-2 inline-block size-2.5 rounded-full",
            evalItemColor(group.id).dot,
          )}
        />
        {name} · <span className="capitalize">{claim.label}</span>
      </summary>
      <div className="mt-3 space-y-3">
        {original && (
          <p className="text-xs text-muted-foreground">
            Proposed: {original.proposed_label}
          </p>
        )}
        {original && (
          <button
            type="button"
            onClick={onFocus}
            title="Highlight source passage"
            className="block w-full whitespace-pre-wrap rounded-sm text-left text-sm leading-6 hover:underline focus-visible:outline-2 focus-visible:outline-ring"
          >
            {original.claim_text}
          </button>
        )}
        <fieldset disabled={locked} className="space-y-3 disabled:opacity-70">
          {!original && (
            <textarea
              className="w-full rounded border bg-background p-2 text-sm"
              aria-label={`${name} text`}
              maxLength={20000}
              value={claim.claim_text}
              onChange={(event) =>
                onChange({ ...claim, claim_text: event.target.value })
              }
            />
          )}
          <Labels
            value={claim.label}
            name={name}
            onChange={(label) => onChange({ ...claim, label })}
          />
          {!original && (
            <>
              <p className="text-xs text-muted-foreground">
                Source:{" "}
                {claim.response_spans.map((span) => span.text).join(" … ")}
              </p>
              <div className="flex flex-wrap gap-2">
                <Button
                  size="sm"
                  variant="outline"
                  disabled={!stagedSpans.length}
                  onClick={() =>
                    onChange({ ...claim, response_spans: [...stagedSpans] })
                  }
                >
                  Use selected source
                </Button>
                <Button size="sm" variant="ghost" onClick={onRemove}>
                  Remove human claim
                </Button>
              </div>
            </>
          )}
        </fieldset>
      </div>
    </details>
  )
}
export function ClaimCorrectionList({
  activePosition,
  groups,
  hiddenModels,
  locked,
  stagedSpans,
  onChange,
  onRemove,
  onFocusClaim,
}: {
  activePosition: number | null
  groups: ClaimGroup[]
  hiddenModels: boolean
  locked: boolean
  stagedSpans: ClaimResponseSpan[]
  onChange: (id: number, claim: HumanClaim) => void
  onRemove: (id: number) => void
  onFocusClaim: (position: number) => void
}) {
  const models = groups.filter((group) => group.original)
  const humans = groups.filter((group) => !group.original)
  const row = (group: ClaimGroup, name: string) => (
    <ClaimRow
      key={group.id}
      group={group}
      name={name}
      active={activePosition === group.id}
      locked={locked}
      stagedSpans={stagedSpans}
      onChange={(claim) => onChange(group.id, claim)}
      onRemove={() => onRemove(group.id)}
      onFocus={() => onFocusClaim(group.id)}
    />
  )
  return (
    <section
      className="space-y-4"
      aria-label="Claims and labels"
      data-testid="claim-correction-list"
    >
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
      <h2 className="text-xl font-semibold">Model claims to grade</h2>
      <div
        hidden={hiddenModels}
        className="space-y-3"
        data-testid="model-claims"
      >
        {models.map((group) =>
          row(group, `Claim ${group.original!.position + 1}`),
        )}
        {!models.length && (
          <p className="text-sm text-muted-foreground">
            No model claims proposed.
          </p>
        )}
      </div>
      {hiddenModels && (
        <p className="rounded-xl border p-4 text-sm text-muted-foreground">
          Model claims and highlights are hidden. Your grading is preserved.
        </p>
      )}
      <h2 className="text-xl font-semibold">Your human claims</h2>
      <p className="text-sm text-muted-foreground">
        These are additional annotations. Model claims and grades stay intact.
      </p>
      <div className="space-y-3" data-testid="human-claims">
        {humans.map((group, i) => row(group, `Human claim ${i + 1}`))}
        {!humans.length && (
          <p className="text-sm text-muted-foreground">
            Select source text to create your own claims.
          </p>
        )}
      </div>
    </section>
  )
}
