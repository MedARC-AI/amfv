import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { createRouter, RouterProvider } from "@tanstack/react-router"
import { StrictMode } from "react"
import ReactDOM from "react-dom/client"
import { OpenAPI } from "./client"
import { Toaster } from "./components/ui/sonner"
import "./index.css"
import { clearAccessToken, getAccessToken } from "./lib/auth"
import { routeTree } from "./routeTree.gen"

OpenAPI.BASE = import.meta.env.VITE_API_URL || ""
OpenAPI.TOKEN = async () => {
  return getAccessToken() || ""
}

const queryClient = new QueryClient()

// The generated client runs this for direct commands as well as cached queries.
OpenAPI.interceptors.response.use((response) => {
  const token = getAccessToken()
  if (
    response.status === 401 &&
    token &&
    response.config.headers.Authorization === `Bearer ${token}`
  ) {
    clearAccessToken()
    queryClient.clear()
    window.location.href = "/login"
  }
  return response
})

const router = createRouter({
  routeTree,
  context: {
    queryClient,
  },
})
// A token change in another tab must not reuse the previous account's cache.
window.addEventListener("storage", (event) => {
  if (event.key === "access_token" || event.key === null) {
    queryClient.clear()
    window.location.reload()
  }
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
