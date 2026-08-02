import {
  createContext,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from "react";

export interface ThemeDef {
  id: string;
  name: string;
  swatch: string; // representative accent for the switcher
}

export const THEMES: ThemeDef[] = [
  { id: "ocean", name: "Ocean", swatch: "#0e7c86" },
  { id: "midnight", name: "Midnight", swatch: "#38bdf8" },
  { id: "slate", name: "Slate", swatch: "#4f6d8c" },
  { id: "sunset", name: "Sunset", swatch: "#e4572e" },
  { id: "forest", name: "Forest", swatch: "#2f9e5f" },
];

const STORAGE_KEY = "pil-theme";
const ThemeCtx = createContext<{ theme: string; setTheme: (t: string) => void }>({
  theme: "ocean",
  setTheme: () => {},
});

export function useTheme() {
  return useContext(ThemeCtx);
}

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [theme, setTheme] = useState<string>(
    () => localStorage.getItem(STORAGE_KEY) || "ocean",
  );

  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
    localStorage.setItem(STORAGE_KEY, theme);
  }, [theme]);

  return <ThemeCtx.Provider value={{ theme, setTheme }}>{children}</ThemeCtx.Provider>;
}

/** Sidebar swatch picker. */
export function ThemeSwitcher() {
  const { theme, setTheme } = useTheme();
  return (
    <div className="theme-switch">
      <div className="theme-switch-label">Theme</div>
      <div className="swatches">
        {THEMES.map((t) => (
          <button
            key={t.id}
            className={`swatch${theme === t.id ? " active" : ""}`}
            style={{ background: t.swatch }}
            title={t.name}
            aria-label={`${t.name} theme`}
            onClick={() => setTheme(t.id)}
          />
        ))}
      </div>
    </div>
  );
}
