import DatasetsAdmin from "@/components/Admin/DatasetsAdmin"
import DocumentsAdmin from "@/components/Admin/DocumentsAdmin"
import ExportAdmin from "@/components/Admin/ExportAdmin"
import MetricsAdmin from "@/components/Admin/MetricsAdmin"
import ModerationAdmin from "@/components/Admin/ModerationAdmin"
import TaskGenerationAdmin from "@/components/Admin/TaskGenerationAdmin"
import UsersAdmin from "@/components/Admin/UsersAdmin"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import useAuth from "@/hooks/useAuth"

export default function AmfvAdminPage() {
  const { user } = useAuth()
  const canManageUsers = user?.is_superuser || user?.role === "admin"
  const defaultTab = canManageUsers ? "users" : "datasets"

  return (
    <div className="flex flex-col gap-6">
      <Tabs key={defaultTab} defaultValue={defaultTab} className="gap-6">
        <TabsList>
          {canManageUsers ? (
            <TabsTrigger value="users">Users</TabsTrigger>
          ) : null}
          <TabsTrigger value="datasets">Datasets</TabsTrigger>
          <TabsTrigger value="documents">Documents</TabsTrigger>
          <TabsTrigger value="moderation">Moderation</TabsTrigger>
          <TabsTrigger value="tasks">Tasks</TabsTrigger>
          <TabsTrigger value="export">Export</TabsTrigger>
          <TabsTrigger value="metrics">Metrics</TabsTrigger>
        </TabsList>

        {canManageUsers ? (
          <TabsContent value="users">
            <UsersAdmin />
          </TabsContent>
        ) : null}

        <TabsContent value="datasets">
          <DatasetsAdmin />
        </TabsContent>

        <TabsContent value="documents">
          <DocumentsAdmin canImport={Boolean(canManageUsers)} />
        </TabsContent>

        <TabsContent value="moderation">
          <ModerationAdmin />
        </TabsContent>

        <TabsContent value="tasks">
          <TaskGenerationAdmin />
        </TabsContent>

        <TabsContent value="export">
          <ExportAdmin />
        </TabsContent>

        <TabsContent value="metrics">
          <MetricsAdmin />
        </TabsContent>
      </Tabs>
    </div>
  )
}
