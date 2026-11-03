import axios, { AxiosError } from 'axios'

const api = axios.create({ baseURL: '/api' })

// attach access token
api.interceptors.request.use((config) => {
  const token = localStorage.getItem('token')
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

let onUnauthorized: (() => void) | null = null
let refreshing: Promise<AuthData | null> | null = null

interface AuthData {
  access_token: string
  user: { id: string; email: string }
}

export function refreshSession(): Promise<AuthData | null> {
  refreshing =
    refreshing ??
    axios
      .post<AuthData>('/api/auth/refresh')
      .then((res) => {
        localStorage.setItem('token', res.data.access_token)
        return res.data
      })
      .catch(() => {
        localStorage.removeItem('token')
        return null
      })
      .finally(() => (refreshing = null))
  return refreshing
}

api.interceptors.response.use(
  (response) => response,
  async (error: AxiosError) => {
    const original = error.config as (typeof error.config & { _retried?: boolean }) | undefined
    if (error.response?.status === 401 && original && !original._retried) {
      original._retried = true
      const data = await refreshSession()
      if (data) {
        const token = localStorage.getItem('token')
        if (token) original.headers!.Authorization = `Bearer ${token}`
        return api.request(original)
      }
      onUnauthorized?.()
    }
    return Promise.reject(error)
  },
)

export function setUnauthorizedHandler(fn: () => void) {
  onUnauthorized = fn
}

export default api
