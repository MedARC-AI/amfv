import type { QueryClient } from "@tanstack/react-query"
import { redirect } from "@tanstack/react-router"

import { clearAccessToken, isLoggedIn } from "@/lib/auth"
import { currentUserQueryOptions } from "@/lib/queries"

export async function requireAuthenticated(queryClient: QueryClient) {
  if (!isLoggedIn()) {
    throw redirect({
      to: "/login",
    })
  }

  try {
    return await queryClient.ensureQueryData(currentUserQueryOptions)
  } catch {
    clearAccessToken()
    throw redirect({
      to: "/login",
    })
  }
}

export async function redirectIfAuthenticated(queryClient: QueryClient) {
  if (!isLoggedIn()) {
    return
  }

  try {
    await queryClient.ensureQueryData(currentUserQueryOptions)
  } catch {
    clearAccessToken()
    return
  }

  throw redirect({
    to: "/",
  })
}
