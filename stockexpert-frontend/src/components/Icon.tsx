import icons from "./figma-icons.json";
import { useTheme } from "../state/Theme";

export function Icon({ name, size = 20 }: { name: string; size?: number }) {
  const { theme } = useTheme();
  const filename =
    (icons[theme] as Record<string, string>)[name] || icons[theme].file;
  return (
    <span
      className="figma-icon"
      style={{ width: size, height: size }}
      aria-hidden="true"
    >
      <img src={`/figma/${filename}`} alt="" />
    </span>
  );
}

export function Brand() {
  return (
    <div className="brand">
      <span className="brand-mark">
        <Icon name="brand" size={22} />
      </span>
      <span>StockExpert</span>
    </div>
  );
}

export function ThemeSwitch() {
  const { theme, toggle } = useTheme();
  return (
    <button
      type="button"
      className="theme-switch"
      onClick={toggle}
      aria-label={
        theme === "light" ? "Включить тёмную тему" : "Включить светлую тему"
      }
    >
      <Icon name="sun" size={20} />
      <span>{theme === "light" ? "Светлая тема" : "Тёмная тема"}</span>
      <Icon name="moon" size={20} />
    </button>
  );
}
