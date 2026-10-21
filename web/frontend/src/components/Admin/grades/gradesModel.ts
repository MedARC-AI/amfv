import type {
  AdminExportCorrectionReview,
  AdminExportModelCorrectionItem,
  AdminUserMetric,
  HumanClaim,
  ResponseClaimSpan,
} from "@/client"
import type { ExportItem } from "./exportAll"

export type Label = "vital" | "semi-important" | "unimportant"
export const LABELS: Label[] = ["vital", "semi-important", "unimportant"]

export type Reviewer = {
  id: string
  name: string
  initials: string
  kind: string
}

/** One reviewer's decision on one model claim, with the grade it amounts to. */
export type Judgment = {
  reviewerId: string
  /** The reviewer's grade, or the model's when they left it alone. */
  effectiveLabel: Label
  /** True when the reviewer's grade differs from the model's. */
  changed: boolean
  looksGood: boolean
  issue: string | null
  duplicate: boolean
  multipleFacts: boolean
}

/** How many reviewers picked each review choice on one claim. */
export type ChoiceCounts = {
  looksGood: number
  issue: number
  duplicate: number
  multipleFacts: number
}

export type ClaimSummary = {
  position: number
  claim: string
  spans: ResponseClaimSpan[]
  proposedLabel: Label
  judgments: Judgment[]
  /** How many reviewers ended up on each grade. */
  labelVotes: Record<Label, number>
  choiceCounts: ChoiceCounts
  /** A reviewer graded the claim differently from the model. */
  disagreement: boolean
  /** Two or more reviewers graded the claim, and not all gave the same grade. */
  reviewersSplit: boolean
  /** Grades on the claim that are not its most common grade (0 unless two or more graded it). */
  minorityGrades: number
  /** Reviewers who chose Extraction issue, Duplicate, or Multiple Facts. */
  flaggedReviews: number
  /** At least one reviewer flagged the claim. */
  flagged: boolean
}

/** A claim one reviewer added to a task. */
type AddedClaim = {
  reviewerId: string
  claim: HumanClaim
}

export type TaskSummary = {
  id: number
  /** The review task number shown on the review page, when one exists. */
  taskId: number | null
  caseId: string
  armId: string
  modelId: string
  question: string | null
  response: string
  claims: ClaimSummary[]
  reviews: AdminExportCorrectionReview[]
  reviewerIds: string[]
  added: AddedClaim[]
  /** Model claims at least one reviewer graded. */
  judged: number
  /** Reviewer grades on the model's claims. */
  judgments: number
  /** Model claims with a reviewer grade different from the model's. */
  disagreeCount: number
  /** Reviewer grades that differ from the model's. */
  changedGrades: number
  /** Model claims graded by two or more reviewers, and how many of those split them. */
  multiGraded: number
  splitCount: number
  /** Reviewer grades on those claims, and how many sit outside the most common grade. */
  multiGradedGrades: number
  minorityGrades: number
  /** Model claims at least one reviewer flagged. */
  flaggedClaims: number
  /** Reviews (one per reviewer per claim) that chose a flag. */
  flaggedReviews: number
  /** Distinct claims reviewers added; the same text from two reviewers counts once. */
  addedClaims: number
  /** Of those, claims that at least one reviewer made by splitting a model claim. */
  addedFromSplits: number
  /** Additions counted per reviewer. */
  additions: number
  /** At least one review was submitted for the task. */
  reviewed: boolean
}

export type GradesTotals = {
  tasks: number
  reviewed: number
  unreviewed: number
  /** Model claims across every task, reviewed or not. */
  claims: number
  /** Reviews submitted, one per reviewer per task. */
  reviews: number
  /** Reviewer grades on the model's claims, and how many differ from the model's. */
  judgments: number
  changed: number
  /** Of the changed grades, how many moved toward vital and how many away. */
  gradedUp: number
  gradedDown: number
  /** Model claims at least one reviewer graded. */
  judged: number
  /** Model claims with a reviewer grade different from the model's. */
  disagreements: number
  /** Model claims graded by two or more reviewers, and how many of those split them. */
  multiGraded: number
  reviewerSplits: number
  /** Reviewer grades on those claims, and how many sit outside the most common grade. */
  multiGradedGrades: number
  minorityGrades: number
  /** Model claims at least one reviewer flagged. */
  flaggedClaims: number
  /** Reviews (one per reviewer per claim) that chose a flag. */
  flaggedReviews: number
  issues: number
  duplicates: number
  multipleFacts: number
  /** Distinct claims reviewers added, how many came from splits, and the per-reviewer count. */
  added: number
  splits: number
  additions: number
}

export type GradesModel = {
  tasks: TaskSummary[]
  reviewers: Reviewer[]
  reviewerById: Record<string, Reviewer>
  totals: GradesTotals
}

function isCorrectionItem(
  item: ExportItem,
): item is AdminExportModelCorrectionItem {
  return item.review_mode === "MODEL_LABEL_CORRECTION"
}

function initialsOf(name: string): string {
  const parts = name
    .replace(/[@.].*$/, "")
    .split(/[\s_-]+/)
    .filter(Boolean)
  if (parts.length === 0) return "?"
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase()
  return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase()
}

function buildReviewer(
  id: string,
  users: Map<string, AdminUserMetric>,
  names: Record<string, string>,
): Reviewer {
  const user = users.get(id)
  const email = user?.email ?? ""
  const name = names[id] ?? (email ? email.split("@")[0] : id.slice(0, 8))
  return {
    id,
    name,
    initials: initialsOf(name),
    kind: user?.reviewer_kind ?? "human",
  }
}

function emptyVotes(): Record<Label, number> {
  return { vital: 0, "semi-important": 0, unimportant: 0 }
}

/**
 * One table row per distinct added claim. Reviewers who typed the same text
 * (ignoring case, punctuation and spacing) share a row, each keeping their
 * own grade and split origin.
 */
export type AddedClaimGroup = {
  text: string
  reviewerIds: string[]
  byReviewer: Record<string, HumanClaim>
  labelVotes: Record<Label, number>
}

export function groupAddedClaims(added: AddedClaim[]): AddedClaimGroup[] {
  const groups = new Map<string, AddedClaimGroup>()
  for (const entry of added) {
    const key = normalizeClaimText(entry.claim.claim_text)
    let group = groups.get(key)
    if (!group) {
      group = {
        text: entry.claim.claim_text,
        reviewerIds: [],
        byReviewer: {},
        labelVotes: emptyVotes(),
      }
      groups.set(key, group)
    }
    if (group.byReviewer[entry.reviewerId]) continue
    group.byReviewer[entry.reviewerId] = entry.claim
    group.reviewerIds.push(entry.reviewerId)
    group.labelVotes[entry.claim.label] += 1
  }
  return [...groups.values()]
}

/** The first position after the model's claims; added claims count up from here. */
export function addedPositionBase(task: TaskSummary): number {
  return (
    task.claims.reduce((max, claim) => Math.max(max, claim.position), -1) + 1
  )
}

/**
 * Every claim's spans with the position that picks its color: the model's
 * claims first, then one entry per distinct added claim.
 */
export function claimColorOwners(
  task: TaskSummary,
): { position: number; spans: ResponseClaimSpan[]; added: boolean }[] {
  const base = addedPositionBase(task)
  return [
    ...task.claims.map((claim) => ({
      position: claim.position,
      spans: claim.spans,
      added: false,
    })),
    ...groupAddedClaims(task.added).map((group, i) => ({
      position: base + i,
      spans: Object.values(group.byReviewer).flatMap(
        (claim) => claim.response_spans,
      ),
      added: true,
    })),
  ]
}

export function normalizeClaimText(text: string): string {
  return text
    .toLowerCase()
    .replace(/[^\p{L}\p{N}\s]/gu, " ")
    .replace(/\s+/g, " ")
    .trim()
}

/** The reviewer chose Extraction issue, Duplicate, or Multiple facts. */
function judgmentFlagged(judgment: Judgment): boolean {
  return judgment.issue !== null || judgment.duplicate || judgment.multipleFacts
}

function summarizeClaim(
  claim: AdminExportModelCorrectionItem["claims"][number],
  reviews: AdminExportCorrectionReview[],
): ClaimSummary {
  const judgments: Judgment[] = []
  for (const review of reviews) {
    const decision = review.claim_reviews.find(
      (entry) => entry.position === claim.position,
    )
    if (!decision) continue
    const effectiveLabel = (decision.label ?? claim.proposed_label) as Label
    judgments.push({
      reviewerId: review.user_id,
      effectiveLabel,
      changed: effectiveLabel !== claim.proposed_label,
      looksGood: Boolean(decision.looks_good),
      issue: decision.issue?.trim() ? decision.issue : null,
      duplicate: Boolean(decision.duplicate),
      multipleFacts: Boolean(decision.multiple_facts),
    })
  }
  const labelVotes = emptyVotes()
  const choiceCounts: ChoiceCounts = {
    looksGood: 0,
    issue: 0,
    duplicate: 0,
    multipleFacts: 0,
  }
  for (const judgment of judgments) {
    labelVotes[judgment.effectiveLabel] += 1
    if (judgment.looksGood) choiceCounts.looksGood += 1
    if (judgment.issue) choiceCounts.issue += 1
    if (judgment.duplicate) choiceCounts.duplicate += 1
    if (judgment.multipleFacts) choiceCounts.multipleFacts += 1
  }
  const flaggedReviews = judgments.filter(judgmentFlagged).length
  return {
    position: claim.position,
    claim: claim.claim,
    spans: claim.spans,
    proposedLabel: claim.proposed_label,
    judgments,
    labelVotes,
    choiceCounts,
    disagreement: judgments.some((judgment) => judgment.changed),
    reviewersSplit:
      judgments.length >= 2 &&
      LABELS.filter((label) => labelVotes[label] > 0).length > 1,
    minorityGrades:
      judgments.length >= 2
        ? judgments.length -
          Math.max(...LABELS.map((label) => labelVotes[label]))
        : 0,
    flaggedReviews,
    flagged: flaggedReviews > 0,
  }
}

function summarizeTask(item: AdminExportModelCorrectionItem): TaskSummary {
  const reviews = item.correction_reviews
  const claims = item.claims.map((claim) => summarizeClaim(claim, reviews))
  const added: AddedClaim[] = reviews.flatMap((review) =>
    review.human_claims.map((claim) => ({ reviewerId: review.user_id, claim })),
  )
  const addedGroups = groupAddedClaims(added)
  const judgments = claims.flatMap((claim) => claim.judgments)
  const multiGradedClaims = claims.filter(
    (claim) => claim.judgments.length >= 2,
  )
  return {
    id: item.id,
    taskId: item.review_task?.id ?? null,
    caseId: item.case_id,
    armId: item.arm_id,
    modelId: item.generator.model_id,
    question: item.user_prompt,
    response: item.assistant_response,
    claims,
    reviews,
    reviewerIds: reviews.map((review) => review.user_id),
    added,
    judged: claims.filter((claim) => claim.judgments.length > 0).length,
    judgments: judgments.length,
    disagreeCount: claims.filter((claim) => claim.disagreement).length,
    changedGrades: judgments.filter((judgment) => judgment.changed).length,
    multiGraded: multiGradedClaims.length,
    splitCount: claims.filter((claim) => claim.reviewersSplit).length,
    multiGradedGrades: multiGradedClaims.reduce(
      (count, claim) => count + claim.judgments.length,
      0,
    ),
    minorityGrades: multiGradedClaims.reduce(
      (count, claim) => count + claim.minorityGrades,
      0,
    ),
    flaggedClaims: claims.filter((claim) => claim.flagged).length,
    flaggedReviews: judgments.filter(judgmentFlagged).length,
    addedClaims: addedGroups.length,
    addedFromSplits: addedGroups.filter((group) =>
      Object.values(group.byReviewer).some(
        (claim) => claim.split_from_position != null,
      ),
    ).length,
    additions: added.length,
    reviewed: reviews.length > 0,
  }
}

export function buildGradesModel(
  items: ExportItem[],
  users: AdminUserMetric[],
  userNames: Record<string, string>,
): GradesModel {
  const usersById = new Map(users.map((user) => [user.user_id, user]))
  const tasks = items.filter(isCorrectionItem).map(summarizeTask)

  const reviewers = [...new Set(tasks.flatMap((task) => task.reviewerIds))]
    .map((id) => buildReviewer(id, usersById, userNames))
    .sort((a, b) => a.name.localeCompare(b.name))
  const reviewerById = Object.fromEntries(
    reviewers.map((reviewer) => [reviewer.id, reviewer]),
  )

  const totals: GradesTotals = {
    tasks: tasks.length,
    reviewed: 0,
    unreviewed: 0,
    claims: 0,
    reviews: 0,
    judgments: 0,
    changed: 0,
    gradedUp: 0,
    gradedDown: 0,
    judged: 0,
    disagreements: 0,
    multiGraded: 0,
    reviewerSplits: 0,
    multiGradedGrades: 0,
    minorityGrades: 0,
    flaggedClaims: 0,
    flaggedReviews: 0,
    issues: 0,
    duplicates: 0,
    multipleFacts: 0,
    added: 0,
    splits: 0,
    additions: 0,
  }
  for (const task of tasks) {
    if (task.reviewed) totals.reviewed += 1
    else totals.unreviewed += 1
    totals.claims += task.claims.length
    totals.reviews += task.reviews.length
    totals.judged += task.judged
    totals.judgments += task.judgments
    totals.changed += task.changedGrades
    totals.disagreements += task.disagreeCount
    totals.multiGraded += task.multiGraded
    totals.reviewerSplits += task.splitCount
    totals.multiGradedGrades += task.multiGradedGrades
    totals.minorityGrades += task.minorityGrades
    totals.flaggedClaims += task.flaggedClaims
    totals.flaggedReviews += task.flaggedReviews
    totals.added += task.addedClaims
    totals.splits += task.addedFromSplits
    totals.additions += task.additions
    for (const claim of task.claims) {
      totals.issues += claim.choiceCounts.issue
      totals.duplicates += claim.choiceCounts.duplicate
      totals.multipleFacts += claim.choiceCounts.multipleFacts
      for (const judgment of claim.judgments) {
        if (!judgment.changed) continue
        // LABELS runs from vital down, so a smaller index means more important.
        if (
          LABELS.indexOf(judgment.effectiveLabel) <
          LABELS.indexOf(claim.proposedLabel)
        ) {
          totals.gradedUp += 1
        } else {
          totals.gradedDown += 1
        }
      }
    }
  }

  return { tasks, reviewers, reviewerById, totals }
}

export function percent(part: number, whole: number): string {
  if (whole === 0) return "—"
  return `${Math.round((part / whole) * 100)}%`
}
