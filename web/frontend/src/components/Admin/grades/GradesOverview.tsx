import { type ReactNode, useLayoutEffect, useRef, useState } from "react"

import { Button } from "@/components/ui/button"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { cn } from "@/lib/utils"
import ClaimColoredText from "./ClaimColoredText"
import { SplitBar, Swatch } from "./charts"
import { HumansVsHumansInfo, Ratio, ReviewChip } from "./chips"
import {
  claimColorOwners,
  type GradesModel,
  percent,
  type TaskSummary,
} from "./gradesModel"

type Filter = "all" | "disagree" | "split" | "flagged" | "added" | "unreviewed"

/**
 * A table cell that shows only its first few lines until "Show more" is
 * clicked. Real questions and answers run to many paragraphs; without this
 * one Overview row fills the screen.
 */
function ClampedCell({
  children,
  className,
}: {
  children: ReactNode
  className?: string
}) {
  const [expanded, setExpanded] = useState(false)
  const [overflows, setOverflows] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  useLayoutEffect(() => {
    const element = ref.current
    if (element) setOverflows(element.scrollHeight > element.clientHeight + 1)
  }, [])
  return (
    <div className="flex flex-col items-start gap-1">
      <div
        className={cn(!expanded && "max-h-24 overflow-hidden", className)}
        ref={ref}
      >
        {children}
      </div>
      {overflows ? (
        <button
          className="cursor-pointer text-xs text-primary underline underline-offset-2 hover:text-primary/80"
          onClick={() => setExpanded((current) => !current)}
          type="button"
        >
          {expanded ? "Show less" : "Show more"}
        </button>
      ) : null}
    </div>
  )
}

function StatTile({
  label,
  value,
  meter,
  details,
}: {
  label: ReactNode
  value?: string
  /** A thin bar drawn under the big number. */
  meter?: ReactNode
  details: ReactNode[]
}) {
  return (
    <div className="flex flex-col gap-1 rounded-md border p-4">
      <span className="text-muted-foreground text-xs font-medium">{label}</span>
      {value ? (
        <span className="text-2xl font-semibold leading-none">{value}</span>
      ) : null}
      {meter ? <div className="mt-1">{meter}</div> : null}
      <ul className="mt-1 list-disc space-y-1 pl-4 text-muted-foreground text-xs">
        {details.map((detail, index) => (
          <li key={index}>{detail}</li>
        ))}
      </ul>
    </div>
  )
}

function matchesFilter(task: TaskSummary, filter: Filter): boolean {
  switch (filter) {
    case "disagree":
      return task.disagreeCount > 0
    case "split":
      return task.splitCount > 0
    case "flagged":
      return task.flaggedReviews > 0
    case "added":
      return task.addedClaims > 0
    case "unreviewed":
      return !task.reviewed
    default:
      return true
  }
}

export default function GradesOverview({
  model,
  onOpenTask,
}: {
  model: GradesModel
  onOpenTask: (caseId: string) => void
}) {
  const [filter, setFilter] = useState<Filter>("all")
  const { totals } = model

  const counts: Record<Filter, number> = {
    all: model.tasks.length,
    disagree: model.tasks.filter((t) => t.disagreeCount > 0).length,
    split: model.tasks.filter((t) => t.splitCount > 0).length,
    flagged: model.tasks.filter((t) => t.flaggedReviews > 0).length,
    added: model.tasks.filter((t) => t.addedClaims > 0).length,
    unreviewed: model.tasks.filter((t) => !t.reviewed).length,
  }
  const filters: { key: Filter; label: string }[] = [
    { key: "all", label: "All tasks" },
    { key: "disagree", label: "Humans disagree with model on claim grade" },
    { key: "split", label: "Humans disagree with humans on claim grade" },
    { key: "flagged", label: "Humans flagged claim" },
    { key: "added", label: "Humans added claims" },
    { key: "unreviewed", label: "Unreviewed" },
  ]

  const visibleTasks = model.tasks.filter((task) => matchesFilter(task, filter))

  return (
    <div className="flex flex-col gap-6">
      <section className="grid gap-3 md:grid-cols-2 xl:grid-cols-5">
        <StatTile
          details={[
            <>
              {totals.reviewed} of {totals.tasks}{" "}
              <strong className="font-semibold">TASKS</strong> reviewed
            </>,
            <>
              {totals.judged} of {totals.claims}{" "}
              <strong className="font-semibold">CLAIMS</strong> (model claims)
              reviewed
            </>,
            <>
              {totals.judgments}{" "}
              <strong className="font-semibold">CLAIMS ASSESSED</strong> (one
              per reviewer per reviewed claim)
            </>,
            <>
              {totals.multiGraded} of {totals.judged} reviewed{" "}
              <strong className="font-semibold">CLAIMS</strong> have two or more
              reviewers (
              {totals.reviewed > 0
                ? (totals.reviews / totals.reviewed).toFixed(1)
                : "0"}{" "}
              reviewers per reviewed task)
            </>,
          ]}
          label="Totals"
        />
        <StatTile
          details={[
            <>
              {totals.changed} of {totals.judgments} assessed claim{" "}
              <strong className="font-semibold">GRADES</strong> differ from the
              model
            </>,
            <>
              {totals.disagreements} of {totals.judged} graded model claims had
              at least one grade changed
            </>,
            <>
              {totals.gradedUp} graded up (toward vital), {totals.gradedDown}{" "}
              graded down
            </>,
          ]}
          label="Humans disagree with model on claim grade"
          meter={
            <SplitBar
              segments={[
                {
                  tone: "all",
                  count: totals.changed,
                  label: "grades that differ from the model's",
                },
              ]}
              total={totals.judgments}
            />
          }
          value={percent(totals.changed, totals.judgments)}
        />
        <StatTile
          details={[
            <>
              {totals.minorityGrades} of {totals.multiGradedGrades} assessed
              claim <strong className="font-semibold">GRADES</strong> differ
              from the most common grade on their claim
            </>,
            <>
              {totals.reviewerSplits} of {totals.multiGraded} claims graded by
              two or more people got more than one grade
            </>,
          ]}
          label={
            <>
              Humans disagree with humans on claim grade
              <HumansVsHumansInfo />
            </>
          }
          meter={
            <SplitBar
              segments={[
                {
                  tone: "all",
                  count: totals.minorityGrades,
                  label: "grades outside the most common grade on their claim",
                },
              ]}
              total={totals.multiGradedGrades}
            />
          }
          value={percent(totals.minorityGrades, totals.multiGradedGrades)}
        />
        <StatTile
          details={[
            <>
              {totals.flaggedReviews} of {totals.judgments} assessed claim{" "}
              <strong className="font-semibold">REVIEWS</strong> had a review
              issue
              <ul className="mt-1 list-[circle] space-y-1 pl-4">
                <li>
                  <Swatch tone="all" />
                  {totals.issues} Extraction issue
                </li>
                <li>
                  <Swatch tone="some" />
                  {totals.duplicates} Duplicate
                </li>
                <li>
                  <Swatch tone="single" />
                  {totals.multipleFacts} Multiple Facts
                </li>
                <li className="list-none text-muted-foreground">
                  a review can carry more than one flag
                </li>
              </ul>
            </>,
            <>
              {totals.flaggedClaims} of {totals.judged} graded model claims got
              at least one
            </>,
          ]}
          label="Humans flagged claim"
          meter={
            <SplitBar
              segments={[
                {
                  tone: "all",
                  count: totals.issues,
                  label: "Extraction issue",
                },
                { tone: "some", count: totals.duplicates, label: "Duplicate" },
                {
                  tone: "single",
                  count: totals.multipleFacts,
                  label: "Multiple Facts",
                },
              ]}
              total={totals.judgments}
            />
          }
          value={percent(totals.flaggedReviews, totals.judgments)}
        />
        <StatTile
          details={[
            <>{totals.added - totals.splits} are new claims the model missed</>,
            <>
              {totals.splits} came from{" "}
              <ReviewChip small>Split into atoms</ReviewChip>
            </>,
            <>
              {totals.additions} additions by reviewers in all; the same claim
              added by two people counts once above
            </>,
          ]}
          label="Claims added by reviewers"
          value={String(totals.added)}
        />
      </section>

      <section className="flex flex-wrap items-center gap-2">
        {filters.map((entry) => (
          <Button
            aria-pressed={filter === entry.key}
            key={entry.key}
            onClick={() => setFilter(entry.key)}
            size="sm"
            type="button"
            variant={filter === entry.key ? "default" : "outline"}
          >
            {entry.label} · {counts[entry.key]}
          </Button>
        ))}
      </section>

      <section className="overflow-x-auto rounded-md border">
        <Table>
          {/* Header labels may wrap to two lines so the number columns stay narrow. */}
          <TableHeader className="[&_th]:h-auto [&_th]:py-3 [&_th]:align-top [&_th]:leading-tight [&_th]:whitespace-normal">
            <TableRow>
              <TableHead className="w-14 text-right">Task</TableHead>
              <TableHead className="w-32">Case</TableHead>
              <TableHead className="min-w-[180px]">Question</TableHead>
              <TableHead className="min-w-[200px]">Source text</TableHead>
              <TableHead className="w-16 text-right">Model claims</TableHead>
              <TableHead className="w-20 text-right">Reviewers</TableHead>
              <TableHead className="w-28">
                Humans disagree with model on claim grade
              </TableHead>
              <TableHead className="w-28">
                Humans disagree with humans on claim grade
                <HumansVsHumansInfo />
              </TableHead>
              <TableHead className="w-24">Humans flagged claim</TableHead>
              <TableHead className="w-20 text-right">
                Humans added claims
              </TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {visibleTasks.map((task) => (
              <TableRow
                className="hover:bg-transparent [&>td]:py-4 [&>td]:align-top"
                key={task.caseId}
              >
                <TableCell className="text-right font-mono">
                  {task.taskId ?? "—"}
                </TableCell>
                <TableCell className="font-mono text-xs">
                  <Button
                    className="h-auto p-0 font-mono text-xs"
                    onClick={() => onOpenTask(task.caseId)}
                    type="button"
                    variant="link"
                  >
                    {task.caseId}
                  </Button>
                </TableCell>
                <TableCell className="text-muted-foreground">
                  <ClampedCell className="whitespace-pre-line">
                    {task.question ?? "—"}
                  </ClampedCell>
                </TableCell>
                <TableCell className="text-xs text-muted-foreground">
                  <ClampedCell className="whitespace-pre-line">
                    <ClaimColoredText
                      className="block leading-8"
                      owners={claimColorOwners(task)}
                      text={task.response}
                    />
                  </ClampedCell>
                </TableCell>
                <TableCell className="text-right font-mono">
                  {task.claims.length}
                </TableCell>
                <TableCell className="text-right tabular-nums">
                  {task.reviewerIds.length}
                </TableCell>
                <TableCell>
                  {task.judgments > 0 ? (
                    <Ratio part={task.changedGrades} whole={task.judgments} />
                  ) : (
                    <span className="text-muted-foreground">—</span>
                  )}
                </TableCell>
                <TableCell>
                  {task.multiGradedGrades > 0 ? (
                    <Ratio
                      part={task.minorityGrades}
                      whole={task.multiGradedGrades}
                    />
                  ) : (
                    <span className="text-muted-foreground">—</span>
                  )}
                </TableCell>
                <TableCell>
                  {task.judgments > 0 ? (
                    <Ratio part={task.flaggedReviews} whole={task.judgments} />
                  ) : (
                    <span className="text-muted-foreground">—</span>
                  )}
                </TableCell>
                <TableCell className="text-right font-mono">
                  {task.addedClaims}
                </TableCell>
              </TableRow>
            ))}
            {visibleTasks.length === 0 ? (
              <TableRow>
                <TableCell className="text-muted-foreground" colSpan={10}>
                  No tasks match this filter.
                </TableCell>
              </TableRow>
            ) : null}
          </TableBody>
        </Table>
      </section>
    </div>
  )
}
