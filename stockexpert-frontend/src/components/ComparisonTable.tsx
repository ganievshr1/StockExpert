import { ScrollArea } from "./ScrollArea";
import type { CandidateItem, Feature, Item } from "../types";
import { materialFeatures } from "../services/taxonomy";
import { useWorkspace } from "../state/Workspace";
export function displayValue(
  value: string | number | null | undefined,
  unit = "none",
) {
  return value == null
    ? "Нет данных"
    : `${value}${unit === "none" ? "" : ` ${unit}`}`;
}
export function ComparisonTable({
  requirement,
  residual,
  diff = [],
}: {
  requirement: Item;
  residual: Item;
  diff?: CandidateItem["diff"];
}) {
  const w = useWorkspace();
  const all = materialFeatures(
    w.data?.session.schema_doc,
    requirement.passport?.category_id,
  );
  const fields =
    all.length > 1 ? all.filter((feature) => feature.key !== "name") : all;
  return (
    <ScrollArea
      label="Сравнение параметров потребности и запаса"
      className="comparison-table"
    >
      <table>
        <thead>
          <tr>
            <th>Параметр</th>
            <th>Нужно</th>
            <th>Запас</th>
          </tr>
        </thead>
        <tbody>
          {fields.map((feature: Feature) => (
            <tr key={feature.key}>
              <td>{feature.label}</td>
              <td>
                {displayValue(
                  requirement.passport?.attributes[feature.key],
                  feature.unit,
                )}
              </td>
              <td
                style={{
                  color: diff.some((value) => value.field === feature.key)
                    ? "var(--danger)"
                    : undefined,
                }}
              >
                {displayValue(
                  residual.passport?.attributes[feature.key],
                  feature.unit,
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </ScrollArea>
  );
}
