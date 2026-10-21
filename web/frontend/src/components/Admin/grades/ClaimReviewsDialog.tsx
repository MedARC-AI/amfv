import { Users } from "lucide-react"
import { Fragment, type ReactNode } from "react"

import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog"
import { cn } from "@/lib/utils"
import { ReviewerChip } from "./chips"
import type { Reviewer } from "./gradesModel"

type ReviewerEntry = {
  reviewer: Reviewer
  /** Position in the dataset's reviewer list, which picks the reviewer's color. */
  index: number
  body: ReactNode
}

/**
 * One claim's ratings, reviewer by reviewer, behind a button in the claims
 * table. The table row itself shows the totals.
 */
export default function ClaimReviewsDialog({
  claimText,
  dotClass,
  entries,
  title,
}: {
  claimText: string
  dotClass: string
  entries: ReviewerEntry[]
  title: string
}) {
  return (
    <Dialog>
      <DialogTrigger asChild>
        <Button
          className="whitespace-nowrap"
          size="sm"
          type="button"
          variant="outline"
        >
          <Users />
          By reviewer
        </Button>
      </DialogTrigger>
      <DialogContent className="max-h-[85svh] overflow-y-auto sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <span
              className={cn("inline-block size-3 rounded-full", dotClass)}
            />
            {title}
          </DialogTitle>
          <DialogDescription className="text-foreground text-sm">
            {claimText}
          </DialogDescription>
        </DialogHeader>
        <dl className="grid grid-cols-[auto_1fr] items-start gap-x-4 gap-y-4">
          {entries.map((entry) => (
            <Fragment key={entry.reviewer.id}>
              <dt className="pt-0.5">
                <ReviewerChip index={entry.index} reviewer={entry.reviewer} />
              </dt>
              <dd className="text-sm">{entry.body}</dd>
            </Fragment>
          ))}
        </dl>
      </DialogContent>
    </Dialog>
  )
}
