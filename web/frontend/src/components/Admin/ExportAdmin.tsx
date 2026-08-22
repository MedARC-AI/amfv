import { useMutation, useQuery } from "@tanstack/react-query"
import { Download } from "lucide-react"
import { useState } from "react"

import { type AdminExport, AdminService } from "@/client"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Label } from "@/components/ui/label"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { apiErrorMessage } from "@/utils"

const EXPORT_PAGE_SIZE = 50

type ExportRequest = {
  datasetId: number | null
  offset: number
}

function pageRange(offset: number, itemCount: number, total: number): string {
  if (total === 0) {
    return "No export records"
  }
  return `Records ${offset + 1}-${offset + itemCount} of ${total}`
}

function downloadPage(exportData: AdminExport) {
  const blob = new Blob([JSON.stringify(exportData, null, 2)], {
    type: "application/json",
  })
  const url = URL.createObjectURL(blob)
  const link = document.createElement("a")
  link.href = url
  link.download = `amfv-export-${exportData.dataset_id ?? "all"}-offset-${exportData.offset}.json`
  document.body.append(link)
  link.click()
  link.remove()
  URL.revokeObjectURL(url)
}

export default function ExportAdmin() {
  const [datasetId, setDatasetId] = useState("all")
  const [exportData, setExportData] = useState<AdminExport | null>(null)
  const [errorMessage, setErrorMessage] = useState<string | null>(null)
  const datasetsQuery = useQuery({
    queryKey: ["admin-datasets"],
    queryFn: () => AdminService.readAdminDatasets(),
  })
  const exportDataset = useMutation({
    mutationFn: ({ datasetId, offset }: ExportRequest) =>
      AdminService.exportDataset({
        datasetId,
        offset,
        limit: EXPORT_PAGE_SIZE,
      }),
    onSuccess: (data) => {
      setExportData(data)
      setErrorMessage(null)
    },
    onError: (error) => setErrorMessage(apiErrorMessage(error)),
  })

  const selectedDatasetId = datasetId === "all" ? null : Number(datasetId)
  const items = exportData?.items ?? []
  const loadPage = (offset: number) => {
    exportDataset.mutate({ datasetId: selectedDatasetId, offset })
  }

  return (
    <div className="grid gap-6 xl:grid-cols-[0.75fr_1.25fr]">
      <section className="rounded-md border p-5">
        <div className="mb-5 flex items-center gap-3">
          <Download className="text-muted-foreground size-5" />
          <div>
            <h1 className="text-2xl font-bold tracking-tight">Export</h1>
            <p className="text-muted-foreground text-sm">
              Preview and download one bounded export page at a time
            </p>
          </div>
        </div>
        <form
          className="flex flex-col gap-4"
          onSubmit={(event) => {
            event.preventDefault()
            loadPage(0)
          }}
        >
          <div className="grid gap-2">
            <Label>Dataset</Label>
            <Select
              onValueChange={(value) => {
                setDatasetId(value)
                setExportData(null)
                setErrorMessage(null)
              }}
              value={datasetId}
            >
              <SelectTrigger data-testid="admin-export-dataset">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">All datasets</SelectItem>
                {(datasetsQuery.data ?? []).map((dataset) => (
                  <SelectItem key={dataset.id} value={String(dataset.id)}>
                    {dataset.display_name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <Button disabled={exportDataset.isPending} type="submit">
            {exportDataset.isPending
              ? "Loading export page"
              : "Load export page"}
          </Button>
          {errorMessage ? (
            <p className="text-destructive text-sm" role="alert">
              {errorMessage}
            </p>
          ) : null}
        </form>
      </section>

      <section className="rounded-md border p-5">
        <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
          <div>
            <h2 className="text-base font-semibold tracking-normal">
              Export page preview
            </h2>
            {exportData ? (
              <p aria-live="polite" className="text-muted-foreground text-sm">
                {pageRange(exportData.offset, items.length, exportData.total)}
              </p>
            ) : null}
          </div>
          {exportData ? (
            <div className="flex items-center gap-2">
              <Badge variant="secondary">{items.length} records on page</Badge>
              <Button
                aria-label="Download current export page"
                onClick={() => downloadPage(exportData)}
                size="sm"
                type="button"
                variant="outline"
              >
                <Download />
                Download page
              </Button>
            </div>
          ) : null}
        </div>
        {exportData ? (
          <div className="space-y-4">
            {items.length > 0 ? (
              <ol className="max-h-[32rem] space-y-3 overflow-auto pr-1">
                {items.map((item, index) => (
                  <li
                    className="rounded-md border bg-muted/20 p-3"
                    key={exportData.offset + index}
                  >
                    <div className="text-muted-foreground text-xs">
                      Record {exportData.offset + index + 1}
                    </div>
                    <pre className="mt-2 overflow-auto whitespace-pre-wrap break-words font-mono text-xs">
                      {JSON.stringify(item, null, 2)}
                    </pre>
                  </li>
                ))}
              </ol>
            ) : (
              <p className="text-muted-foreground text-sm">
                This export page has no records.
              </p>
            )}
            <nav
              aria-label="Export pages"
              className="flex flex-wrap items-center justify-between gap-3 border-t pt-4"
            >
              <Button
                disabled={exportDataset.isPending || exportData.offset === 0}
                onClick={() =>
                  loadPage(Math.max(0, exportData.offset - exportData.limit))
                }
                type="button"
                variant="outline"
              >
                Previous page
              </Button>
              <Button
                disabled={
                  exportDataset.isPending ||
                  exportData.next_offset === null ||
                  exportData.next_offset === undefined
                }
                onClick={() => {
                  if (
                    exportData.next_offset !== null &&
                    exportData.next_offset !== undefined
                  ) {
                    loadPage(exportData.next_offset)
                  }
                }}
                type="button"
                variant="outline"
              >
                Next page
              </Button>
            </nav>
          </div>
        ) : (
          <p className="text-muted-foreground text-sm">
            Load an export page to inspect up to {EXPORT_PAGE_SIZE} records.
          </p>
        )}
      </section>
    </div>
  )
}
