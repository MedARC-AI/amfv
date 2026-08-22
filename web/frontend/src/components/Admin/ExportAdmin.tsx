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

export default function ExportAdmin() {
  const [datasetId, setDatasetId] = useState("all")
  const [exportData, setExportData] = useState<AdminExport | null>(null)
  const datasetsQuery = useQuery({
    queryKey: ["admin-datasets"],
    queryFn: () => AdminService.readAdminDatasets(),
  })
  const exportDataset = useMutation({
    mutationFn: () =>
      AdminService.exportDataset({
        datasetId: datasetId === "all" ? null : Number(datasetId),
      }),
    onSuccess: setExportData,
  })
  const json = exportData ? JSON.stringify(exportData, null, 2) : ""

  return (
    <div className="grid gap-6 xl:grid-cols-[0.75fr_1.25fr]">
      <section className="rounded-md border p-5">
        <div className="mb-5 flex items-center gap-3">
          <Download className="text-muted-foreground size-5" />
          <div>
            <h1 className="text-2xl font-bold tracking-tight">Export</h1>
            <p className="text-muted-foreground text-sm">
              Preview dataset-scoped export payloads
            </p>
          </div>
        </div>
        <form
          className="flex flex-col gap-4"
          onSubmit={(event) => {
            event.preventDefault()
            exportDataset.mutate()
          }}
        >
          <div className="grid gap-2">
            <Label>Dataset</Label>
            <Select value={datasetId} onValueChange={setDatasetId}>
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
          <Button type="submit" disabled={exportDataset.isPending}>
            Load export
          </Button>
          {exportDataset.isError ? (
            <p className="text-destructive text-sm">Could not load export.</p>
          ) : null}
        </form>
      </section>

      <section className="rounded-md border p-5">
        <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
          <h2 className="text-base font-semibold tracking-normal">
            Export payload
          </h2>
          {exportData ? (
            <Badge variant="secondary">
              {(exportData.items ?? []).length} items
            </Badge>
          ) : null}
        </div>
        {exportData ? (
          <textarea
            readOnly
            className="border-input min-h-96 w-full rounded-md border bg-transparent px-3 py-2 font-mono text-xs shadow-xs outline-none"
            value={json}
          />
        ) : (
          <p className="text-muted-foreground text-sm">
            Load an export to inspect the JSON payload.
          </p>
        )}
      </section>
    </div>
  )
}
