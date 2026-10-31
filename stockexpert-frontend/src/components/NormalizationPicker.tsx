export function NormalizationPicker({
  value,
  onChange,
  disabled = false,
}: {
  value: "llm" | "regex";
  onChange: (value: "llm" | "regex") => void;
  disabled?: boolean;
}) {
  return (
    <div className="stack">
      <label>
        Метод H1 — извлечение характеристик
        <select
          aria-label="Метод H1 — извлечение характеристик"
          value={value}
          disabled={disabled}
          onChange={(e) => onChange(e.target.value as "llm" | "regex")}
        >
          <option value="regex">Регулярные выражения · CPU</option>
          <option value="llm">LLM · выбранная модель</option>
        </select>
      </label>
      <small>
        {value === "regex"
          ? "H1 извлекает параметры регулярками без Qwen/DeepSeek. Если включён H2, для него используется выбранная модель. Многострочный опросный лист сохраняется одной позицией и требует проверки."
          : "H1 извлекает параметры выбранной моделью. Если H2 включён, он использует ту же модель для классификации паспортов."}
      </small>
    </div>
  );
}
