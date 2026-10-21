import {
  AgreementBar,
  emptyGradeGrid,
  GradeGrid,
  Swatch,
  TaskBars,
} from "./charts"
import { ReviewChip } from "./chips"
import {
  type GradesModel,
  groupAddedClaims,
  LABELS,
  type Label,
  percent,
  type TaskSummary,
} from "./gradesModel"

type Flag = "issue" | "duplicate" | "multipleFacts"

function SectionHeading({ children }: { children: string }) {
  return (
    <h2 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
      {children}
    </h2>
  )
}

/** One question about the data, answered with the numbers behind it. */
function Question({
  title,
  children,
}: {
  title: string
  children: React.ReactNode
}) {
  return (
    <section className="flex flex-col gap-3 rounded-md border p-5">
      <h3 className="text-lg font-semibold">{title}</h3>
      {children}
    </section>
  )
}

/**
 * Among the claims carrying a flag that two or more people reviewed: on how
 * many did every reviewer raise it, and on how many only some?
 */
function HumanAgreement({
  flaggedTotal,
  unanimous,
  multi,
  what = "flagged",
}: {
  /** Claims the reviewers acted on (flagged, regraded, added) */
  flaggedTotal: number
  /** Of those reviewed by two or more people, the ones every reviewer acted on */
  unanimous: number
  /** Acted-on claims reviewed by two or more people */
  multi: number
  /** The verb for what reviewers did */
  what?: "flagged" | "regraded"
}) {
  const single = flaggedTotal - multi
  return (
    <div className="flex flex-col gap-1.5 text-sm">
      <span className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
        Human agreement
      </span>
      {multi === 0 ? (
        <span className="text-muted-foreground">
          No {what} claim was reviewed by two or more people, so agreement
          cannot be measured.
        </span>
      ) : (
        <>
          <AgreementBar
            all={unanimous}
            single={single}
            some={multi - unanimous}
          />
          <ul className="list-disc space-y-1 pl-5">
            <li>
              <Swatch tone="all" />
              <span className="font-mono">
                {unanimous} of {multi} ({percent(unanimous, multi)})
              </span>{" "}
              {what} claims reviewed by two or more people were {what} by all of
              their reviewers.
            </li>
            <li>
              <Swatch tone="some" />
              <span className="font-mono">
                {multi - unanimous} of {multi} (
                {percent(multi - unanimous, multi)})
              </span>{" "}
              were {what} by only some of their reviewers.
            </li>
            {single > 0 ? (
              <li className="text-muted-foreground">
                <Swatch tone="single" />
                {single} {what} {single === 1 ? "claim" : "claims"} had a single
                reviewer and {single === 1 ? "is" : "are"} not counted.
              </li>
            ) : null}
          </ul>
        </>
      )}
    </div>
  )
}

export default function GradesAnalysis({
  model,
  onOpenTask,
}: {
  model: GradesModel
  onOpenTask: (caseId: string) => void
}) {
  const gradedClaims = model.tasks.flatMap((task) =>
    task.claims.filter((claim) => claim.judgments.length > 0),
  )
  const flaggedClaims = (key: Flag) =>
    gradedClaims.filter((claim) => claim.choiceCounts[key] > 0).length
  const agreementOnFlag = (key: Flag) => {
    const flagged = gradedClaims.filter(
      (claim) => claim.judgments.length >= 2 && claim.choiceCounts[key] > 0,
    )
    return {
      multi: flagged.length,
      unanimous: flagged.filter(
        (claim) => claim.choiceCounts[key] === claim.judgments.length,
      ).length,
    }
  }
  // The task whose reviewed claims drew the most Duplicate flags.
  const mostDuplicates = model.tasks
    .map((task) => ({
      task,
      count: task.claims.filter((claim) => claim.choiceCounts.duplicate > 0)
        .length,
    }))
    .reduce<{ task: TaskSummary; count: number } | null>(
      (best, entry) => (entry.count > (best?.count ?? 0) ? entry : best),
      null,
    )
  // Model claims that at least one reviewer split into smaller claims.
  const splitClaims = model.tasks.reduce((count, task) => {
    const splitFrom = new Set(
      task.added
        .map((entry) => entry.claim.split_from_position)
        .filter((position): position is number => position != null),
    )
    return (
      count +
      task.claims.filter(
        (claim) => claim.judgments.length > 0 && splitFrom.has(claim.position),
      ).length
    )
  }, 0)
  const issueAgreement = agreementOnFlag("issue")
  // How human grades moved away from the model's, counted per human grade.
  const gradeMoves = LABELS.flatMap((from) =>
    LABELS.filter((to) => to !== from).map((to) => ({
      from,
      to,
      count: gradedClaims.reduce(
        (count, claim) =>
          claim.proposedLabel === from
            ? count +
              claim.judgments.filter(
                (judgment) => judgment.effectiveLabel === to,
              ).length
            : count,
        0,
      ),
    })),
  )
    .filter((move) => move.count > 0)
    .sort((a, b) => b.count - a.count)
  const totalMoves = gradeMoves.reduce((sum, move) => sum + move.count, 0)
  const gradeGrid = emptyGradeGrid()
  for (const claim of gradedClaims) {
    for (const judgment of claim.judgments) {
      gradeGrid[claim.proposedLabel][judgment.effectiveLabel] += 1
    }
  }
  const issueClaims = gradedClaims.filter(
    (claim) => claim.choiceCounts.issue > 0,
  )
  const issueAndRegraded = issueClaims.filter((claim) => claim.disagreement)
  // Up = the human thought the claim mattered more than the model did.
  const rank = (label: Label) => LABELS.indexOf(label) // vital first
  const movesUp = gradeMoves.filter((move) => rank(move.to) < rank(move.from))
  const movesDown = gradeMoves.filter((move) => rank(move.to) > rank(move.from))
  const sumMoves = (moves: typeof gradeMoves) =>
    moves.reduce((sum, move) => sum + move.count, 0)
  // Claims reviewers added: one entry per distinct added claim per task, with
  // the grade most of its adders gave it and whether every reviewer of the
  // task added it.
  const reviewedTasks = model.tasks.filter((task) => task.reviews.length > 0)
  const addedClaims = reviewedTasks.flatMap((task) =>
    groupAddedClaims(task.added).map((group) => ({
      caseId: task.caseId,
      label: LABELS.reduce((best, label) =>
        group.labelVotes[label] > group.labelVotes[best] ? label : best,
      ),
      split: Object.values(group.byReviewer).some(
        (claim) => claim.split_from_position != null,
      ),
      multi: task.reviewerIds.length >= 2,
      byAll: group.reviewerIds.length === task.reviewerIds.length,
    })),
  )
  // A claim made with Split into atoms is not something the model missed; the
  // model wrote it as part of a bigger claim. Only new claims count here.
  const newClaims = addedClaims.filter((entry) => !entry.split)
  const splitAdded = addedClaims.length - newClaims.length
  const tasksWithNew = new Set(newClaims.map((entry) => entry.caseId)).size
  const newByLabel = (label: Label) =>
    newClaims.filter((entry) => entry.label === label).length
  const newMulti = newClaims.filter((entry) => entry.multi)
  const newByAll = newMulti.filter((entry) => entry.byAll).length
  const multipleFactsAgreement = agreementOnFlag("multipleFacts")
  const duplicateAgreement = agreementOnFlag("duplicate")

  return (
    <div className="flex flex-col gap-6">
      <SectionHeading>Review issues</SectionHeading>
      <Question title="Did the model extract claims from the source text properly?">
        <p className="flex flex-wrap items-center gap-2 text-sm">
          <span className="font-mono">
            {flaggedClaims("issue")} of {gradedClaims.length}
          </span>
          <span>claims were flagged by human reviewers as having an</span>
          <ReviewChip>Extraction issue</ReviewChip>
        </p>
        <TaskBars
          count={(task) =>
            task.claims.filter((claim) => claim.choiceCounts.issue > 0).length
          }
          onOpenTask={onOpenTask}
          tasks={model.tasks}
          what="flagged as an Extraction issue"
        />
        <HumanAgreement
          flaggedTotal={flaggedClaims("issue")}
          multi={issueAgreement.multi}
          unanimous={issueAgreement.unanimous}
        />
      </Question>
      <Question title="Did the model create atomic claims?">
        <p className="flex flex-wrap items-center gap-2 text-sm">
          <span className="font-mono">
            {flaggedClaims("multipleFacts")} of {gradedClaims.length}
          </span>
          <span>claims were flagged by human reviewers as having</span>
          <ReviewChip>Multiple Facts</ReviewChip>
        </p>
        <p className="flex flex-wrap items-center gap-2 text-sm">
          <span className="font-mono">
            {splitClaims} of {gradedClaims.length}
          </span>
          <span>claims were marked by human reviewers as needing to be</span>
          <ReviewChip>Split into atoms</ReviewChip>
        </p>
        <TaskBars
          count={(task) =>
            task.claims.filter((claim) => claim.choiceCounts.multipleFacts > 0)
              .length
          }
          onOpenTask={onOpenTask}
          tasks={model.tasks}
          what="flagged as Multiple Facts"
        />
        <HumanAgreement
          flaggedTotal={flaggedClaims("multipleFacts")}
          multi={multipleFactsAgreement.multi}
          unanimous={multipleFactsAgreement.unanimous}
        />
      </Question>
      <Question title="Did the model create distinct claims?">
        <p className="flex flex-wrap items-center gap-2 text-sm">
          <span className="font-mono">
            {flaggedClaims("duplicate")} of {gradedClaims.length}
          </span>
          <span>claims were flagged by human reviewers as being</span>
          <ReviewChip>Duplicate</ReviewChip>
        </p>
        <p className="text-muted-foreground text-sm">
          {mostDuplicates ? (
            <>
              Task{" "}
              <span className="font-mono">
                {mostDuplicates.task.taskId ?? mostDuplicates.task.caseId}
              </span>{" "}
              had the most duplicates: {mostDuplicates.count} of its{" "}
              {mostDuplicates.task.claims.length} claims.
            </>
          ) : (
            "No task had a claim flagged as a duplicate."
          )}
        </p>
        <TaskBars
          count={(task) =>
            task.claims.filter((claim) => claim.choiceCounts.duplicate > 0)
              .length
          }
          onOpenTask={onOpenTask}
          tasks={model.tasks}
          what="flagged as a Duplicate"
        />
        <HumanAgreement
          flaggedTotal={flaggedClaims("duplicate")}
          multi={duplicateAgreement.multi}
          unanimous={duplicateAgreement.unanimous}
        />
      </Question>
      <SectionHeading>Missing claim issues</SectionHeading>
      <Question title="Did the model miss important claims?">
        <p className="flex flex-wrap items-center gap-2 text-sm">
          <span className="font-mono">
            {tasksWithNew} of {reviewedTasks.length}
          </span>
          <span>
            reviewed tasks had{" "}
            <span className="font-semibold underline underline-offset-4">
              Missing Important claims
            </span>{" "}
            added by human reviewers, {newClaims.length} in all.
          </span>
        </p>
        <ul className="list-disc space-y-1 pl-5 text-sm">
          {LABELS.map((label) => (
            <li key={label}>
              <span className="font-mono">
                {newByLabel(label)} (
                {percent(newByLabel(label), newClaims.length)})
              </span>{" "}
              of the missing claims were graded {label}.
            </li>
          ))}
          <li className="text-muted-foreground">
            Another {splitAdded} added {splitAdded === 1 ? "claim" : "claims"}{" "}
            came from Split into atoms. Those are counted on the atomic-claims
            card, not here.
          </li>
        </ul>
        <div className="flex flex-col gap-1.5 text-sm">
          <span className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
            Human agreement
          </span>
          {newMulti.length === 0 ? (
            <span className="text-muted-foreground">
              No missing claim is on a task reviewed by two or more people, so
              agreement cannot be measured.
            </span>
          ) : (
            <>
              <AgreementBar
                all={newByAll}
                single={newClaims.length - newMulti.length}
                some={newMulti.length - newByAll}
              />
              <ul className="list-disc space-y-1 pl-5">
                <li>
                  <Swatch tone="all" />
                  <span className="font-mono">
                    {newByAll} of {newMulti.length} (
                    {percent(newByAll, newMulti.length)})
                  </span>{" "}
                  missing claims on tasks reviewed by two or more people were
                  added by all of the task's reviewers.
                </li>
                <li>
                  <Swatch tone="some" />
                  <span className="font-mono">
                    {newMulti.length - newByAll} of {newMulti.length} (
                    {percent(newMulti.length - newByAll, newMulti.length)})
                  </span>{" "}
                  were added by only some of them.
                </li>
                {newClaims.length - newMulti.length > 0 ? (
                  <li className="text-muted-foreground">
                    <Swatch tone="single" />
                    {newClaims.length - newMulti.length} missing{" "}
                    {newClaims.length - newMulti.length === 1
                      ? "claim is"
                      : "claims are"}{" "}
                    on single-reviewer tasks and not counted.
                  </li>
                ) : null}
              </ul>
            </>
          )}
        </div>
      </Question>
      <SectionHeading>Grade issues</SectionHeading>
      <Question title="Did the model grade claims the way human reviewers did?">
        <p className="flex flex-wrap items-center gap-2 text-sm">
          <span className="font-mono">
            {gradedClaims.filter((claim) => claim.disagreement).length} of{" "}
            {gradedClaims.length}
          </span>
          <span>
            claims were given a different grade from the model's by at least one
            human reviewer.
          </span>
        </p>
        <p className="flex flex-wrap items-center gap-2 text-sm">
          <span>Of the {issueClaims.length} claims flagged as having an</span>
          <ReviewChip>Extraction issue</ReviewChip>
          <span className="font-mono">
            {issueAndRegraded.length} (
            {percent(issueAndRegraded.length, issueClaims.length)})
          </span>
          <span>also had their grade changed by a human reviewer.</span>
        </p>
        {gradeMoves.length === 0 ? (
          <p className="text-muted-foreground text-sm">No grade was changed.</p>
        ) : (
          <ul className="list-disc space-y-2 pl-5 text-sm">
            {[
              { title: "Graded up", moves: movesUp },
              { title: "Graded down", moves: movesDown },
            ].map(({ title, moves }) => (
              <li key={title}>
                <span className="font-medium">{title}:</span>{" "}
                <span className="font-mono">
                  {sumMoves(moves)} ({percent(sumMoves(moves), totalMoves)})
                </span>
                {moves.length === 0 ? (
                  <span className="text-muted-foreground"> · none</span>
                ) : null}
              </li>
            ))}
          </ul>
        )}
        <GradeGrid counts={gradeGrid} />
        <HumanAgreement
          flaggedTotal={
            gradedClaims.filter((claim) => claim.disagreement).length
          }
          multi={
            gradedClaims.filter(
              (claim) => claim.disagreement && claim.judgments.length >= 2,
            ).length
          }
          unanimous={
            gradedClaims.filter(
              (claim) =>
                claim.disagreement &&
                claim.judgments.length >= 2 &&
                claim.judgments.every((judgment) => judgment.changed),
            ).length
          }
          what="regraded"
        />
      </Question>
    </div>
  )
}
