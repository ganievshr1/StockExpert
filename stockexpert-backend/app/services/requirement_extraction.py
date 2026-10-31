"""Split requirements without losing the original document on invalid LLM output."""

from app.core.config import settings
from app.services import llm
from app.services.contracts import Requirements


def split(text):
    payload = {"text": text}
    for attempt in range(1, settings.NORMALIZATION_MAX_ATTEMPTS + 1):
        try:
            response = llm.structured(
                "Выдели отдельные позиции потребности. Каждый элемент — дословный непрерывный "
                "фрагмент исходника со всеми требованиями к позиции. Не сокращай и не добавляй текст.",
                payload,
                Requirements.model_json_schema(),
            )
            parts = Requirements.model_validate(response).items
            if not parts or any(not part.strip() or part not in text for part in parts):
                raise ValueError("Requirement item not grounded in source")
            return parts, []
        except (ValueError, llm.LLMResponseError):
            payload = {
                "text": text,
                "repair_instruction": "Предыдущий ответ не подтверждён источником или нарушает JSON Schema. "
                "Верни непустой список items, только дословные фрагменты исходного текста.",
            }
    return [text], [
        {
            "code": "REQUIREMENT_SPLIT_NEEDS_REVIEW",
            "attempts": attempt,
            "message": "Модель не разделила опросный лист достоверно. "
            "Весь исходный текст сохранён одной позицией; требуется проверка.",
        }
    ]
