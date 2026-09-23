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

export type SplitAtomDraft = {
  id: number
  claim_text: string
  response_spans: ClaimResponseSpan[]
  label?: ImportanceLabel
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

function SplitEditor({
  position,
  stagedSpans,
  labels,
  onConfirm,
  onCancel,
}: {
  position: number
  stagedSpans: ClaimResponseSpan[]
  labels: ImportanceGuideLabel[]
  onConfirm: (atoms: SplitAtomDraft[]) => void
  onCancel: () => void
}) {
  const nextId = React.useRef(2)
  const [atoms, setAtoms] = React.useState<SplitAtomDraft[]>([
    { id: 0, claim_text: "", response_spans: [], label: undefined },
    { id: 1, claim_text: "", response_spans: [], label: undefined },
  ])

  const updateAtom = (id: number, patch: Partial<SplitAtomDraft>) =>
    setAtoms((current) =>
      current.map((a) => (a.id === id ? { ...a, ...patch } : a)),
    )

  const canConfirm =
    atoms.length >= 2 &&
    atoms.every(
      (a) => a.claim_text.trim() && a.response_spans.length > 0 && a.label,
    )

  return (
    <div className="mt-4 space-y-3 rounded-lg border border-dashed p-3">
      <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
        Split into atoms — Claim {position + 1}
      </p>
      {atoms.map((atom, i) => (
        <div
          key={atom.id}
          className="space-y-2 rounded-md border bg-muted/10 p-3"
        >
          <div className="flex items-center justify-between">
            <span className="text-xs font-medium text-muted-foreground">
              Atom {i + 1}
            </span>
            {atoms.length > 2 && (
              <Button
                size="sm"
                variant="ghost"
                type="button"
                onClick={() =>
                  setAtoms((current) => current.filter((a) => a.id !== atom.id))
                }
              >
                Remove
              </Button>
            )}
          </div>
          <textarea
            aria-label={`Atom ${i + 1} text`}
            className="w-full rounded border bg-background p-2 text-sm"
            maxLength={20000}
            placeholder="First select span text."
            value={atom.claim_text}
            onChange={(e) =>
              updateAtom(atom.id, { claim_text: e.target.value })
            }
          />
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-xs text-muted-foreground">
              {atom.response_spans.length
                ? atom.response_spans.map((s) => s.text).join(" … ")
                : "No spans selected"}
            </span>
            <Button
              size="sm"
              variant="outline"
              type="button"
              disabled={!stagedSpans.length}
              onClick={() =>
                updateAtom(atom.id, {
                  response_spans: [...stagedSpans],
                  claim_text: stagedSpans.map((s) => s.text).join(" "),
                })
              }
            >
              Use staged spans
            </Button>
          </div>
          <Labels
            value={atom.label}
            name={`Atom ${i + 1}`}
            labels={labels}
            onChange={(label) => updateAtom(atom.id, { label })}
          />
        </div>
      ))}
      <div className="flex flex-wrap gap-2">
        <Button
          size="sm"
          variant="outline"
          type="button"
          onClick={() => {
            const id = nextId.current++
            setAtoms((current) => [
              ...current,
              { id, claim_text: "", response_spans: [], label: undefined },
            ])
          }}
        >
          + Add atom
        </Button>
        <Button
          size="sm"
          type="button"
          disabled={!canConfirm}
          onClick={() => onConfirm(atoms)}
        >
          Confirm split
        </Button>
        <Button size="sm" variant="ghost" type="button" onClick={onCancel}>
          Cancel
        </Button>
      </div>
    </div>
  )
}

function ClaimRow({
  group,
  name,
  active,
  locked,
  labels,
  stagedSpans,
  splitSection,
  onSplitStart,
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
  splitSection?: React.ReactNode
  onSplitStart?: () => void
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
        active && "ring-2",
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
            className="block w-full whitespace-pre-wrap rounded-sm text-left text-sm font-medium leading-6 hover:underline focus-visible:outline-2 focus-visible:outline-ring"
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
                {onSplitStart ? (
                  <Button
                    type="button"
                    size="sm"
                    variant="outline"
                    onClick={onSplitStart}
                  >
                    Split into atoms
                  </Button>
                ) : null}
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
        {splitSection}
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
  onSplitConfirm,
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
  onSplitConfirm: (groupId: number, atoms: SplitAtomDraft[]) => void
}) {
  const [splitTargetId, setSplitTargetId] = React.useState<number | null>(null)
  const models = groups.filter((group) => group.original)
  const humans = groups.filter((group) => !group.original)

  const humanName = (group: ClaimGroup) => {
    const pos = group.claim.split_from_position
    if (pos != null) {
      const splitIndex = humans
        .filter((g) => g.claim.split_from_position === pos)
        .indexOf(group)
      return `Split ${splitIndex + 1} (from Claim ${pos + 1})`
    }
    const nonSplitIndex = humans
      .filter((g) => g.claim.split_from_position == null)
      .indexOf(group)
    return `Human claim ${nonSplitIndex + 1}`
  }

  const row = (
    group: ClaimGroup,
    name: string,
    splitSection?: React.ReactNode,
    onSplitStart?: () => void,
  ) => (
    <ClaimRow
      key={group.id}
      group={group}
      name={name}
      active={activePosition === group.id}
      locked={locked}
      labels={guide.labels}
      stagedSpans={stagedSpans}
      splitSection={splitSection}
      onSplitStart={onSplitStart}
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
        {models.map((group) => {
          const isSplitting = group.id === splitTargetId
          const splitSection =
            !locked && isSplitting ? (
              <SplitEditor
                position={group.original!.position}
                stagedSpans={stagedSpans}
                labels={guide.labels}
                onConfirm={(atoms) => {
                  setSplitTargetId(null)
                  onSplitConfirm(group.id, atoms)
                }}
                onCancel={() => setSplitTargetId(null)}
              />
            ) : undefined
          const onSplitStart =
            !locked && !isSplitting
              ? () => {
                  setSplitTargetId(group.id)
                  onChange(
                    group.id,
                    group.claim,
                    group.issue,
                    group.duplicate,
                    false,
                    true,
                  )
                }
              : undefined
          return row(
            group,
            `Claim ${group.original!.position + 1}`,
            splitSection,
            onSplitStart,
          )
        })}
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
        {humans.map((group) => row(group, humanName(group)))}
        {!humans.length ? (
          <p className="text-sm text-muted-foreground">
            Select source text to add a missing claim.
          </p>
        ) : null}
      </div>
    </section>
  )
}
