import * as React from "react"
import type {
  HumanClaim,
  ImportanceGuide,
  ImportanceGuideLabel,
  ReviewModelClaim,
} from "@/client/types.gen"
import { Button } from "@/components/ui/button"
import { evalItemColor } from "@/lib/evalItemPalette"
import { cn } from "@/lib/utils"
import type { ClaimResponseSpan } from "./SelectableClaimResponse"

export type ImportanceLabel = ImportanceGuideLabel["value"]
export type DraftHumanClaim = Omit<HumanClaim, "label"> & {
  label?: ImportanceLabel
}
export type ClaimGroup = {
  id: number
  original?: ReviewModelClaim
  claim: DraftHumanClaim
  issue?: string | null
  duplicate?: boolean
  multipleFacts?: boolean
  looksGood?: boolean
}

export function initialModelClaim(claim: ReviewModelClaim): DraftHumanClaim {
  return { claim_text: claim.claim_text, response_spans: claim.response_spans }
}

export function Labels({
  value,
  name,
  labels,
  locked,
  onChange,
}: {
  value?: ImportanceLabel
  name: string
  labels: ImportanceGuideLabel[]
  locked?: boolean
  onChange: (label: ImportanceLabel | undefined) => void
}) {
  return (
    <fieldset className="flex flex-wrap items-center gap-2" disabled={locked}>
      <legend className="sr-only">{name} label</legend>
      <span aria-hidden="true" className="w-14 shrink-0 text-sm font-medium">
        Grade:
      </span>
      {labels.map((option) => (
        <Button
          size="sm"
          variant={option.value === value ? "default" : "outline"}
          key={option.value}
          type="button"
          aria-pressed={option.value === value}
          title={option.definition}
          onClick={() =>
            onChange(option.value === value ? undefined : option.value)
          }
        >
          {option.label}
        </Button>
      ))}
    </fieldset>
  )
}

function ClaimRow({
  group,
  name,
  active,
  locked,
  labels,
  stagedSpans,
  onChange,
  onRemove,
  onFocus,
}: {
  group: ClaimGroup
  name: string
  active: boolean
  locked: boolean
  labels: ImportanceGuideLabel[]
  stagedSpans: ClaimResponseSpan[]
  onChange: (
    claim: DraftHumanClaim,
    issue?: string | null,
    duplicate?: boolean,
    looksGood?: boolean,
    multipleFacts?: boolean,
  ) => void
  onRemove: () => void
  onFocus: () => void
}) {
  const [open, setOpen] = React.useState(true)
  const { claim, original } = group
  const reviewed =
    group.looksGood ||
    group.duplicate ||
    group.multipleFacts ||
    !!group.issue?.trim() ||
    (!!claim.label && claim.label !== original?.proposed_label)
  const flagged = group.issue !== null && group.issue !== undefined
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
        {name} · <span className="capitalize">{claim.label ?? "Unjudged"}</span>
        {flagged ? " · Extraction issue" : ""}
        {group.duplicate ? " · Duplicate" : ""}
        {group.multipleFacts ? " · Multiple Facts" : ""}
        {original ? (reviewed ? " · Reviewed" : " · Needs review") : ""}
      </summary>
      <div className="mt-3 space-y-3">
        {original ? (
          <p className="text-xs text-muted-foreground">
            Model label: {original.proposed_label}
          </p>
        ) : null}
        {original ? (
          <button
            type="button"
            onClick={onFocus}
            title="Highlight source passage"
            className="block w-full whitespace-pre-wrap rounded-sm text-left text-sm leading-6 hover:underline focus-visible:outline-2 focus-visible:outline-ring"
          >
            {original.claim_text}
          </button>
        ) : null}
        <fieldset disabled={locked} className="space-y-3 disabled:opacity-70">
          {!original ? (
            <textarea
              className="w-full rounded border bg-background p-2 text-sm"
              aria-label={`${name} text`}
              maxLength={20000}
              value={claim.claim_text}
              onChange={(event) =>
                onChange(
                  { ...claim, claim_text: event.target.value },
                  group.issue,
                )
              }
            />
          ) : null}
          <Labels
            value={claim.label}
            name={name}
            labels={labels}
            locked={locked}
            onChange={(label) =>
              onChange(
                { ...claim, label },
                group.issue,
                group.duplicate,
                false,
                group.multipleFacts,
              )
            }
          />
          {original ? (
            <div className="space-y-2">
              <fieldset
                className="flex flex-wrap items-center gap-2"
                aria-label={`${name} review decision`}
              >
                <span
                  aria-hidden="true"
                  className="w-14 shrink-0 text-sm font-medium"
                >
                  Review:
                </span>
                <Button
                  type="button"
                  size="sm"
                  variant={group.looksGood ? "default" : "outline"}
                  aria-pressed={!!group.looksGood}
                  onClick={() =>
                    onChange(
                      {
                        ...claim,
                        label: claim.label ?? original.proposed_label,
                      },
                      null,
                      false,
                      !group.looksGood,
                      false,
                    )
                  }
                >
                  Looks good
                </Button>
                <Button
                  type="button"
                  size="sm"
                  variant={flagged ? "default" : "outline"}
                  aria-pressed={flagged}
                  onClick={() =>
                    onChange(
                      claim,
                      flagged ? null : "",
                      group.duplicate,
                      false,
                      group.multipleFacts,
                    )
                  }
                >
                  Extraction issue
                </Button>
                <Button
                  type="button"
                  size="sm"
                  variant={group.duplicate ? "default" : "outline"}
                  aria-pressed={!!group.duplicate}
                  onClick={() =>
                    onChange(
                      claim,
                      group.issue,
                      !group.duplicate,
                      false,
                      group.multipleFacts,
                    )
                  }
                >
                  Duplicate
                </Button>
                <Button
                  type="button"
                  size="sm"
                  variant={group.multipleFacts ? "default" : "outline"}
                  aria-pressed={!!group.multipleFacts}
                  title="This claim contains multiple facts and is not atomic"
                  onClick={() =>
                    onChange(
                      claim,
                      group.issue,
                      group.duplicate,
                      false,
                      !group.multipleFacts,
                    )
                  }
                >
                  Multiple Facts
                </Button>
              </fieldset>
              {flagged ? (
                <textarea
                  aria-label={`${name} extraction issue`}
                  className="w-full rounded border bg-background p-2 text-sm"
                  maxLength={500}
                  placeholder="Explain the extraction issue"
                  value={group.issue ?? ""}
                  onChange={(event) =>
                    onChange(
                      claim,
                      event.target.value,
                      group.duplicate,
                      false,
                      group.multipleFacts,
                    )
                  }
                />
              ) : null}
            </div>
          ) : (
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
                    onChange(
                      { ...claim, response_spans: [...stagedSpans] },
                      group.issue,
                    )
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
  guide,
  hiddenModels,
  locked,
  stagedSpans,
  onChange,
  onRemove,
  onFocusClaim,
}: {
  activePosition: number | null
  groups: ClaimGroup[]
  guide: ImportanceGuide
  hiddenModels: boolean
  locked: boolean
  stagedSpans: ClaimResponseSpan[]
  onChange: (
    id: number,
    claim: DraftHumanClaim,
    issue?: string | null,
    duplicate?: boolean,
    looksGood?: boolean,
    multipleFacts?: boolean,
  ) => void
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
      labels={guide.labels}
      stagedSpans={stagedSpans}
      onChange={(claim, issue, duplicate, looksGood, multipleFacts) =>
        onChange(group.id, claim, issue, duplicate, looksGood, multipleFacts)
      }
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
        {guide.labels.map((option) => (
          <div key={option.value}>
            <dt className="font-semibold">{option.label}</dt>
            <dd className="text-muted-foreground">{option.definition}</dd>
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
        {!models.length ? (
          <p className="text-sm text-muted-foreground">
            No model claims proposed.
          </p>
        ) : null}
      </div>
      {hiddenModels ? (
        <p className="rounded-xl border p-4 text-sm text-muted-foreground">
          Model claims and highlights are hidden. Your grading is preserved.
        </p>
      ) : null}
      <h2 className="text-xl font-semibold">Missing Important claims</h2>
      <p className="text-sm text-muted-foreground">
        Add only worthwhile assertions that are present in the source text.
      </p>
      <div className="space-y-3" data-testid="human-claims">
        {humans.map((group, i) => row(group, `Human claim ${i + 1}`))}
        {!humans.length ? (
          <p className="text-sm text-muted-foreground">
            Select source text to add a missing claim.
          </p>
        ) : null}
      </div>
    </section>
  )
}
