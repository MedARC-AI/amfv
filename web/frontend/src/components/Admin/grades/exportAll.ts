import {
  type AdminExport,
  type AdminExportAuthoredItem,
  type AdminExportModelCorrectionItem,
  AdminService,
  type AdminUserMetric,
  UsersService,
} from "@/client"
import { hasApiErrorStatus } from "@/utils"

export type ExportItem =
  | AdminExportAuthoredItem
  | AdminExportModelCorrectionItem

/** One self-contained file: every export page plus who the reviewers are. */
export type ExportBundle = {
  schema_version: 1
  exported_at: string
  dataset_id: number | null
  total: number
  users: AdminUserMetric[]
  user_names: Record<string, string>
  items: ExportItem[]
}

export type ExportProgress = { loaded: number; total: number | null }

const INITIAL_PAGE_SIZE = 100
const USER_PAGE_SIZE = 50

function isLastPage(page: { next_offset?: number | null }): boolean {
  return page.next_offset === null || page.next_offset === undefined
}

/**
 * Walk every export page. The server caps a page by serialized size and
 * answers 413 when a page is too big, so the page size halves until it fits.
 */
async function fetchAllExportItems(
  datasetId: number | null,
  onProgress?: (progress: ExportProgress) => void,
): Promise<{ items: ExportItem[]; total: number }> {
  const items: ExportItem[] = []
  let offset = 0
  let limit = INITIAL_PAGE_SIZE
  let total = 0
  for (;;) {
    let page: AdminExport
    try {
      page = await AdminService.exportDataset({ datasetId, limit, offset })
    } catch (error) {
      if (hasApiErrorStatus(error, 413) && limit > 1) {
        limit = Math.max(1, Math.floor(limit / 2))
        continue
      }
      throw error
    }
    total = page.total
    const pageItems = page.items ?? []
    items.push(...pageItems)
    onProgress?.({ loaded: items.length, total })
    if (isLastPage(page) || pageItems.length === 0) break
    offset = page.next_offset as number
  }
  return { items, total }
}

async function fetchAllUserMetrics(): Promise<AdminUserMetric[]> {
  const users: AdminUserMetric[] = []
  let offset = 0
  for (;;) {
    const page = await AdminService.readUserMetrics({
      datasetId: null,
      limit: USER_PAGE_SIZE,
      offset,
    })
    const pageUsers = page.items ?? []
    users.push(...pageUsers)
    if (isLastPage(page) || pageUsers.length === 0) break
    offset = page.next_offset as number
  }
  return users
}

/** Full names come from the users API, which only superusers may read. */
async function fetchUserNames(): Promise<Record<string, string>> {
  const names: Record<string, string> = {}
  try {
    let skip = 0
    for (;;) {
      const page = await UsersService.readUsers({ skip, limit: 100 })
      for (const user of page.data) {
        if (user.full_name) names[user.id] = user.full_name
      }
      skip += page.data.length
      if (page.data.length === 0 || skip >= page.count) break
    }
  } catch {
    return names
  }
  return names
}

export async function fetchExportBundle(
  datasetId: number | null,
  onProgress?: (progress: ExportProgress) => void,
): Promise<ExportBundle> {
  const [{ items, total }, users, userNames] = await Promise.all([
    fetchAllExportItems(datasetId, onProgress),
    fetchAllUserMetrics(),
    fetchUserNames(),
  ])
  return {
    schema_version: 1,
    exported_at: new Date().toISOString(),
    dataset_id: datasetId,
    total,
    users,
    user_names: userNames,
    items,
  }
}

export function downloadJson(data: unknown, filename: string) {
  const blob = new Blob([JSON.stringify(data, null, 2)], {
    type: "application/json",
  })
  const url = URL.createObjectURL(blob)
  const link = document.createElement("a")
  link.href = url
  link.download = filename
  document.body.append(link)
  link.click()
  link.remove()
  URL.revokeObjectURL(url)
}
