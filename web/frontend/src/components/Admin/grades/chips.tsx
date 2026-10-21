import { Info } from "lucide-react"
import { Fragment, type ReactNode } from "react"
import { buttonVariants } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog"
import { cn } from "@/lib/utils"
import {
  type AddedClaimGroup,
  type ChoiceCounts,
  type ClaimSummary,
  type Judgment,
  LABELS,
  type Label,
  percent,
  type Reviewer,
} from "./gradesModel"
import { REVIEW_CHOICES } from "./reviewOptions"

const CHOICE_ORDER = REVIEW_CHOICES.map((choice) => choice.key)
const CHOICE_TEXT = Object.fromEntries(
  REVIEW_CHOICES.map((choice) => [choice.key, choice.text]),
) as Record<keyof ChoiceCounts, string>

/** A review choice shown the way the review page's buttons look, but not clickable. */
export function ReviewChip({
  children,
  small = false,
}: {
  children: string
  /** Fits the chip into small text, such as an Overview tile's bullets. */
  small?: boolean
}) {
  return (
    <span
      className={cn(
        buttonVariants({ variant: "outline", size: "sm" }),
        "pointer-events-none align-middle",
        small && "h-6 px-2 text-xs",
      )}
    >
      {children}
    </span>
  )
}

/**
 * A small info icon next to a metric's name. Clicking it opens a dialog that
 * explains how the metric is calculated.
 */
function MetricInfo({
  title,
  children,
}: {
  title: string
  children: ReactNode
}) {
  return (
    <Dialog>
      <DialogTrigger asChild>
        <button
          aria-label={`How "${title}" is calculated`}
          className="ml-1 inline-flex cursor-pointer rounded-sm align-middle text-muted-foreground hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring"
          type="button"
        >
          <Info aria-hidden="true" className="size-3.5" />
        </button>
      </DialogTrigger>
      <DialogContent className="max-h-[85svh] overflow-y-auto sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>How this number is calculated.</DialogDescription>
        </DialogHeader>
        {children}
      </DialogContent>
    </Dialog>
  )
}

/** The explanation behind every "Humans disagree with humans on claim grade" number. */
export function HumansVsHumansInfo() {
  return (
    <MetricInfo title="Humans disagree with humans on claim grade">
      <ul className="list-disc space-y-2 pl-5 text-sm">
        <li>
          Only model claims that two or more reviewers graded count. A claim
          with one reviewer is skipped, because there is nobody for that
          reviewer to disagree with.
        </li>
        <li>
          Every grade given on those claims is counted. That is the second
          number.
        </li>
        <li>
          On each claim, the most common grade is the majority. Every other
          grade on that claim counts as a disagreement. Those add up to the
          first number.
        </li>
        <li>
          Examples: vital, vital, unimportant scores 1 of 3. Vital,
          semi-important, unimportant scores 2 of 3. Vital and semi-important
          from two reviewers scores 1 of 2.
        </li>
        <li>
          The model's grade is not part of this. Neither are the review buttons;
          those are counted under flagged claims.
        </li>
      </ul>
    </MetricInfo>
  )
}

/** "4 of 18 (22%)": a count out of a total, with the share in muted text. */
export function Ratio({ part, whole }: { part: number; whole: number }) {
  return (
    <span className="inline-flex flex-col font-mono text-xs">
      <span className="whitespace-nowrap">
        {part} of {whole}
      </span>
      <span className="text-muted-foreground">({percent(part, whole)})</span>
    </span>
  )
}

/** A grade as plain text. */
export function GradeText({
  label,
  className,
}: {
  label: Label
  className?: string
}) {
  return <span className={cn("text-sm", className)}>{label}</span>
}

const REVIEWER_COLORS = [
  "bg-sky-100 text-sky-900 dark:bg-sky-900/50 dark:text-sky-200",
  "bg-violet-100 text-violet-900 dark:bg-violet-900/50 dark:text-violet-200",
  "bg-emerald-100 text-emerald-900 dark:bg-emerald-900/50 dark:text-emerald-200",
  "bg-rose-100 text-rose-900 dark:bg-rose-900/50 dark:text-rose-200",
  "bg-amber-100 text-amber-900 dark:bg-amber-900/50 dark:text-amber-200",
  "bg-teal-100 text-teal-900 dark:bg-teal-900/50 dark:text-teal-200",
  "bg-fuchsia-100 text-fuchsia-900 dark:bg-fuchsia-900/50 dark:text-fuchsia-200",
  "bg-lime-100 text-lime-900 dark:bg-lime-900/50 dark:text-lime-200",
]

function reviewerColor(index: number): string {
  return REVIEWER_COLORS[index % REVIEWER_COLORS.length]
}

/** A reviewer's initials in their color, with their name beside. */
export function ReviewerChip({
  reviewer,
  index,
}: {
  reviewer: Reviewer
  /** Position in the dataset's reviewer list, which picks the color. */
  index: number
}) {
  return (
    <span className="inline-flex items-center gap-2">
      <span
        className={cn(
          "inline-flex size-6 shrink-0 items-center justify-center rounded-full text-[10px] font-semibold",
          reviewerColor(index),
        )}
        title={`${reviewer.name} (${reviewer.kind})`}
      >
        {reviewer.initials}
      </span>
      <span className="text-sm">
        {reviewer.name}
        {reviewer.kind === "expert" ? (
          <span className="text-muted-foreground"> · expert</span>
        ) : null}
      </span>
    </span>
  )
}

/**
 * What one reviewer did with one model claim, worded like the review page:
 * the grade they left it with, then the review choice (Looks good,
 * Extraction issue with its note, Duplicate, Multiple Facts).
 */
export function ReviewerDecision({
  judgment,
  proposedLabel,
}: {
  judgment: Judgment
  proposedLabel: Label
}) {
  const chosen = CHOICE_ORDER.filter((key) => {
    if (key === "looksGood") return judgment.looksGood
    if (key === "issue") return judgment.issue !== null
    if (key === "duplicate") return judgment.duplicate
    return judgment.multipleFacts
  })
  return (
    <span className="flex flex-col items-start gap-1">
      <span className="flex flex-wrap items-center gap-1">
        <GradeText label={judgment.effectiveLabel} />
        <span className="text-muted-foreground text-[11px]">
          {judgment.changed ? `was ${proposedLabel}` : "kept"}
        </span>
      </span>
      {chosen.length === 0 ? (
        <span className="text-muted-foreground text-[11px]">
          grade changed only
        </span>
      ) : (
        <span className="text-sm">
          {chosen.map((key) => CHOICE_TEXT[key]).join(" · ")}
        </span>
      )}
      {judgment.issue ? (
        <span className="text-muted-foreground text-xs italic leading-snug">
          "{judgment.issue}"
        </span>
      ) : null}
    </span>
  )
}

/**
 * The extraction-issue notes reviewers wrote on one claim, opened from an
 * underlined "Extraction issue" entry in the claims table.
 */
function IssueNotesDialog({
  claim,
  reviewers,
  children,
}: {
  claim: ClaimSummary
  reviewers: Reviewer[]
  children: ReactNode
}) {
  const notes = claim.judgments.filter((judgment) => judgment.issue)
  return (
    <Dialog>
      <DialogTrigger asChild>
        <button
          className="cursor-pointer rounded-sm text-left text-sm text-primary underline underline-offset-2 hover:text-primary/80 focus-visible:ring-2 focus-visible:ring-ring"
          type="button"
        >
          {children}
        </button>
      </DialogTrigger>
      <DialogContent className="max-h-[85svh] overflow-y-auto sm:max-w-xl">
        <DialogHeader>
          <DialogTitle>
            Extraction issues on claim {claim.position + 1}
          </DialogTitle>
          <DialogDescription className="text-foreground text-sm">
            {claim.claim}
          </DialogDescription>
        </DialogHeader>
        <dl className="grid grid-cols-[auto_1fr] items-start gap-x-4 gap-y-4">
          {notes.map((judgment) => {
            const index = reviewers.findIndex(
              (reviewer) => reviewer.id === judgment.reviewerId,
            )
            const reviewer = index >= 0 ? reviewers[index] : null
            return (
              <Fragment key={judgment.reviewerId}>
                <dt className="pt-0.5">
                  {reviewer ? (
                    <ReviewerChip index={index} reviewer={reviewer} />
                  ) : (
                    <span className="text-muted-foreground text-sm">
                      unknown reviewer
                    </span>
                  )}
                </dt>
                <dd className="text-sm italic">"{judgment.issue}"</dd>
              </Fragment>
            )
          })}
        </dl>
      </DialogContent>
    </Dialog>
  )
}

/** Two columns: a label per row, then how many reviewers chose it. */
function CountRows({
  rows,
}: {
  rows: { key: string; label: ReactNode; count: number; note?: string }[]
}) {
  return (
    <span className="grid w-fit grid-cols-[auto_auto] items-center gap-x-3 gap-y-1">
      {rows.map((row) => (
        <Fragment key={row.key}>
          {row.label}
          <span className="whitespace-nowrap text-right font-mono text-xs tabular-nums">
            {row.count}
            {row.note ? (
              <span className="text-muted-foreground"> {row.note}</span>
            ) : null}
          </span>
        </Fragment>
      ))}
    </span>
  )
}

function gradeRows(labelVotes: Record<Label, number>) {
  return LABELS.filter((label) => labelVotes[label] > 0)
    .sort((a, b) => labelVotes[b] - labelVotes[a])
    .map((label) => ({
      key: label,
      label: <GradeText label={label} />,
      count: labelVotes[label],
    }))
}

/** Each grade reviewers gave one model claim, with how many gave it. */
export function ReviewersGradeCell({ claim }: { claim: ClaimSummary }) {
  if (claim.judgments.length === 0) {
    return <span className="text-muted-foreground text-xs">no reviews yet</span>
  }
  return <CountRows rows={gradeRows(claim.labelVotes)} />
}

/**
 * Each review choice reviewers made on one model claim, with how many made
 * it. "Extraction issue" is underlined and opens the notes they wrote.
 */
export function ReviewersReviewCell({
  claim,
  reviewers,
}: {
  claim: ClaimSummary
  reviewers: Reviewer[]
}) {
  const rows = CHOICE_ORDER.filter((key) => claim.choiceCounts[key] > 0).map(
    (key) => ({
      key,
      label:
        key === "issue" ? (
          <IssueNotesDialog claim={claim} reviewers={reviewers}>
            {CHOICE_TEXT[key]}
          </IssueNotesDialog>
        ) : (
          <span className="text-sm">{CHOICE_TEXT[key]}</span>
        ),
      count: claim.choiceCounts[key],
    }),
  )
  if (rows.length === 0) return null
  return <CountRows rows={rows} />
}

/**
 * The grades reviewers gave a claim they added themselves, with counts, plus
 * how many of the task's reviewers added it.
 */
export function AddedGradeCell({
  group,
  reviewerCount,
}: {
  group: AddedClaimGroup
  reviewerCount: number
}) {
  const total = group.reviewerIds.length
  return (
    <span className="flex flex-col items-start gap-1">
      <CountRows rows={gradeRows(group.labelVotes)} />
      <span className="text-muted-foreground text-[11px]">
        added by {total} of {reviewerCount} reviewer
        {reviewerCount === 1 ? "" : "s"}
      </span>
    </span>
  )
}
