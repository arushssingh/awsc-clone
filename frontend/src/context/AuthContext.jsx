import { createContext, useContext, useState, useEffect } from 'react';
import api from '../api';

const AuthContext = createContext(null);

const SESSION_KEY = 'user_profile';

function getCachedUser() {
  try {
    const saved = sessionStorage.getItem(SESSION_KEY);
    return saved ? JSON.parse(saved) : null;
  } catch {
    return null;
  }
}

function cacheUser(userData) {
  if (userData) {
    sessionStorage.setItem(SESSION_KEY, JSON.stringify(userData));
  } else {
    sessionStorage.removeItem(SESSION_KEY);
  }
}

export function AuthProvider({ children }) {
  const cached = getCachedUser();
  const [user, setUser] = useState(cached);
  const [loading, setLoading] = useState(!cached);

  useEffect(() => {
    api.get('/auth/me')
      .then((res) => {
        setUser(res.data);
        cacheUser(res.data);
      })
      .catch(() => {
        setUser(null);
        cacheUser(null);
      })
      .finally(() => setLoading(false));
  }, []);

  const login = async (username, password) => {
    await api.post('/auth/login', { username, password });
    const meRes = await api.get('/auth/me');
    setUser(meRes.data);
    cacheUser(meRes.data);
    return meRes.data;
  };

  const register = async (username, email, password) => {
    await api.post('/auth/register', { username, email, password });
    const meRes = await api.get('/auth/me');
    setUser(meRes.data);
    cacheUser(meRes.data);
    return meRes.data;
  };

  const logout = async () => {
    try {
      await api.post('/auth/logout');
    } catch {
      // Clear state even if the call fails
    }
    setUser(null);
    cacheUser(null);
  };

  return (
    <AuthContext.Provider value={{ user, loading, login, register, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be used within AuthProvider');
  return ctx;
}
