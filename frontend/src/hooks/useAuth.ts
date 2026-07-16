import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import toast from 'react-hot-toast'
import { authApi } from '@/api/auth'
import { useAuthStore } from '@/store/auth'

export function useAuth() {
  const { user, isAuthenticated, setSession, clearSession } = useAuthStore()
  const navigate = useNavigate()
  const queryClient = useQueryClient()

  const loginMutation = useMutation({
    mutationFn: ({ username, password }: { username: string; password: string }) =>
      authApi.login(username, password),
    onSuccess: (data) => {
      setSession(data.user, data.access_token)
      toast.success(`Welcome back, ${data.user.name.split(' ')[0]}`)
      navigate('/app/dashboard')
    },
    onError: (err: any) => {
      toast.error(err?.response?.data?.detail || 'Invalid email or password')
    },
  })

  const logout = () => {
    clearSession()
    queryClient.clear()
    navigate('/login')
  }

  return {
    user,
    isAuthenticated,
    login: loginMutation.mutate,
    isLoggingIn: loginMutation.isPending,
    logout,
  }
}

/** Refetches the current user on mount to validate the persisted token is still good. */
export function useCurrentUser() {
  const { isAuthenticated, updateUser, clearSession } = useAuthStore()
  return useQuery({
    queryKey: ['auth', 'me'],
    queryFn: async () => {
      try {
        const me = await authApi.me()
        updateUser(me)
        return me
      } catch (err) {
        clearSession()
        throw err
      }
    },
    enabled: isAuthenticated,
    retry: false,
    staleTime: 5 * 60_000,
  })
}
