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
let refreshing: Promise<boolean> | null = null

async function tryRefresh(): Promise<boolean> {
  try {
    const res = await axios.post('/api/auth/refresh')
    localStorage.setItem('token', res.data.access_token)
    return true
  } catch {
    localStorage.removeItem('token')
    return false
  }
}

api.interceptors.response.use(
  (response) => response,
  async (error: AxiosError) => {
    const original = error.config as (typeof error.config & { _retried?: boolean }) | undefined
    if (error.response?.status === 401 && original && !original._retried) {
      original._retried = true
      refreshing = refreshing ?? tryRefresh().finally(() => (refreshing = null))
      const ok = await refreshing
      if (ok) {
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
