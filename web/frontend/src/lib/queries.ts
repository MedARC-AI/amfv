import { queryOptions } from "@tanstack/react-query"

import { HomeService, UsersService } from "@/client"

export const currentUserQueryKey = ["currentUser"] as const

export const currentUserQueryOptions = queryOptions({
  queryKey: currentUserQueryKey,
  queryFn: () => UsersService.readUserMe(),
  staleTime: 5 * 60 * 1000,
  retry: false,
})

export const homeSummaryQueryKey = ["home-summary"] as const

export const homeSummaryQueryOptions = queryOptions({
  queryKey: homeSummaryQueryKey,
  queryFn: () => HomeService.readHomeSummary(),
  staleTime: 60 * 1000,
})
