import type { ChoiceCounts } from "./gradesModel"

/** The grade buttons on the review page, as the table headers list them. */
export const GRADE_OPTIONS = ["Vital", "Semi-important", "Unimportant"]

/** The review buttons on the review page, in their order, keyed to the counts. */
export const REVIEW_CHOICES: { key: keyof ChoiceCounts; text: string }[] = [
  { key: "looksGood", text: "Looks good" },
  { key: "issue", text: "Extraction issue" },
  { key: "duplicate", text: "Duplicate" },
  { key: "multipleFacts", text: "Multiple Facts" },
]

export const REVIEW_OPTIONS = REVIEW_CHOICES.map((choice) => choice.text)
