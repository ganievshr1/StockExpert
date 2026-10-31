from io import BytesIO
from xml.sax.saxutils import escape
from reportlab.platypus import SimpleDocTemplate,Paragraph,Spacer
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

def pdf_report(session,rows,names=None):
    names=names or {}
    pdfmetrics.registerFont(TTFont('DejaVu','/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'))
    styles=getSampleStyleSheet()
    for name in ('Normal','Title'): styles[name].fontName='DejaVu'
    story=[Paragraph('StockExpert — проект отчёта',styles['Title']),Paragraph(escape(session.title),styles['Normal']),Paragraph('Требуется экспертное согласование. Не является подписанным протоколом комиссии.',styles['Normal'])]
    for m in rows:
        story.extend([Spacer(1,8),Paragraph(escape(f'{names.get(m.requirement_id,m.requirement_id)} → {names.get(m.item_id,m.item_id)}: {m.result["state"]}'),styles['Normal']),Paragraph(escape(m.result['reason']),styles['Normal']),Paragraph(escape(f'Решение эксперта: {m.decision}. {m.expert_comment or ""}'),styles['Normal'])])
    out=BytesIO();SimpleDocTemplate(out).build(story);return out.getvalue()

