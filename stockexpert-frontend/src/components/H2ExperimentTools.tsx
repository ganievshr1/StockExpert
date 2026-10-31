import {useEffect,useState} from 'react';
import {useWorkspace} from '../state/Workspace';
import {developerApi} from '../services/developer';
import {labApi,type H2Check,type Source} from '../services/laboratory';
import {percentage} from './ui';

export const H2_METHODS=['h2_kmeans','h2_raw','h2_passports','h2_llm','h2_hybrid'];

export function H2ExperimentTools({source,payload,disabled,onReference,onRun}:{
 source:Source;payload:Record<string,unknown>;disabled:boolean;
 onReference:(id:string)=>void;onRun:()=>void;
}){
 const w=useWorkspace();const [check,setCheck]=useState<H2Check|null>(null);
 const fingerprint=JSON.stringify(payload);
 useEffect(()=>setCheck(null),[fingerprint]);
 return <div className="panel stack" aria-label="Подготовка эксперимента H2">
  <p>Основная метрика: <strong>Accuracy конечной категории ≥85%</strong> и положительная разница с TF-IDF + k-means на том же закрытом тесте. Неприсвоенная категория считается ошибкой.</p>
  {source==='automatic'&&w.user?.role==='admin'&&<label>Загрузить экспертную разметку H2 (Excel или JSON)
   <input type="file" accept=".xlsx,.json" disabled={w.busy} onChange={e=>{const file=e.target.files?.[0];if(!file)return;void w.act(async()=>{const r=await developerApi.upload(file);onReference(r.id);},'Эталон H2 загружен и выбран');e.target.value='';}}/>
  </label>}
  <div className="row wrap">
   <button className="btn" disabled={disabled} onClick={()=>void w.act(async()=>setCheck(await labApi.checkH2(w.sid,{...payload,methods:H2_METHODS})),'Состав теста H2 проверен')}>Проверить разметку и разделение H2</button>
   <button className="btn-primary" disabled={disabled} onClick={onRun}>Запустить сравнение H2: 5 методов</button>
  </div>
  {check&&<div role="status"><p>Всего {check.rows}, обучение {check.train_count}, тест {check.test_count}. Категорий {check.classes}, одиночных {check.singletons}. Классы отсутствуют в обучении у {check.unseen_test_count} тестовых позиций. Верхняя граница для соответствия, обученного по меткам: {percentage(check.train_mapping_accuracy_ceiling)}.</p>{check.warnings.map(t=><p key={t}>{t}</p>)}</div>}
  <details><summary>Как проверить H2</summary><ol>
   <li>Загрузите складскую базу и завершите извлечение H1. Для ЕБДЗ_100: заголовок 0, название только B, столбец категории не выбран. Группу A не передавайте модели.</li>
   <li>Загрузите эталон H2 и выберите его в поле «Эталон сравнения». Каждый складской ID должен иметь конечную категорию. Категории и их состав эксперт фиксирует до теста.</li>
   <li>В «Настроить и запустить сравнение методов» задайте seed, долю обучения, k, минимум HDBSCAN, веса и модель LLM. Не подбирайте их по результатам закрытого теста. Для пилота допустимо оставить подтверждение эксперта выключенным.</li>
   <li>Проверьте состав разметки и запустите пять методов. При выключенной таксономии основного расчёта лабораторный H2 всё равно доступен.</li>
   <li>Выберите завершённый эксперимент и скачайте «Отчёт H2: Excel». Сверьте сводку, парные сравнения, все тестовые строки и разделение train/test. Результаты других расчётов сохраняются в истории.</li>
  </ol><p>Верность связей предложенного дерева требует отдельной экспертной оценки. До неё показатель пустой. Шум HDBSCAN и итоговые отказы показаны раздельно. Венгерская accuracy — дополнительная диагностика, не критерий H2.</p></details>
 </div>;
}
