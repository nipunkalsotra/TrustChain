"use client";
import { createContext, useContext, useEffect, useState } from "react";
type Theme = "light" | "dark";
const Context = createContext({ theme: "light" as Theme, toggle: () => {} });
export function ThemeProvider({ children }: { children: React.ReactNode }) {
  const [theme, setTheme] = useState<Theme>("light");
  // localStorage is external state and must be read after hydration.
  useEffect(() => {
    try {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      if (localStorage.getItem("tc_theme") === "dark") setTheme("dark");
    } catch {}
  }, []);
  function toggle() {
    const next = theme === "light" ? "dark" : "light";
    setTheme(next);
    try {
      localStorage.setItem("tc_theme", next);
    } catch {}
  }
  return (
    <Context.Provider value={{ theme, toggle }}>
      <div className="tc-app" data-app-theme={theme}>
        {children}
      </div>
    </Context.Provider>
  );
}
export const useTheme = () => useContext(Context);
