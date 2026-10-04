"""Selectable report sections shared by PDF preview and downloadable reports."""

from dataclasses import dataclass
from io import BytesIO
from xml.sax.saxutils import escape

from openpyxl import Workbook
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer
from sqlalchemy import select

from app.db import Audit, Item, User
from app.services.metrics import add_sheets, measurement


@dataclass(frozen=True)
class ReportSections:
    summary: bool = True
    candidates: bool = True
    evidence: bool = True
    history: bool = True

    def any(self):
        return self.summary or self.candidates or self.evidence or self.history


def build_report(db, session, matches, sections, file_format):
    items = {
        item.id: item
        for item in db.scalars(select(Item).where(Item.session_id == session.id))
    }
    history = (
        db.execute(
            select(Audit, User.username)
            .outerjoin(User, Audit.user_id == User.id)
            .where(Audit.session_id == session.id)
            .order_by(Audit.id)
        ).all()
        if sections.history
        else []
    )
    metrics = measurement(db, session.id) if sections.summary else None
    if file_format == "xlsx":
        workbook = Workbook()
        workbook.remove(workbook.active)
        if sections.summary:
            add_sheets(workbook, metrics)
        if sections.candidates:
            sheet = workbook.create_sheet("Кандидаты и решения")
            sheet.append(
                [
                    "Потребность",
                    "Остаток",
                    "Проверка",
                    "Ранг",
                    "Shortlist",
                    "Решение",
                    "Комментарий",
                ]
            )
            for match in matches:
                sheet.append(
                    [
                        items[match.requirement_id].source_text,
                        items[match.item_id].source_text,
                        match.result["state"],
                        match.result.get("rank"),
                        bool(match.result.get("is_shortlisted")),
                        match.decision,
                        match.expert_comment,
                    ]
                )
        if sections.evidence:
            sheet = workbook.create_sheet("Обоснования и источники")
            sheet.append(
                [
                    "ID позиции",
                    "Тип",
                    "Лист",
                    "Строка",
                    "Исходный текст",
                    "Ключ параметра",
                    "Значение",
                    "Цитата",
                ]
            )
            ids = {
                ident
                for match in matches
                for ident in (match.requirement_id, match.item_id)
            }
            for ident in sorted(ids):
                item = items[ident]
                passport = item.passport or {}
                attributes = passport.get("attributes", {})
                for key, value in attributes.items():
                    sheet.append(
                        [
                            item.id,
                            item.kind,
                            item.sheet,
                            item.row_index,
                            item.source_text,
                            key,
                            value,
                            passport.get("evidence", {}).get(key, {}).get("quote", ""),
                        ]
                    )
        if sections.history:
            sheet = workbook.create_sheet("История действий")
            sheet.append(["Время UTC", "Автор", "Событие", "SHA-256"])
            for event, author in history:
                sheet.append(
                    [
                        event.created_at,
                        author or "Система",
                        event.event_type,
                        event.sha256_hash,
                    ]
                )
        for sheet in workbook:
            sheet.freeze_panes = "A2"
            sheet.auto_filter.ref = sheet.dimensions
            for row in sheet:
                for cell in row:
                    if isinstance(cell.value, str):
                        cell.data_type = "s"
            for column in sheet.columns:
                sheet.column_dimensions[column[0].column_letter].width = 30
        output = BytesIO()
        workbook.save(output)
        return output.getvalue()

    pdfmetrics.registerFont(
        TTFont("DejaVu", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
    )
    styles = getSampleStyleSheet()
    for style in styles.byName.values():
        style.fontName = "DejaVu"
    story = [
        Paragraph("Протокол предзакупочной проверки запасов", styles["Title"]),
        Paragraph(escape(session.title), styles["Heading2"]),
        Paragraph(
            "Проект. Требуется экспертное согласование. Денежный эффект и физическое резервирование не подтверждены.",
            styles["Normal"],
        ),
    ]

    def paragraph(value, style="Normal"):
        story.append(Paragraph(escape(str(value)), styles[style]))
        story.append(Spacer(1, 6))

    if sections.summary:
        paragraph("Сводные результаты", "Heading2")
        paragraph(
            f"Проверено пар: {metrics['matching']['pairs_evaluated']}. В shortlist: {metrics['matching']['shortlisted_pairs']}."
        )
        for target in metrics["targets"]:
            value = (
                "Не измерено" if target["value"] is None else f"{target['value']:.2%}"
            )
            paragraph(f"{target['label']}: {value}")
    if sections.candidates:
        paragraph("Кандидаты и решения", "Heading2")
        for match in matches:
            paragraph(
                f"{items[match.requirement_id].source_text} → {items[match.item_id].source_text}",
                "Heading3",
            )
            paragraph(f"{match.result['state']}. {match.result['reason']}")
            paragraph(f"Решение: {match.decision}. {match.expert_comment or ''}")
    if sections.evidence:
        paragraph("Обоснования и источники", "Heading2")
        ids = {
            ident
            for match in matches
            for ident in (match.requirement_id, match.item_id)
        }
        for ident in sorted(ids):
            item = items[ident]
            paragraph(
                f"{item.kind}; лист {item.sheet or '—'}; строка {item.row_index}: {item.source_text}"
            )
            for key, evidence in (item.passport or {}).get("evidence", {}).items():
                paragraph(f"{key}: {evidence.get('quote', '')}")
    if sections.history:
        paragraph("История действий", "Heading2")
        for event, author in history:
            paragraph(
                f"{event.created_at} · {author or 'Система'} · {event.event_type}"
            )
    output = BytesIO()
    SimpleDocTemplate(output, title=session.title, author="StockExpert").build(story)
    return output.getvalue()
