import { useCallback, useState } from "react"

export function parseFactHistory(raw: string | null): number[] {
  try {
    const value: unknown = JSON.parse(raw ?? "[]")
    return Array.isArray(value)
      ? [
          ...new Set(
            value.filter(
              (id): id is number => Number.isSafeInteger(id) && id > 0,
            ),
          ),
        ].slice(-100)
      : []
  } catch {
    return []
  }
}

/** The caller mounts this hook only once identity has resolved, keyed by user. */
export function useFactReviewHistory(userId: string) {
  const key = `fact-review-history:${userId}`
  const [state, setState] = useState(() => {
    try {
      return {
        ids: parseFactHistory(sessionStorage.getItem(key)),
        unavailable: false,
      }
    } catch {
      return { ids: [] as number[], unavailable: true }
    }
  })
  const visit = useCallback(
    (id: number) => {
      setState((current) => {
        if (current.ids.includes(id)) return current
        const ids = [...current.ids, id].slice(-100)
        try {
          sessionStorage.setItem(key, JSON.stringify(ids))
          return { ids, unavailable: current.unavailable }
        } catch {
          return { ids, unavailable: true }
        }
      })
    },
    [key],
  )
  return { visited: state.ids, historyUnavailable: state.unavailable, visit }
}
