from io import BytesIO
from openpyxl import Workbook
from openpyxl.styles import Font

def workbook(data):
    book=Workbook();ws=book.active;ws.title='Ручная проверка'
    ws.append(['Запуск','Источник','H1 F1 текста по строкам','Accuracy H2','Recall150 H3','Покрытие','Micro-F1 технических параметров отдельно'])
    q=data['manual'];ws.append([data['job_id'],'Ручная проверка',q.get('text_f1'),q.get('accuracy'),q.get('recall_at_150'),str(q.get('coverage',{})),q.get('f1')])
    ws=book.create_sheet('Автоматическое сравнение');ws.append(['Запуск','Эталон','Версия','H1','H2','H3','Дата','Режим H1'])
    refs={r['id']:r for r in data['references']}
    for row in data['automatic']:
        ref=refs.get(row['dataset_id'],{});h={h['id']:h.get('value') for h in row['result'].get('hypotheses',[])}
        ws.append([row['job_id'],ref.get('title'),ref.get('version'),h.get('H1'),h.get('H2'),h.get('H3'),str(row['created_at']),row['result'].get('h1_mode','attributes')])
    for hyp,slide in [('H1',11),('H2',16),('H3',21)]:
        ws=book.create_sheet(f'{hyp} слайд {slide}');ws.append(['Источник','Эксперимент','Запуск','Метод','Статус','F1','Accuracy','ARI','V-мера','Силуэт','Шум HDBSCAN','Верность связей','Recall150 до CE','nDCG10','p50 мс','p95 мс','Время с','Выборка','Примечание','N_correct H2','N_test H2','Без категории H2','Ошибки родителей H2','Δ Accuracy','Accuracy CI95','Δ CI95','Протокол H2'])
        for exp in data['experiments']:
            for key,result in exp['result'].get('methods',{}).items():
                if result.get('hypothesis')!=hyp:continue
                m=result.get('metrics',{});ws.append([exp['source'],exp['id'],exp['job_id'],result.get('label',key),result.get('status'),m.get('f1'),m.get('accuracy'),m.get('ari'),m.get('v_measure'),m.get('silhouette'),m.get('noise_rate'),m.get('edge_accuracy'),m.get('recall150'),m.get('ndcg10'),m.get('p50_ms'),m.get('p95_ms'),result.get('seconds'),result.get('population'),result.get('note'),m.get('n_correct'),m.get('n_test'),m.get('unassigned_rate'),m.get('parent_errors'),m.get('delta_accuracy'),str(m.get('accuracy_ci95')) if m.get('accuracy_ci95') else None,str(m.get('delta_ci95')) if m.get('delta_ci95') else None,result.get('protocol')])
    ws=book.create_sheet('История оценок');ws.append(['Версия','Дата','Запуск','Источник','Действие','Эталон'])
    for row in data['history']:ws.append([row['id'],str(row['created_at']),row['job_id'],row['source'],row['action'],row.get('dataset_id')])
    for ws in book:
        ws.freeze_panes='A2';ws.auto_filter.ref=ws.dimensions
        for cell in ws[1]:cell.font=Font(bold=True)
        for col in ws.columns:ws.column_dimensions[col[0].column_letter].width=24
        for row in ws:
            for cell in row:
                if isinstance(cell.value,str) and cell.value.startswith(('=','+','-','@')):cell.data_type='s'
    out=BytesIO();book.save(out);return out.getvalue()
