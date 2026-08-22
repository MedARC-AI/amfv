/**
 * Repeating color palette used to visually tie each added eval item to its
 * highlighted evidence in the source document. The hues stay low-saturation and
 * lightly tinted so they read as quiet backgrounds rather than bright markers,
 * keeping long source documents easy on the eyes while still letting each item
 * be told apart. The order is arranged so adjacent items contrast, then wraps.
 *
 * Indigo/blue are deliberately excluded: the in-progress selection highlight
 * uses the indigo `primary` color, so item colors stay clear of it to remain
 * distinct from the live selection. Pale hues (amber, sky, cyan, lime) carry
 * slightly higher opacity so they don't wash out against the page. Every class
 * string is written out in full (no interpolation) so Tailwind keeps them in the
 * build.
 */
export type EvalItemColor = {
  /** Inline <mark> highlight for exact (word-for-word) evidence. */
  mark: string
  /** Block-level highlight for paragraph/block evidence. */
  block: string
  /** Accent applied to the eval item card in the sidebar list. */
  card: string
  /** Small swatch shown next to the eval item title. */
  dot: string
}

export const EVAL_ITEM_PALETTE: EvalItemColor[] = [
  {
    mark: "bg-violet-500/8 ring-violet-500/25",
    block: "border-violet-500/40 bg-violet-500/8",
    card: "border-l-violet-400 bg-violet-500/5",
    dot: "bg-violet-400",
  },
  {
    mark: "bg-teal-500/8 ring-teal-500/25",
    block: "border-teal-500/40 bg-teal-500/8",
    card: "border-l-teal-400 bg-teal-500/5",
    dot: "bg-teal-400",
  },
  {
    mark: "bg-rose-500/8 ring-rose-500/25",
    block: "border-rose-500/40 bg-rose-500/8",
    card: "border-l-rose-400 bg-rose-500/5",
    dot: "bg-rose-400",
  },
  {
    mark: "bg-sky-500/12 ring-sky-500/30",
    block: "border-sky-500/45 bg-sky-500/10",
    card: "border-l-sky-400 bg-sky-500/5",
    dot: "bg-sky-400",
  },
  {
    mark: "bg-emerald-500/8 ring-emerald-500/25",
    block: "border-emerald-500/40 bg-emerald-500/8",
    card: "border-l-emerald-400 bg-emerald-500/5",
    dot: "bg-emerald-400",
  },
  {
    mark: "bg-fuchsia-500/8 ring-fuchsia-500/25",
    block: "border-fuchsia-500/40 bg-fuchsia-500/8",
    card: "border-l-fuchsia-400 bg-fuchsia-500/5",
    dot: "bg-fuchsia-400",
  },
  {
    mark: "bg-stone-500/8 ring-stone-500/25",
    block: "border-stone-500/40 bg-stone-500/8",
    card: "border-l-stone-400 bg-stone-500/5",
    dot: "bg-stone-400",
  },
  {
    mark: "bg-orange-500/8 ring-orange-500/25",
    block: "border-orange-500/40 bg-orange-500/8",
    card: "border-l-orange-400 bg-orange-500/5",
    dot: "bg-orange-400",
  },
  {
    mark: "bg-cyan-500/12 ring-cyan-500/30",
    block: "border-cyan-500/45 bg-cyan-500/10",
    card: "border-l-cyan-400 bg-cyan-500/5",
    dot: "bg-cyan-400",
  },
  {
    mark: "bg-lime-500/12 ring-lime-500/30",
    block: "border-lime-500/45 bg-lime-500/10",
    card: "border-l-lime-400 bg-lime-500/5",
    dot: "bg-lime-400",
  },
  {
    mark: "bg-pink-500/8 ring-pink-500/25",
    block: "border-pink-500/40 bg-pink-500/8",
    card: "border-l-pink-400 bg-pink-500/5",
    dot: "bg-pink-400",
  },
  {
    mark: "bg-red-500/8 ring-red-500/25",
    block: "border-red-500/40 bg-red-500/8",
    card: "border-l-red-400 bg-red-500/5",
    dot: "bg-red-400",
  },
  {
    mark: "bg-purple-500/8 ring-purple-500/25",
    block: "border-purple-500/40 bg-purple-500/8",
    card: "border-l-purple-400 bg-purple-500/5",
    dot: "bg-purple-400",
  },
  {
    mark: "bg-green-500/8 ring-green-500/25",
    block: "border-green-500/40 bg-green-500/8",
    card: "border-l-green-400 bg-green-500/5",
    dot: "bg-green-400",
  },
  {
    mark: "bg-slate-500/8 ring-slate-500/25",
    block: "border-slate-500/40 bg-slate-500/8",
    card: "border-l-slate-400 bg-slate-500/5",
    dot: "bg-slate-400",
  },
  {
    mark: "bg-amber-500/12 ring-amber-500/30",
    block: "border-amber-500/45 bg-amber-500/10",
    card: "border-l-amber-400 bg-amber-500/5",
    dot: "bg-amber-400",
  },
]

/** Pick the palette entry for an item at the given (zero-based) position. */
export function evalItemColor(index: number): EvalItemColor {
  return EVAL_ITEM_PALETTE[index % EVAL_ITEM_PALETTE.length]
}

/** DOM id for an added eval item card, used as a scroll target. */
export function evalItemDomId(itemId: string): string {
  return `eval-item-${itemId}`
}
