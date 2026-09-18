"use client";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
} from "react";
import { getMe, ApiRequestError, switchProject as changeProject } from "./api";
import { setSession, clearSession } from "./auth";
import type { MeResponse } from "./product-types";
const Context = createContext<{
  me: MeResponse | null;
  loading: boolean;
  error: string;
  refresh: () => Promise<void>;
  switchProject: (id: number) => Promise<void>;
}>({
  me: null,
  loading: true,
  error: "",
  refresh: async () => {},
  switchProject: async () => {},
});
export function SessionProvider({ children }: { children: React.ReactNode }) {
  const [me, setMe] = useState<MeResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const refresh = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const data = await getMe();
      setMe(data);
      setSession(data.user);
    } catch (e) {
      setMe(null);
      if (e instanceof ApiRequestError && e.status === 401) clearSession();
      else setError(e instanceof Error ? e.message : "Session check failed.");
    } finally {
      setLoading(false);
    }
  }, []);
  // The server session is external state; synchronize it after hydration.
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void refresh();
    const expire = () => {
      clearSession();
      setMe(null);
      setLoading(false);
    };
    window.addEventListener("tc:session-expired", expire);
    return () => window.removeEventListener("tc:session-expired", expire);
  }, [refresh]);
  const switchProject = async (id: number) => {
    await changeProject(id);
    await refresh();
  };
  return (
    <Context.Provider value={{ me, loading, error, refresh, switchProject }}>
      {children}
    </Context.Provider>
  );
}
export const useSession = () => useContext(Context);
