import {useEffect,useState} from 'react';
import {useWorkspace} from '../state/Workspace';
import {request} from '../services/api';
import type {SchemaDraft,Feature} from '../types';
import {Notice,PageTitle} from './ui';
import {ScrollArea} from './ScrollArea';
export function FlatRules(){
 const w=useWorkspace();const [draft,setDraft]=useState<SchemaDraft|null>(null);
 useEffect(()=>{setDraft(w.data?.session.schema_doc as SchemaDraft || null);},[w.data?.session.schema_doc]);
 const change=(key:string,patch:Partial<Feature>)=>setDraft(d=>d?{...d,features:d.features.map(f=>f.key===key?{...f,...patch}:f)}:d);
 return <div className="section-gap"><PageTitle eyebrow="Настройка сравнения" title="Параметры без таксономии H2" description="Категории не строятся и не ограничивают поиск. Правила сравнивают извлечённые значения и единицы."/>
 <Notice title="H2 отключён">Метрика классификации не рассчитывается. Совпадение характеристик само по себе не доказывает взаимозаменяемость изделий.</Notice>
 {draft?.taxonomy_status==='skipped'?<section className="panel stack"><h3>Правила технического сравнения</h3><p>Выберите проверяемые параметры. «Информационно» показывает значение, но не отсеивает кандидата.</p><ScrollArea label="Правила параметров"><table><thead><tr><th>Параметр</th><th>Единица</th><th>Сравнение</th><th>Допуск</th></tr></thead><tbody>{draft.features.filter(f=>f.key!=='name').map(f=><tr key={f.key}><td>{f.label}</td><td>{f.unit}</td><td><select aria-label={`Сравнение ${f.label}`} value={f.comparison} onChange={e=>change(f.key,{comparison:e.target.value as Feature['comparison']})} disabled={!w.canExpert||w.active||w.busy}><option value="informational">Информационно</option><option value="eq">Равно</option>{f.kind!=='string'&&<><option value="gte">Не меньше требования</option><option value="lte">Не больше требования</option></>}</select></td><td><input type="number" min="0" step="any" value={f.tolerance} disabled={f.kind==='string'||!w.canExpert||w.active||w.busy} onChange={e=>change(f.key,{tolerance:Number(e.target.value)})}/></td></tr>)}</tbody></table></ScrollArea><button className="btn-primary" disabled={!w.canExpert||w.active||w.busy} onClick={()=>void w.act(()=>request(`/procurement/sessions/${w.sid}/comparison-rules`,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(draft)}),'Правила сохранены. Запустите подбор для применения.')}>Сохранить правила</button></section>:<p>Сначала подготовьте столбцы и выполните H1 на вкладке «Данные».</p>}
 <button className="btn" onClick={()=>w.setTab('data')}>К данным и запуску</button></div>;
}
