import {useEffect,useState} from 'react';
import {useWorkspace} from '../state/Workspace';
import {request} from '../services/api';
import {labApi,type Source,type ExportStage} from '../services/laboratory';
import {useResource} from '../hooks/useResource';
import {ErrorBox,percentage,Pagination} from './ui';
import {ScrollArea} from './ScrollArea';

interface Row {id:string;session_id:string;session_title:string;job_id:string;created_at:string;source:Source;dataset_id:string|null;reference:string;kind:'revision'|'experiment';method:string;label?:string;model?:string;taxonomy_enabled:boolean|null;value:number|null;metrics:Record<string,number|number[]|null>;cohort:string;scope:string;population:string;status:string;seconds?:number|null;note?:string}

export function HypothesisComparison({source,refreshKey=0}:{source:Source;refreshKey?:number}){
 const w=useWorkspace();const [hypothesis,setHypothesis]=useState('H1');const [offset,setOffset]=useState(0);const [group,setGroup]=useState('');const [revision,setRevision]=useState(0);
 const data=useResource(()=>request<{items:Row[];total:number}>(`/developer/comparisons?${new URLSearchParams({hypothesis,source,offset:String(offset),limit:'100'})}`),[hypothesis,source,offset,refreshKey,revision],w.user?.role==='admin');
 useEffect(()=>{setOffset(0);setGroup('');},[hypothesis,source]);
 const rows=(data.data?.items||[]).filter(r=>!group||r.cohort===group);
 const groups=[...new Set((data.data?.items||[]).map(r=>r.cohort))];
 const stage=hypothesis.toLowerCase() as ExportStage;
 return <section className="panel stack"><div className="row between wrap"><h2>Сравнение расчётов по гипотезе</h2><button className="btn" onClick={()=>setRevision(v=>v+1)}>Обновить сравнение</button></div>
 <div className="tabs" role="tablist" aria-label="Гипотеза для сравнения">{['H1','H2','H3'].map(h=><button key={h} role="tab" aria-selected={h===hypothesis} className={h===hypothesis?'active':''} onClick={()=>setHypothesis(h)}>{h}</button>)}</div>
 <p>История всех сессий, моделей и методов. Одинаковая группа означает одинаковые исходные строки, разметку и протокол разделения. Сравнивайте значения внутри одной группы. Ручная оценка и автоматическая разметка хранятся раздельно.</p>
 <label>Группа сопоставимых расчётов<select value={group} onChange={e=>setGroup(e.target.value)}><option value="">Все группы</option>{groups.map(g=><option key={g} value={g}>{g.slice(0,12)}</option>)}</select></label>
 {data.error&&<ErrorBox message={data.error}/>}
 <ScrollArea label={`История ${hypothesis} ${source}`}><table><thead><tr><th>Сессия / дата</th><th>Метод / модель</th><th>Таксономия</th><th>{hypothesis==='H1'?'F1':hypothesis==='H2'?'Accuracy конечного уровня':'Recall@150'}</th><th>Выборка и группа</th><th>Отчёт</th></tr></thead><tbody>{rows.map(r=><tr key={`${r.kind}-${r.id}-${r.method}`}><td>{r.session_title}<small className="block">{new Date(r.created_at).toLocaleString('ru-RU')} · {r.job_id.slice(0,8)}</small></td><td>{r.label||r.method}<small className="block">{r.model||'Модель не записана'}</small><small className="block">{r.status}</small></td><td>{r.taxonomy_enabled==null?'Не записано':r.taxonomy_enabled?'С таксономией':'Без таксономии'}</td><td>{percentage(r.value)}{typeof r.seconds==='number'&&<small className="block">{r.seconds.toFixed(2)} с</small>}{typeof r.metrics.p95_ms==='number'&&<small className="block">p95 {r.metrics.p95_ms.toFixed(2)} мс</small>}</td><td>{r.scope}<small className="block">{r.reference}</small><small className="block">{r.cohort.slice(0,12)}</small><details><summary>Условия и метрики</summary><p>{r.note}</p><p>{r.population}</p><pre className="passport-json">{JSON.stringify(r.metrics,null,2)}</pre></details></td><td><button className="btn" disabled={w.busy} onClick={()=>void w.act(()=>r.kind==='revision'?labApi.exportRevision(r.session_id,r.id,'xlsx',stage):labApi.exportExperiment(r.session_id,r.id,'xlsx',r.method,stage),'Отчёт скачан')}>Excel {hypothesis}</button><button className="btn" disabled={w.busy} onClick={()=>void w.act(()=>r.kind==='revision'?labApi.exportRevision(r.session_id,r.id,'json',stage):labApi.exportExperiment(r.session_id,r.id,'json',r.method,stage))}>JSON</button></td></tr>)}</tbody></table></ScrollArea>
 {!rows.length&&<p>Сохранённых расчётов {hypothesis} пока нет. Выполните пересчёт или лабораторный эксперимент.</p>}
 <Pagination offset={offset} total={data.data?.total||0} pageSize={100} onChange={setOffset}/>
 </section>;
}
