"""Evidence-backed passports with bounded model repair and explicit review fallback."""

import hashlib
import unicodedata
from collections import Counter

from pydantic import ValidationError

from app.core.config import settings
from app.services import llm
from app.services.contracts import OpenExtraction, OpenValue
from app.services.normalization import normalized

NAME_FEATURE = {
    "key": "name",
    "label": "Наименование",
    "kind": "string",
    "unit": "none",
    "comparison": "informational",
    "tolerance": 0,
}
REVIEW_MATERIAL = "Требует проверки"
REVIEW_CATEGORY_ID = "normalization_review"
PROMPT = (
    "Нормализуй одну позицию независимо от закупочной потребности и без классификации. "
    "name — наименование изделия из источника; убери только названия столбцов и лишние пробелы. "
    "name_evidence — дословный непрерывный фрагмент источника, содержащий name. "
    "Не меняй регистр, марку, знаки или кириллицу на латиницу. Не добавляй слова. "
    "Извлеки все явно указанные технические свойства в values. Для каждого свойства укажи "
    "устойчивый английский snake_case key, русское label, kind, исходные value и unit, "
    "точную цитату evidence и target_unit. Не придумывай отсутствующие свойства. "
    "Нормализацию чисел и единиц выполнит программа: напряжение V, длина/диаметр mm, "
    "площадь сечения mm2, давление MPa, масса kg, количество pcs. "
    "Неизвестные единицы и сложные диапазоны сохраняй как string целиком с единицей, "
    "target_unit=none. Не считай наружный диаметр DN. Не расшифровывай марки. "
    "Для кабеля NхS: core_count=N pcs, core_cross_section_mm2=S mm2. "
    "Категории и подкатегории здесь не создавай."
)


def clean(value):
    return " ".join(unicodedata.normalize("NFKC", value).split())


def needs_normalization_review(passport):
    return bool((passport or {}).get("normalization", {}).get("requires_review"))


def needs_review(passport):
    return needs_normalization_review(passport) or bool(
        (passport or {}).get("classification", {}).get("requires_review")
    ) or bool((passport or {}).get("taxonomy_mapping", {}).get("requires_review"))


def blocks_classification(passport):
    normalization = (passport or {}).get('normalization', {})
    return bool(normalization.get('blocks_classification', normalization.get('requires_review', False)))


def _grounded_name(name, quote, source):
    return (
        isinstance(name, str)
        and isinstance(quote, str)
        and bool(clean(name))
        and bool(quote.strip())
        and quote in source
        and clean(name) in clean(quote)
    )


def _validate(raw, text):
    parsed = OpenExtraction.model_validate(raw)
    if not _grounded_name(parsed.name, parsed.name_evidence, text):
        raise ValueError("Normalized name must be grounded in source evidence")
    if any(x.key == "name" for x in parsed.values):
        raise ValueError("name is reserved for the normalized item name")
    if any(x.kind == "string" and x.target_unit != "none" for x in parsed.values):
        raise ValueError("Text property must use target_unit=none")
    if len({x.key for x in parsed.values}) != len(parsed.values):
        raise ValueError("Duplicate extracted key")
    return _assemble(text, parsed.name, parsed.name_evidence, parsed.values, raw, "llm")


def _assemble(text, name, quote, values, raw, method):
    features = [
        {
            "key": x.key,
            "label": x.label,
            "kind": x.kind,
            "unit": x.target_unit,
            "comparison": "informational",
            "tolerance": 0,
        }
        for x in values
    ]
    extracted = [
        {"key": x.key, "value": x.value, "unit": x.unit, "evidence": x.evidence}
        for x in values
    ]
    p, issues = normalized(
        {"category_id": None, "category_evidence": None, "values": extracted},
        text,
        {"features": features, "categories": []},
    )
    issues = [x for x in issues if x["code"] != "CATEGORY_NEEDS_REVIEW"]
    name = clean(name)
    attrs = {"name": name, **p["attributes"]}
    evidence = {
        "name": {
            "quote": quote,
            "raw_value": name,
            "source_unit": "none",
            "target_unit": "none",
            "method": method,
        },
        **p["evidence"],
    }
    config = llm.current_config()
    return {
        "name": name,
        "name_evidence": quote,
        "attributes": attrs,
        "evidence": evidence,
        "category_id": None,
        "category_evidence": None,
        "normalized_attributes": attrs.copy(),
        "normalized_evidence": evidence.copy(),
        "normalized_features": features,
        "provenance": {
            "provider": config.provider,
            "model": config.model,
            "profile_id": config.profile_id,
            "llm_config": config.model_dump(),
            "llm_fingerprint": config.fingerprint(),
            "prompt_version": "open-passport-v3",
            "source_hash": hashlib.sha256(text.encode()).hexdigest(),
            "raw_response": raw,
        },
    }, issues


def source_fallback(text, raw=None):
    """Use source text and salvage individually validated properties from one response."""
    raw = raw if isinstance(raw, dict) else {}
    proposed = raw.get("values")
    proposed = proposed if isinstance(proposed, list) else []
    values = []
    for value in proposed[:64]:
        try:
            value = OpenValue.model_validate(value)
        except ValidationError:
            continue
        if value.key != "name" and not (
            value.kind == "string" and value.target_unit != "none"
        ):
            values.append(value)
    counts = Counter(x.key for x in values)
    values = [x for x in values if counts[x.key] == 1]
    name, quote = raw.get("name"), raw.get("name_evidence")
    grounded = _grounded_name(name, quote, text) and len(clean(name)) <= 500
    if not grounded:
        name, quote = clean(text)[:500], text
    passport, issues = _assemble(
        text, name, quote, values, raw, "llm" if grounded else "source-copy"
    )
    has_values = any(
        v is not None for k, v in passport["attributes"].items() if k != "name"
    )
    passport["normalization"] = {
        "status": "partial" if has_values else "source",
        "requires_review": True,
        "attempts": 0,
        "errors": [],
    }
    return passport, issues


def _diagnostic(exc, raw, attempt):
    # ValidationError.__str__ contains entire input values; keep only field locations.
    if isinstance(exc, ValidationError):
        message = "Invalid model fields: " + ", ".join(
            ".".join(map(str, e["loc"])) + " (" + e["type"] + ")"
            for e in exc.errors(include_input=False)[:10]
        )
    else:
        message = str(exc)[:800]
    raw = raw if isinstance(raw, dict) else {}
    return {
        "attempt": attempt,
        "code": "NORMALIZATION_INVALID_RESPONSE",
        "message": message,
        "disputed_name": str(raw.get("name") or "")[:500],
        "name_evidence": str(raw.get("name_evidence") or "")[:1000],
    }


def extract(text):
    errors, candidates = [], []
    payload = {"source": text}
    for attempt in range(1, settings.NORMALIZATION_MAX_ATTEMPTS + 1):
        raw = None
        try:
            raw = llm.structured(PROMPT, payload, OpenExtraction.model_json_schema())
            passport, issues = _validate(raw, text)
            unresolved = [
                x
                for x in issues
                if x["code"] in ("UNSUPPORTED_EVIDENCE", "INVALID_VALUE_OR_UNIT")
            ]
            if unresolved:
                raise ValueError(
                    "Properties lack source evidence or valid units: "
                    + ", ".join(x["key"] for x in unresolved)
                )
            passport["normalization"] = {
                "status": "normalized",
                "requires_review": False,
                "attempts": attempt,
                "errors": errors,
            }
            return passport, issues
        except (ValueError, llm.LLMResponseError) as exc:
            if isinstance(exc, llm.LLMResponseError):
                raw = exc.raw_response
            errors.append(_diagnostic(exc, raw, attempt))
            candidate = source_fallback(text, raw)
            candidates.append(candidate)
            payload = {
                "source": text,
                "previous_response": raw,
                "validation_error": errors[-1],
                "repair_instruction": "Исправь ответ по исходнику. name и цитата должны быть "
                "дословно подтверждены; неподтверждённые свойства удали. "
                "Верни полный JSON без пояснений.",
            }
    # Never merge inconsistent attempts; retain the single most useful grounded result.
    passport, issues = max(
        candidates, key=lambda pair: len(pair[0]["normalized_evidence"])
    )
    passport["normalization"].update(attempts=len(errors), errors=errors)
    issues.append(
        {
            "code": "NORMALIZATION_NEEDS_REVIEW",
            "message": "Не удалось полностью нормализовать позицию. Сохранены исходное название "
            "и подтверждённые свойства; требуется проверка.",
            "attempts": len(errors),
            "disputed_name": errors[-1]["disputed_name"],
        }
    )
    return passport, issues
