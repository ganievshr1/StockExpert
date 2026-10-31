import {useState} from 'react';
import {useWorkspace} from '../state/Workspace';
import {labApi,type Dashboard,type ExportStage,type ExportItems} from '../services/laboratory';

export function RunExportPanel({data:d}:{data:Dashboard}){
 const w=useWorkspace();const [stage,setStage]=useState<ExportStage>('h1');
 const [items,setItems]=useState<ExportItems>('residual');
 const run=d.runs.find(r=>r.id===d.job_id);
 return <section className="panel stack" aria-label="Выгрузка результатов запуска">
  <h3>Скачать результаты выбранного расчёта</h3>
  <p>Исходные строки и ответы модели из сохранённого запуска. Эти файлы можно сравнить с экспертной разметкой самостоятельно.</p>
  <div className="row wrap">
   <label>Этап для выгрузки<select value={stage} onChange={e=>setStage(e.target.value as ExportStage)}>
    <option value="h1">H1 — распознанный текст и параметры</option>
    <option value="h2" disabled={!d.taxonomy_enabled}>H2 — дерево и назначенные категории</option>
    <option value="h3" disabled={!run||!['match','full'].includes(run.kind)}>H3 — результаты подбора</option>
    <option value="all">Все сохранённые этапы</option>
   </select></label>
   {stage!=='h3'&&<label>Позиции H1 / H2<select value={items} onChange={e=>setItems(e.target.value as ExportItems)}>
    <option value="residual">Только складские МТР</option><option value="requirement">Только опросный лист</option><option value="all">МТР и опросный лист</option>
   </select></label>}
   {(['json','xlsx'] as const).map(format=><button key={format} className="btn" disabled={w.busy||!d.job_id}
    onClick={()=>void w.act(()=>labApi.exportRun(w.sid,d.job_id!,stage,format,items),'Файл результатов скачан')}>
    Скачать {format==='json'?'JSON':'Excel'}
   </button>)}
  </div>
  <small>В H1 нет категорий H2. Для проверки текста сравнивайте name по файлу (source_sha256), листу, строке и типу позиции. Повторы и позиции без ответа сохранены.</small>
  {run&&<small>Запуск: {run.id} · {new Date(run.created_at).toLocaleString('ru-RU')} · {d.taxonomy_enabled?'с H2':'без H2'}</small>}
 </section>;
}
