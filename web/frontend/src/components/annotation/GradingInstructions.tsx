import * as React from "react"
import type { ImportanceGuide } from "@/client/types.gen"

const preferenceKey = "fact-decomposition-grading-instructions-open"

export function GradingInstructions({ guide }: { guide: ImportanceGuide }) {
  const contentId = React.useId()
  const [open, setOpen] = React.useState(() => {
    try {
      return localStorage.getItem(preferenceKey) !== "false"
    } catch {
      return true
    }
  })
  return (
    <section
      className="rounded-xl border bg-card p-4"
      aria-label="Grading instructions"
    >
      <button
        type="button"
        aria-controls={contentId}
        aria-expanded={open}
        className="cursor-pointer font-semibold"
        onClick={(event) => {
          event.preventDefault()
          const value = !open
          setOpen(value)
          try {
            localStorage.setItem(preferenceKey, String(value))
          } catch {
            // Instructions remain usable when browser storage is unavailable.
          }
        }}
      >
        <span aria-hidden="true">{open ? "▾ " : "▸ "}</span>Grading instructions
      </button>
      <div id={contentId} hidden={!open} className="mt-3 space-y-3 text-sm">
        {guide.instructions.map((instruction) => (
          <p key={instruction}>{instruction}</p>
        ))}
      </div>
    </section>
  )
}
