import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useNavigate } from "@tanstack/react-router"

import {
  type Body_login_login_access_token as AccessToken,
  AuthService,
  type InviteSignupRequest,
  LoginService,
} from "@/client"
import { clearAccessToken, isLoggedIn, setAccessToken } from "@/lib/auth"
import { currentUserQueryKey, currentUserQueryOptions } from "@/lib/queries"
import { handleError, hasApiErrorStatus, ProductMessageError } from "@/utils"
import useCustomToast from "./useCustomToast"
import { requestEditorExit } from "./useEditorExit"

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
      queryClient.clear()
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
    try {
      const response = await LoginService.loginAccessToken({
        formData: data,
      })
      queryClient.clear()
      setAccessToken(response.access_token)
    } catch (error) {
      if (hasApiErrorStatus(error, 400)) {
        throw new ProductMessageError("Incorrect email or password")
      }
      throw error
    }
  }

  const loginMutation = useMutation({
    mutationFn: login,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: currentUserQueryKey })
      navigate({ to: "/" })
    },
    onError: handleError.bind(showErrorToast),
  })

  const logout = () =>
    requestEditorExit(() => {
      clearAccessToken()
      queryClient.clear()
      void navigate({ to: "/login", ignoreBlocker: true })
    })

  return {
    signUpMutation,
    loginMutation,
    logout,
    user,
  }
}

export default useAuth
