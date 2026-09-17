import { useCallback, useEffect, useRef } from "react"

/** Bind async continuations to this editor visit, including resets and unmounts. */
export function useEditorLifetime(identity: number | undefined | null) {
  const current = useRef({ identity, generation: 0 })
  if (current.current.identity !== identity) {
    current.current = { identity, generation: current.current.generation + 1 }
  }
  const invalidate = useCallback(() => {
    current.current.generation += 1
  }, [])
  useEffect(() => invalidate, [invalidate])
  const capture = useCallback(() => {
    const generation = current.current.generation
    return () => current.current.generation === generation
  }, [])
  return { capture, invalidate }
}
