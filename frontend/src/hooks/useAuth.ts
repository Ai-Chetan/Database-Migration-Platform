import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import toast from 'react-hot-toast'
import { authApi, RegisterPayload } from '@/api/auth'
import { useAuthStore } from '@/store/auth'

export function useAuth() {
  const { user, isAuthenticated, setSession, clearSession } = useAuthStore()
  const navigate = useNavigate()
  const queryClient = useQueryClient()

  const loginMutation = useMutation({
    mutationFn: ({ email, password }: { email: string; password: string }) =>
      authApi.login(email, password),
    onSuccess: (data) => {
      setSession(data.user, data.access_token)
      toast.success(`Welcome back, ${data.user.name.split(' ')[0]}`)
      navigate('/app/dashboard')
    },
    onError: (err: any) => {
      toast.error(err?.response?.data?.detail || 'Invalid email or password')
    },
  })

  const registerMutation = useMutation({
    mutationFn: (payload: RegisterPayload) => authApi.register(payload),
    onSuccess: (data) => {
      setSession(data.user, data.access_token)
      toast.success(`Welcome, ${data.user.name.split(' ')[0]}! Your workspace is ready.`)
      navigate('/app/dashboard')
    },
    onError: (err: any) => {
      toast.error(err?.response?.data?.detail || 'Could not create your account')
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
    login: (email: string, password: string) => loginMutation.mutate({ email, password }),
    isLoggingIn: loginMutation.isPending,
    register: registerMutation.mutate,
    isRegistering: registerMutation.isPending,
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
