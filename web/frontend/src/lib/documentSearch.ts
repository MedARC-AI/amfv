import type { ChunkSummary } from "@/client"

export type DocumentSearchMatch = {
  chunkId: number
  end: number
  index: number
  start: number
}

export function findDocumentSearchMatches(
  chunks: ChunkSummary[],
  query: string,
): DocumentSearchMatch[] {
  const needle = query.trim().toLocaleLowerCase()
  if (needle.length === 0) {
    return []
  }

  const matches: DocumentSearchMatch[] = []
  for (const chunk of chunks) {
    const haystack = chunk.text.toLocaleLowerCase()
    let start = haystack.indexOf(needle)
    while (start !== -1) {
      matches.push({
        chunkId: chunk.id,
        end: start + needle.length,
        index: matches.length,
        start,
      })
      start = haystack.indexOf(needle, start + needle.length)
    }
  }
  return matches
}

export function searchMatchesByChunk(
  matches: DocumentSearchMatch[],
): Map<number, DocumentSearchMatch[]> {
  const matchesByChunk = new Map<number, DocumentSearchMatch[]>()
  for (const match of matches) {
    const chunkMatches = matchesByChunk.get(match.chunkId) ?? []
    chunkMatches.push(match)
    matchesByChunk.set(match.chunkId, chunkMatches)
  }
  return matchesByChunk
}
