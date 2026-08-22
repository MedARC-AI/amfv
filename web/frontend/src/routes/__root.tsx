import type { QueryClient } from "@tanstack/react-query"
import {
  createRootRouteWithContext,
  HeadContent,
  Outlet,
} from "@tanstack/react-router"
import { lazy, Suspense } from "react"
import ErrorComponent from "@/components/Common/ErrorComponent"
import NotFound from "@/components/Common/NotFound"

interface RouterContext {
  queryClient: QueryClient
}

const Devtools = import.meta.env.DEV
  ? lazy(async () => {
      const [{ ReactQueryDevtools }, { TanStackRouterDevtools }] =
        await Promise.all([
          import("@tanstack/react-query-devtools"),
          import("@tanstack/react-router-devtools"),
        ])

      return {
        default: () => (
          <>
            <TanStackRouterDevtools position="bottom-right" />
            <ReactQueryDevtools initialIsOpen={false} />
          </>
        ),
      }
    })
  : null

export const Route = createRootRouteWithContext<RouterContext>()({
  component: () => (
    <>
      <HeadContent />
      <Outlet />
      {Devtools ? (
        <Suspense fallback={null}>
          <Devtools />
        </Suspense>
      ) : null}
    </>
  ),
  notFoundComponent: () => <NotFound />,
  errorComponent: () => <ErrorComponent />,
})
