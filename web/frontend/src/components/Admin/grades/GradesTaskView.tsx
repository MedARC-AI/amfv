import { ChevronLeft, ChevronRight } from "lucide-react"
import type React from "react"
import { Fragment, type ReactNode, useMemo, useState } from "react"

import type { ResponseClaimSpan } from "@/client"
import { Button } from "@/components/ui/button"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { evalItemColor } from "@/lib/evalItemPalette"
import { cn } from "@/lib/utils"
import ClaimColoredText from "./ClaimColoredText"
import ClaimReviewsDialog from "./ClaimReviewsDialog"
import {
  AddedGradeCell,
  GradeText,
  HumansVsHumansInfo,
  Ratio,
  ReviewerDecision,
  ReviewersGradeCell,
  ReviewersReviewCell,
} from "./chips"
import {
  addedPositionBase,
  type ClaimSummary,
  claimColorOwners,
  type GradesModel,
  groupAddedClaims,
  LABELS,
} from "./gradesModel"
import { GRADE_OPTIONS, REVIEW_CHOICES, REVIEW_OPTIONS } from "./reviewOptions"

// Columns shrink to fit the page; these minimums keep the text cells readable.
const CLAIM_CELL = "min-w-[180px] whitespace-normal align-top text-sm"
const SOURCE_CELL = "min-w-[180px] whitespace-normal align-top text-xs"
const GRADE_CELL = "min-w-[150px] whitespace-normal align-top"
const REVIEW_CELL = "min-w-[170px] whitespace-normal align-top"

/** A column title with the reviewer's options printed underneath. */
function HeaderWithOptions({
  title,
  options,
}: {
  title: string
  options: string[]
}) {
  return (
    <span className="flex flex-col items-start gap-0.5">
      <span>{title}</span>
      <span className="whitespace-normal text-[10px] font-normal normal-case tracking-normal">
        {options.join(" · ")}
      </span>
    </span>
  )
}

/**
 * The passages of the source a claim was drawn from, each underlined in the
 * claim's color so they match the marked source text above.
 */
function SpanQuotes({
  position,
  spans,
  added = false,
}: {
  position: number
  spans: ResponseClaimSpan[]
  added?: boolean
}) {
  const seen = new Set<string>()
  const unique = spans.filter((span) => {
    const key = `${span.start}:${span.end}`
    if (seen.has(key)) return false
    seen.add(key)
    return true
  })
  if (unique.length === 0) {
    return <span className="text-muted-foreground">no passage selected</span>
  }
  return (
    <span className="leading-relaxed">
      {unique.map((span, i) => (
        <Fragment key={`${span.start}:${span.end}`}>
          {i > 0 ? <span className="text-muted-foreground"> … </span> : null}
          <span
            className={cn(
              added ? "border-t-2 pt-[2px]" : "border-b-2 pb-[2px]",
              evalItemColor(position).line,
            )}
          >
            {span.text}
          </span>
        </Fragment>
      ))}
    </span>
  )
}

/** A count in one of the disagreement columns; hovering it gives the specifics. */
function FlagMark({ text, detail }: { text: ReactNode; detail: string }) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <button
          className="cursor-default rounded-sm text-left focus-visible:ring-2 focus-visible:ring-ring"
          type="button"
        >
          {text}
        </button>
      </TooltipTrigger>
      <TooltipContent className="max-w-72">{detail}</TooltipContent>
    </Tooltip>
  )
}

/** "2 vital, 1 unimportant" for the grade tooltips. */
function gradeBreakdown(claim: ClaimSummary): string {
  return LABELS.filter((label) => claim.labelVotes[label] > 0)
    .map((label) => `${claim.labelVotes[label]} ${label}`)
    .join(", ")
}

/** "2 Extraction issue, 1 Duplicate" for the flag tooltip. */
function flagBreakdown(claim: ClaimSummary): string {
  return REVIEW_CHOICES.filter(
    (choice) =>
      choice.key !== "looksGood" && claim.choiceCounts[choice.key] > 0,
  )
    .map((choice) => `${claim.choiceCounts[choice.key]} ${choice.text}`)
    .join(", ")
}

/** Reviewers whose grade differs from the model's. */
function changedCount(claim: ClaimSummary): number {
  return claim.judgments.filter((judgment) => judgment.changed).length
}

/** DOM id of a claim's row in the table, used to scroll it into view. */
function claimRowId(position: number): string {
  return `grades-claim-row-${position}`
}

export default function GradesTaskView({
  model,
  caseId,
  onChangeTask,
}: {
  model: GradesModel
  caseId: string | null
  onChangeTask: (caseId: string) => void
}) {
  const [active, setActive] = useState<{
    caseId: string
    position: number
  } | null>(null)
  const index = model.tasks.findIndex((task) => task.caseId === caseId)
  const task = index >= 0 ? model.tasks[index] : model.tasks[0]
  // The selected claim belongs to one task; moving to another task clears it.
  const activePosition =
    task && active?.caseId === task.caseId ? active.position : null
  const setActivePosition = (position: number) => {
    if (task) setActive({ caseId: task.caseId, position })
  }
  // Clicking a row (anywhere but a button or link) selects it, which draws
  // its border and dashes its line in the source text; clicking it again
  // clears it. The row's number toggles the same way.
  const toggleActivePosition = (position: number) => {
    if (activePosition === position) setActive(null)
    else setActivePosition(position)
  }
  const toggleOnRowClick = (
    position: number,
  ): React.MouseEventHandler<HTMLTableRowElement> => {
    return (event) => {
      if ((event.target as HTMLElement).closest("button, a")) return
      toggleActivePosition(position)
    }
  }
  // Clicking a passage in the source text also brings its table row into view.
  const selectFromText = (position: number) => {
    setActivePosition(position)
    requestAnimationFrame(() => {
      document
        .getElementById(claimRowId(position))
        ?.scrollIntoView({ behavior: "smooth", block: "center" })
    })
  }
  const reviewerIndex = useMemo(
    () => new Map(model.reviewers.map((reviewer, i) => [reviewer.id, i])),
    [model.reviewers],
  )

  const addedGroups = useMemo(
    () => (task ? groupAddedClaims(task.added) : []),
    [task],
  )
  // Added claims get positions after the model's, so they can be selected
  // and colored like the model claims (same palette as the review page).
  const addedBase = task ? addedPositionBase(task) : 0
  const owners = useMemo(() => (task ? claimColorOwners(task) : []), [task])

  if (!task) {
    return (
      <p className="text-muted-foreground text-sm">No tasks in this export.</p>
    )
  }
  const taskIndex = index >= 0 ? index : 0
  const reviewers = task.reviewerIds
    .map((id) => model.reviewerById[id])
    .filter((reviewer) => reviewer !== undefined)

  return (
    <div className="flex flex-col gap-5">
      <section className="sticky -top-6 z-10 flex flex-wrap items-end gap-4 border-b bg-background pb-3 pt-1 md:-top-8">
        <div className="flex flex-col gap-1">
          <h2 className="font-mono text-xl font-semibold">{task.caseId}</h2>
          <p className="text-muted-foreground text-xs">
            arm <span className="font-mono">{task.armId}</span> · generator{" "}
            {task.modelId}
          </p>
        </div>
        <div className="flex-1" />
        <div className="flex flex-wrap items-center gap-2">
          <span className="rounded-full border px-3 py-1 text-xs font-medium">
            {task.reviews.length} review{task.reviews.length === 1 ? "" : "s"}
          </span>
          <Button
            aria-label="Previous task"
            disabled={taskIndex === 0}
            onClick={() => onChangeTask(model.tasks[taskIndex - 1].caseId)}
            size="icon"
            type="button"
            variant="outline"
          >
            <ChevronLeft />
          </Button>
          <span className="font-mono text-xs text-muted-foreground">
            {taskIndex + 1} / {model.tasks.length}
          </span>
          <Button
            aria-label="Next task"
            disabled={taskIndex >= model.tasks.length - 1}
            onClick={() => onChangeTask(model.tasks[taskIndex + 1].caseId)}
            size="icon"
            type="button"
            variant="outline"
          >
            <ChevronRight />
          </Button>
        </div>
      </section>

      <section className="flex flex-col gap-4">
        {task.question ? (
          <details className="rounded-md border p-4" open>
            <summary className="cursor-pointer text-xs font-semibold uppercase tracking-wide text-muted-foreground">
              Question
            </summary>
            <p className="mt-2 whitespace-pre-line text-sm">{task.question}</p>
          </details>
        ) : null}
        <div className="flex flex-col gap-3 rounded-md border p-4">
          <span className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
            Source text
          </span>
          <ClaimColoredText
            activePosition={activePosition}
            className="block rounded-lg border bg-background p-4 font-mono text-sm leading-10"
            onSelect={selectFromText}
            owners={owners}
            text={task.response}
          />
          <details className="border-t pt-3 text-xs text-muted-foreground">
            <summary className="cursor-pointer font-medium">
              How to read the lines
            </summary>
            <ul className="mt-2 list-disc space-y-1 pl-4">
              <li>
                A colored line under a passage marks the model claim it came
                from. The color matches the claim's dot in the # column.
              </li>
              <li>
                When two model claims share words, their lines stack below the
                passage, and each claim keeps its own level for its whole
                passage.
              </li>
              <li>
                A colored line above a passage marks a claim a human reviewer
                added (the +1, +2 rows).
              </li>
              <li>
                Click a marked passage to select its claim in the table. Where
                claims overlap, click again to move to the next one.
              </li>
            </ul>
          </details>
        </div>

        {/* Full height and page width, so the page is the only thing that scrolls. */}
        <Table>
          <TableHeader className="[&_th]:h-auto [&_th]:py-2 [&_th]:align-top [&_th]:whitespace-normal">
            <TableRow>
              <TableHead className="w-16">
                <span className="flex flex-col items-start gap-0.5">
                  <span>#</span>
                  <span className="text-[10px] font-normal normal-case tracking-normal">
                    total: {task.claims.length + addedGroups.length}
                  </span>
                </span>
              </TableHead>
              <TableHead className="w-24">
                <strong>Humans</strong> disagree with <strong>model</strong> on{" "}
                <em>claim grade</em>
              </TableHead>
              <TableHead className="w-24">
                <strong>Humans</strong> disagree with <strong>humans</strong> on{" "}
                <em>claim grade</em>
                <HumansVsHumansInfo />
              </TableHead>
              <TableHead className="w-24">
                <strong>Humans</strong> flagged claim
              </TableHead>
              <TableHead className="w-[24%] min-w-[180px]">
                Source text
              </TableHead>
              <TableHead className="w-[24%] min-w-[180px]">Claim</TableHead>
              <TableHead className="w-28">Model's grade</TableHead>
              <TableHead className="min-w-[150px]">
                <HeaderWithOptions
                  options={GRADE_OPTIONS}
                  title="Humans' grades"
                />
              </TableHead>
              <TableHead className="min-w-[170px]">
                <HeaderWithOptions
                  options={REVIEW_OPTIONS}
                  title="Humans' reviews"
                />
              </TableHead>
              <TableHead className="w-36">
                <span className="sr-only">Ratings by reviewer</span>
              </TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {task.claims.map((claim) => (
              <TableRow
                className={cn(
                  "hover:bg-transparent",
                  activePosition === claim.position &&
                    "ring-2 ring-inset ring-primary/40",
                )}
                id={claimRowId(claim.position)}
                key={claim.position}
                onClick={toggleOnRowClick(claim.position)}
              >
                <TableCell className="align-top font-mono text-xs">
                  <Button
                    className="h-auto gap-1.5 p-0 font-mono text-xs"
                    onClick={() => toggleActivePosition(claim.position)}
                    type="button"
                    variant="link"
                  >
                    <span
                      className={cn(
                        "inline-block size-2.5 rounded-full",
                        evalItemColor(claim.position).dot,
                      )}
                    />
                    {claim.position + 1}
                  </Button>
                </TableCell>
                <TableCell className="align-top font-mono text-xs">
                  {claim.disagreement ? (
                    <FlagMark
                      detail={`The model said ${claim.proposedLabel}; reviewers gave ${gradeBreakdown(claim)}.`}
                      text={
                        <Ratio
                          part={changedCount(claim)}
                          whole={claim.judgments.length}
                        />
                      }
                    />
                  ) : null}
                </TableCell>
                <TableCell className="align-top font-mono text-xs">
                  {claim.reviewersSplit ? (
                    <FlagMark
                      detail={`Reviewers gave different grades: ${gradeBreakdown(claim)}.`}
                      text={
                        <Ratio
                          part={claim.minorityGrades}
                          whole={claim.judgments.length}
                        />
                      }
                    />
                  ) : null}
                </TableCell>
                <TableCell className="align-top font-mono text-xs">
                  {claim.flaggedReviews > 0 ? (
                    <FlagMark
                      detail={`Flags: ${flagBreakdown(claim)}.`}
                      text={
                        <Ratio
                          part={claim.flaggedReviews}
                          whole={claim.judgments.length}
                        />
                      }
                    />
                  ) : null}
                </TableCell>
                <TableCell className={SOURCE_CELL}>
                  <SpanQuotes position={claim.position} spans={claim.spans} />
                </TableCell>
                <TableCell className={CLAIM_CELL}>{claim.claim}</TableCell>
                <TableCell className="align-top">
                  <GradeText label={claim.proposedLabel} />
                </TableCell>
                <TableCell className={GRADE_CELL}>
                  <ReviewersGradeCell claim={claim} />
                </TableCell>
                <TableCell className={REVIEW_CELL}>
                  <ReviewersReviewCell
                    claim={claim}
                    reviewers={model.reviewers}
                  />
                </TableCell>
                <TableCell className="align-top">
                  {claim.judgments.length > 0 ? (
                    <ClaimReviewsDialog
                      claimText={claim.claim}
                      dotClass={evalItemColor(claim.position).dot}
                      entries={reviewers.map((reviewer) => {
                        const judgment = claim.judgments.find(
                          (entry) => entry.reviewerId === reviewer.id,
                        )
                        return {
                          reviewer,
                          index: reviewerIndex.get(reviewer.id) ?? 0,
                          body: judgment ? (
                            <ReviewerDecision
                              judgment={judgment}
                              proposedLabel={claim.proposedLabel}
                            />
                          ) : (
                            <span className="text-muted-foreground text-xs">
                              not judged
                            </span>
                          ),
                        }
                      })}
                      title={`Claim ${claim.position + 1}`}
                    />
                  ) : null}
                </TableCell>
              </TableRow>
            ))}
            {addedGroups.map((group, i) => (
              <TableRow
                className={cn(
                  "bg-primary/5 hover:bg-primary/5",
                  activePosition === addedBase + i &&
                    "ring-2 ring-inset ring-primary/40",
                )}
                id={claimRowId(addedBase + i)}
                key={`added-${i}`}
                onClick={toggleOnRowClick(addedBase + i)}
              >
                <TableCell className="align-top font-mono text-xs">
                  <Button
                    className="h-auto gap-1.5 p-0 font-mono text-xs"
                    onClick={() => toggleActivePosition(addedBase + i)}
                    type="button"
                    variant="link"
                  >
                    <span
                      className={cn(
                        "inline-block size-2.5 rounded-full",
                        evalItemColor(addedBase + i).dot,
                      )}
                    />
                    +{i + 1}
                  </Button>
                </TableCell>
                <TableCell />
                <TableCell />
                <TableCell />
                <TableCell className={SOURCE_CELL}>
                  <SpanQuotes
                    added
                    position={addedBase + i}
                    spans={Object.values(group.byReviewer).flatMap(
                      (claim) => claim.response_spans,
                    )}
                  />
                </TableCell>
                <TableCell className={CLAIM_CELL}>{group.text}</TableCell>
                <TableCell className="align-top text-xs text-muted-foreground">
                  none, human added
                </TableCell>
                <TableCell className={GRADE_CELL}>
                  <AddedGradeCell
                    group={group}
                    reviewerCount={reviewers.length}
                  />
                </TableCell>
                <TableCell className={REVIEW_CELL} />
                <TableCell className="align-top">
                  <ClaimReviewsDialog
                    claimText={group.text}
                    dotClass={evalItemColor(addedBase + i).dot}
                    entries={reviewers.map((reviewer) => {
                      const own = group.byReviewer[reviewer.id]
                      return {
                        reviewer,
                        index: reviewerIndex.get(reviewer.id) ?? 0,
                        body: own ? (
                          <span className="flex flex-wrap items-center gap-1">
                            <GradeText label={own.label} />
                            <span className="text-muted-foreground text-[11px]">
                              {own.split_from_position !== null &&
                              own.split_from_position !== undefined
                                ? `split from claim ${own.split_from_position + 1}`
                                : "new claim"}
                            </span>
                          </span>
                        ) : (
                          <span className="text-muted-foreground text-xs">
                            did not add
                          </span>
                        ),
                      }
                    })}
                    title={`Added claim ${i + 1}`}
                  />
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </section>
    </div>
  )
}
