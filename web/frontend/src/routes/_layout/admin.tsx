import { createFileRoute, redirect } from "@tanstack/react-router"
import AmfvAdminPage from "@/components/Admin/AmfvAdminPage"
import { requireAuthenticated } from "@/lib/routeGuards"

export const Route = createFileRoute("/_layout/admin")({
  component: AmfvAdminPage,
  beforeLoad: async ({ context }) => {
    const user = await requireAuthenticated(context.queryClient)
    const role = user.role ?? "user"
    if (!user.is_superuser && !["admin", "data_admin"].includes(role)) {
      throw redirect({
        to: "/",
      })
    }
  },
  head: () => ({
    meta: [
      {
        title: "Admin - AMFV Web",
      },
    ],
  }),
})
