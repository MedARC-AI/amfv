import { createFileRoute, Outlet, useRouterState } from "@tanstack/react-router"

import AppSidebar from "@/components/Sidebar/AppSidebar"
import {
  SidebarInset,
  SidebarProvider,
  SidebarTrigger,
} from "@/components/ui/sidebar"
import { requireAuthenticated } from "@/lib/routeGuards"

const pageTitles: Record<string, string> = {
  "/": "Home",
  "/admin": "Admin",
  "/create": "Create",
  "/create/fact-decomposition": "Create Fact Decomposition Eval Item",
  "/create/retrieval": "Create Retrieval Eval Item",
  "/my-work": "My Work",
  "/review": "Review",
  "/review/fact-decomposition": "Fact Decomposition Review",
  "/review/relevance": "Relevance Review",
  "/review/retrieval": "Retrieval Review",
  "/settings": "User Settings",
}

export const Route = createFileRoute("/_layout")({
  component: Layout,
  beforeLoad: async ({ context }) => {
    await requireAuthenticated(context.queryClient)
  },
})

function Layout() {
  const pathname = useRouterState({
    select: (state) => state.location.pathname,
  })
  const pageTitle = pageTitles[pathname] ?? null

  return (
    <SidebarProvider>
      <AppSidebar />
      <SidebarInset className="h-svh overflow-hidden">
        <header className="bg-background z-10 flex h-16 shrink-0 items-center gap-2 border-b px-4">
          <SidebarTrigger className="-ml-1 text-muted-foreground" />
          {pageTitle ? (
            <h1 className="ml-4 text-2xl font-semibold tracking-normal">
              {pageTitle}
            </h1>
          ) : null}
        </header>
        <main className="flex-1 overflow-y-auto p-6 md:p-8">
          <div className="mx-auto max-w-[120rem]">
            <Outlet />
          </div>
        </main>
      </SidebarInset>
    </SidebarProvider>
  )
}

export default Layout
