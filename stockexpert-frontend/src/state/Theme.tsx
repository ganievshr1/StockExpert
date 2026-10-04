import {
  createContext,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from "react";

type Theme = "light" | "dark";
const ThemeContext = createContext<{ theme: Theme; toggle: () => void }>({
  theme: "light",
  toggle: () => {},
});

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [theme, setTheme] = useState<Theme>(() => {
    try {
      return localStorage.getItem("stockexpert.theme") === "dark"
        ? "dark"
        : "light";
    } catch {
      return "light";
    }
  });
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    document.documentElement.style.colorScheme = theme;
    try {
      localStorage.setItem("stockexpert.theme", theme);
    } catch {
      /* Theme also works without persistent storage. */
    }
  }, [theme]);
  useEffect(() => {
    const sync = (event: StorageEvent) => {
      if (
        event.key === "stockexpert.theme" &&
        (event.newValue === "light" || event.newValue === "dark")
      )
        setTheme(event.newValue);
    };
    window.addEventListener("storage", sync);
    return () => window.removeEventListener("storage", sync);
  }, []);
  return (
    <ThemeContext.Provider
      value={{
        theme,
        toggle: () =>
          setTheme((current) => (current === "light" ? "dark" : "light")),
      }}
    >
      {children}
    </ThemeContext.Provider>
  );
}

export const useTheme = () => useContext(ThemeContext);
