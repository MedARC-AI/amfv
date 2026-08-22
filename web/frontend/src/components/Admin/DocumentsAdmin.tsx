import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { FileText } from "lucide-react"
import { useMemo, useState } from "react"

import {
  AdminService,
  type DatasetSummary,
  type DocumentSummary,
} from "@/client"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"

export default function DocumentsAdmin() {
  const queryClient = useQueryClient()
  const [datasetId, setDatasetId] = useState("")
  const [title, setTitle] = useState("")
  const [externalId, setExternalId] = useState("")
  const [content, setContent] = useState("")
  const [selectedDocumentId, setSelectedDocumentId] = useState<number | null>(
    null,
  )
  const datasetsQuery = useQuery({
    queryKey: ["admin-datasets"],
    queryFn: () => AdminService.readAdminDatasets(),
  })
  const documentsQuery = useQuery({
    queryKey: ["admin-documents"],
    queryFn: () => AdminService.readAdminDocuments(),
  })
  const selectedDocumentQuery = useQuery({
    queryKey: ["admin-document", selectedDocumentId],
    queryFn: () =>
      AdminService.readAdminDocument({
        documentId: selectedDocumentId as number,
      }),
    enabled: selectedDocumentId !== null,
  })
  const createDocument = useMutation({
    mutationFn: () =>
      AdminService.createAdminDocument({
        requestBody: {
          dataset_id: Number(datasetId),
          title,
          external_id: externalId || null,
          content,
        },
      }),
    onSuccess: (document) => {
      setTitle("")
      setExternalId("")
      setContent("")
      setSelectedDocumentId(document.id)
      void queryClient.invalidateQueries({ queryKey: ["admin-documents"] })
    },
  })
  const toggleDocument = useMutation({
    mutationFn: (documentId: number) =>
      AdminService.toggleAdminDocument({ documentId }),
    onSuccess: (document) => {
      void queryClient.invalidateQueries({ queryKey: ["admin-documents"] })
      void queryClient.invalidateQueries({
        queryKey: ["admin-document", document.id],
      })
    },
  })
  const datasetsById = useMemo(() => {
    return new Map(
      (datasetsQuery.data ?? []).map((dataset) => [dataset.id, dataset]),
    )
  }, [datasetsQuery.data])
  const canSubmit =
    datasetId.length > 0 && title.trim().length > 0 && content.trim().length > 0

  return (
    <div className="grid gap-6 xl:grid-cols-[0.85fr_1.15fr]">
      <section className="rounded-md border p-5">
        <div className="mb-5 flex items-center gap-3">
          <FileText className="text-muted-foreground size-5" />
          <div>
            <h1 className="text-2xl font-bold tracking-tight">Documents</h1>
            <p className="text-muted-foreground text-sm">
              Complete source documents
            </p>
          </div>
        </div>
        <form
          className="flex flex-col gap-4"
          onSubmit={(event) => {
            event.preventDefault()
            if (canSubmit) {
              createDocument.mutate()
            }
          }}
        >
          <div className="grid gap-2">
            <Label>Dataset</Label>
            <Select value={datasetId} onValueChange={setDatasetId}>
              <SelectTrigger data-testid="admin-document-dataset">
                <SelectValue placeholder="Select dataset" />
              </SelectTrigger>
              <SelectContent>
                {(datasetsQuery.data ?? []).map((dataset) => (
                  <SelectItem key={dataset.id} value={String(dataset.id)}>
                    {dataset.display_name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="grid gap-2">
            <Label htmlFor="document-title">Title</Label>
            <Input
              id="document-title"
              value={title}
              onChange={(event) => setTitle(event.target.value)}
              placeholder="Clinical source note"
            />
          </div>
          <div className="grid gap-2">
            <Label htmlFor="document-external-id">External ID</Label>
            <Input
              id="document-external-id"
              value={externalId}
              onChange={(event) => setExternalId(event.target.value)}
              placeholder="source-001"
            />
          </div>
          <div className="grid gap-2">
            <Label htmlFor="document-content">Content</Label>
            <textarea
              id="document-content"
              className="border-input min-h-40 w-full rounded-md border bg-transparent px-3 py-2 text-sm shadow-xs outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50"
              value={content}
              onChange={(event) => setContent(event.target.value)}
            />
          </div>
          <Button
            type="submit"
            disabled={!canSubmit || createDocument.isPending}
          >
            Create document
          </Button>
          {createDocument.isError ? (
            <p className="text-destructive text-sm">
              Could not create document.
            </p>
          ) : null}
        </form>
      </section>

      <section className="flex flex-col gap-4">
        <div className="rounded-md border p-5">
          <h2 className="mb-4 text-base font-semibold tracking-normal">
            Source documents
          </h2>
          {documentsQuery.isLoading ? (
            <p className="text-muted-foreground text-sm">Loading documents</p>
          ) : null}
          {documentsQuery.isError ? (
            <p className="text-destructive text-sm">
              Could not load documents.
            </p>
          ) : null}
          <div className="grid gap-3">
            {(documentsQuery.data ?? []).map((document) => (
              <DocumentRow
                key={document.id}
                document={document}
                dataset={datasetsById.get(document.dataset_id)}
                onPreview={() => setSelectedDocumentId(document.id)}
                onToggle={() => toggleDocument.mutate(document.id)}
              />
            ))}
          </div>
        </div>

        {selectedDocumentQuery.data ? (
          <DocumentDetailPanel document={selectedDocumentQuery.data} />
        ) : null}
      </section>
    </div>
  )
}

function DocumentRow({
  document,
  dataset,
  onPreview,
  onToggle,
}: {
  document: DocumentSummary
  dataset: DatasetSummary | undefined
  onPreview: () => void
  onToggle: () => void
}) {
  return (
    <div
      className="rounded-md border p-4"
      data-testid={`admin-document-${document.external_id}`}
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h3 className="font-medium text-sm">{document.title}</h3>
          <p className="text-muted-foreground text-sm">
            {dataset?.display_name ?? `Dataset ${document.dataset_id}`}
          </p>
          <p className="text-muted-foreground text-xs">
            {document.external_id}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Badge variant={document.is_active ? "default" : "outline"}>
            {document.is_active ? "Active" : "Inactive"}
          </Badge>
          <Button type="button" variant="outline" size="sm" onClick={onPreview}>
            Preview
          </Button>
          <Button type="button" variant="outline" size="sm" onClick={onToggle}>
            {document.is_active ? "Deactivate" : "Activate"}
          </Button>
        </div>
      </div>
    </div>
  )
}

function DocumentDetailPanel({
  document,
}: {
  document: Awaited<ReturnType<typeof AdminService.readAdminDocument>>
}) {
  return (
    <div className="rounded-md border p-5">
      <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-base font-semibold tracking-normal">
            {document.title}
          </h2>
          <p className="text-muted-foreground text-sm">
            Complete document text
          </p>
        </div>
        <Badge variant={document.is_active ? "default" : "outline"}>
          {document.is_active ? "Active" : "Inactive"}
        </Badge>
      </div>
      <p className="text-muted-foreground max-h-32 overflow-auto whitespace-pre-wrap rounded-md border p-3 text-sm">
        {document.content}
      </p>
      <div className="mt-4 grid gap-3">
        {(document.chunks ?? []).map((chunk) => (
          <div key={chunk.id} className="rounded-md border p-3">
            <div className="text-muted-foreground mb-2 text-xs">
              Backing text {chunk.position}
            </div>
            <p className="whitespace-pre-wrap text-sm">{chunk.text}</p>
          </div>
        ))}
      </div>
    </div>
  )
}
