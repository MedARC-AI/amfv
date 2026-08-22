import {
  MutationCache,
  QueryCache,
  QueryClient,
  QueryClientProvider,
} from "@tanstack/react-query"
import { createRouter, RouterProvider } from "@tanstack/react-router"
import { StrictMode } from "react"
import ReactDOM from "react-dom/client"
import { ApiError, OpenAPI } from "./client"
import { Toaster } from "./components/ui/sonner"
import "./index.css"
import { clearAccessToken, getAccessToken } from "./lib/auth"
import { routeTree } from "./routeTree.gen"

OpenAPI.BASE = import.meta.env.VITE_API_URL || ""
OpenAPI.TOKEN = async () => {
  return getAccessToken() || ""
}

const handleApiError = (error: Error) => {
  if (error instanceof ApiError && [401, 403].includes(error.status)) {
    clearAccessToken()
    window.location.href = "/login"
  }
}
const queryClient = new QueryClient({
  queryCache: new QueryCache({
    onError: handleApiError,
  }),
  mutationCache: new MutationCache({
    onError: handleApiError,
  }),
})

const router = createRouter({
  routeTree,
  context: {
    queryClient,
  },
})
declare module "@tanstack/react-router" {
  interface Register {
    router: typeof router
  }
}

ReactDOM.createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
      <Toaster richColors closeButton />
    </QueryClientProvider>
  </StrictMode>,
)
