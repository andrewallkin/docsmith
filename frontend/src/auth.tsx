import { createContext, useContext, useEffect, useState } from 'react'
import api, { setUnauthorizedHandler } from './client'

export interface User {
  id: string
  email: string
}

interface AuthContextValue {
  user: User | null
  loading: boolean
  login: (email: string, password: string) => Promise<void>
  register: (email: string, password: string) => Promise<void>
  logout: () => Promise<void>
}

const AuthContext = createContext<AuthContextValue>({
  user: null,
  loading: true,
  login: async () => {},
  register: async () => {},
  logout: async () => {},
})

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<User | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    api
      .post('/auth/refresh')
      .then((res) => {
        localStorage.setItem('token', res.data.access_token)
        setUser(res.data.user)
      })
      .catch(() => setUser(null))
      .finally(() => setLoading(false))
    setUnauthorizedHandler(() => setUser(null))
  }, [])

  const login = async (email: string, password: string) => {
    const res = await api.post('/auth/login', { email, password })
    localStorage.setItem('token', res.data.access_token)
    setUser(res.data.user)
  }

  const register = async (email: string, password: string) => {
    const res = await api.post('/auth/register', { email, password })
    localStorage.setItem('token', res.data.access_token)
    setUser(res.data.user)
  }

  const logout = async () => {
    try {
      await api.post('/auth/logout')
    } finally {
      localStorage.removeItem('token')
      setUser(null)
    }
  }

  return (
    <AuthContext.Provider value={{ user, loading, login, register, logout }}>
      {children}
    </AuthContext.Provider>
  )
}

export function useAuth() {
  return useContext(AuthContext)
}
