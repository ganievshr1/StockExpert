"""Recover per-item taxonomy proposals without guessing identifiers or losing rows."""

from collections import Counter
from dataclasses import dataclass

from pydantic import ValidationError

from app.core.config import settings
from app.services import llm
from app.services.contracts import MaterialLabel, MaterialLabels
from app.services.passports import REVIEW_MATERIAL, clean

PROMPT = (
    "Построй классификацию по уже нормализованным JSON-паспортам. "
    "Обработай КАЖДЫЙ item_id ровно один раз, копируй идентификатор дословно. "
    "Не заменяй идентификаторы порядковыми номерами. source_rows — число исходных дублей; "
    "для одного item_id всё равно нужен только один ответ. "
    "Первое логическое поле name — основа классификации. Выдели характерное название изделия: "
    "главное существительное, при необходимости устойчивое сочетание. "
    "material — единое родовое название такого изделия в единственном числе. "
    "Объединяй синонимы. Не объединяй разные изделия лишь по сырью или общим техническим свойствам. "
    "subtype — подтип внутри этого материала по назначению или конструкции, если он явно понятен "
    "из наименования; иначе null. Размеры, марки, напряжения и количества остаются признаками, "
    "не отдельными категориями. Не используй заранее заданный список типов материалов."
)


@dataclass(frozen=True)
class ClassificationResult:
    item_id: str
    material: str
    subtype: str | None
    requires_review: bool
    attempts: int
    errors: list[dict]
    raw_response: dict | None


def _inspect(raw, expected):
    """Only accept a validated entry whose supplied ID occurs exactly once."""
    entries = raw.get("items") if isinstance(raw, dict) else None
    if not isinstance(entries, list) or len(entries) > 100:
        entries = []
    counts = Counter(
        x["item_id"]
        for x in entries
        if isinstance(x, dict) and isinstance(x.get("item_id"), str)
    )
    valid = {}
    invalid = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        ident = entry.get("item_id")
        if not isinstance(ident, str) or ident not in expected or counts[ident] != 1:
            continue
        try:
            label = MaterialLabel.model_validate(entry)
        except ValidationError:
            invalid.append(ident)
            continue
        material = clean(label.material)
        if (
            not any(c.isalnum() for c in material)
            or material.casefold() == REVIEW_MATERIAL.casefold()
        ):
            invalid.append(ident)
            continue
        subtype = clean(label.subtype) if label.subtype else None
        if subtype and not any(c.isalnum() for c in subtype):
            invalid.append(ident)
            continue
        valid[ident] = label.model_copy(
            update={"material": material, "subtype": subtype}
        )
    diagnostic = {
        "code": "TAXONOMY_BATCH_MISMATCH",
        "message": "Ответ классификации содержит пропуски, повторы или недопустимые значения.",
        "expected_count": len(expected),
        "received_count": len(entries),
        "missing_ids": sorted(expected - counts.keys()),
        "duplicate_ids": sorted(k for k, v in counts.items() if v > 1),
        "unexpected_ids": sorted(counts.keys() - expected),
        "invalid_ids": sorted(invalid),
    }
    mismatch = len(valid) != len(expected) or bool(diagnostic["unexpected_ids"])
    return valid, diagnostic if mismatch else None


def classify(inputs, tick):
    """Yield successes immediately; retry unresolved IDs individually, at most N calls each.

    The caller commits each result before this generator advances. Transport,
    authentication, database and programming failures propagate to the job layer.
    """
    pending = list(inputs)
    histories = {x["item_id"]: [] for x in inputs}
    for attempt in range(1, settings.TAXONOMY_MAX_ATTEMPTS + 1):
        groups = [pending] if attempt == 1 else [[x] for x in pending]
        remaining = []
        for group in groups:
            if not group:
                continue
            expected = {x["item_id"] for x in group}
            schema = MaterialLabels.model_json_schema()
            schema["$defs"]["MaterialLabel"]["properties"]["item_id"]["enum"] = sorted(
                expected
            )
            schema["properties"]["items"].update(
                minItems=len(group), maxItems=len(group)
            )
            payload = {"passports": group, "required_item_ids": sorted(expected)}
            if attempt > 1:
                payload.update(
                    repair_instruction="Исправь ответ только для переданных позиций. "
                    "Скопируй item_id дословно; верни ровно одну классификацию на позицию.",
                    previous_errors=histories[group[0]["item_id"]][-1:],
                )
            tick("TAXONOMY_BUILD")
            response_error = None
            try:
                raw = llm.structured(PROMPT, payload, schema)
            except llm.LLMResponseError as exc:
                # JSON/schema errors may still contain usable, unambiguous entries.
                raw = exc.raw_response
                response_error = str(exc)[:800]
            valid, diagnostic = _inspect(raw, expected)
            if response_error and diagnostic:
                diagnostic["response_error"] = response_error
            for row in group:
                ident = row["item_id"]
                if diagnostic:
                    histories[ident].append({**diagnostic, "attempt": attempt})
                label = valid.get(ident)
                if label is not None:
                    yield ClassificationResult(
                        ident,
                        label.material,
                        label.subtype,
                        False,
                        attempt,
                        histories[ident],
                        label.model_dump(),
                    )
                elif attempt == settings.TAXONOMY_MAX_ATTEMPTS:
                    yield ClassificationResult(
                        ident,
                        REVIEW_MATERIAL,
                        None,
                        True,
                        attempt,
                        histories[ident],
                        None,
                    )
                else:
                    remaining.append(row)
            tick("TAXONOMY_BUILD")
        pending = remaining
        if not pending:
            break
