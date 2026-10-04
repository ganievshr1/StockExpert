import type { SchemaDraft } from "../types";
export function materialFeatures(
  schema: Partial<SchemaDraft> | undefined,
  categoryId?: string | null,
) {
  const features = schema?.features || [];
  if (schema?.taxonomy_mode !== "passports") return features;
  const category = schema.categories?.find((c) => c.id === categoryId);
  const keys = new Set(category?.feature_keys || ["name"]);
  return features.filter((f) => keys.has(f.key));
}
