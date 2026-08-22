import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useNavigate } from "@tanstack/react-router"

import {
  type Body_login_login_access_token as AccessToken,
  AuthService,
  type InviteSignupRequest,
  LoginService,
} from "@/client"
import { clearAccessToken, isLoggedIn, setAccessToken } from "@/lib/auth"
import {
  currentUserQueryKey,
  currentUserQueryOptions,
  homeSummaryQueryKey,
} from "@/lib/queries"
import { handleError } from "@/utils"
import useCustomToast from "./useCustomToast"

const useAuth = () => {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const { showErrorToast } = useCustomToast()

  const { data: user } = useQuery({
    ...currentUserQueryOptions,
    enabled: isLoggedIn(),
  })

  const signUpMutation = useMutation({
    mutationFn: (data: InviteSignupRequest) =>
      AuthService.inviteSignup({ requestBody: data }),
    onSuccess: (response) => {
      setAccessToken(response.access_token)
      queryClient.invalidateQueries({ queryKey: currentUserQueryKey })
      navigate({ to: "/" })
    },
    onError: handleError.bind(showErrorToast),
    onSettled: () => {
      queryClient.invalidateQueries({ queryKey: ["users"] })
    },
  })

  const login = async (data: AccessToken) => {
    const response = await LoginService.loginAccessToken({
      formData: data,
    })
    setAccessToken(response.access_token)
  }

  const loginMutation = useMutation({
    mutationFn: login,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: currentUserQueryKey })
      navigate({ to: "/" })
    },
    onError: handleError.bind(showErrorToast),
  })

  const logout = () => {
    clearAccessToken()
    queryClient.removeQueries({ queryKey: currentUserQueryKey })
    queryClient.removeQueries({ queryKey: homeSummaryQueryKey })
    navigate({ to: "/login" })
  }

  return {
    signUpMutation,
    loginMutation,
    logout,
    user,
  }
}

export default useAuth
