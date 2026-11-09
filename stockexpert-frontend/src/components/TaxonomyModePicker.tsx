import { useId } from 'react';
import { useWorkspace } from '../state/Workspace';
import { api } from '../services/api';

export function TaxonomyModePicker({value,onChange,disabled=false,locked=false}:{value:boolean;onChange:(value:boolean)=>void;disabled?:boolean;locked?:boolean}) {
  const name=useId();
  return <fieldset className="panel stack" disabled={disabled}>
    <legend>Режим H2 — таксономия</legend>
    <p>Отдельная настройка для регулярок и любой LLM. Смена модели не включает H2.</p>
    <label className="checkbox-label"><input type="radio" name={name} checked={value} onChange={()=>onChange(true)}/>С H2 — построить категории</label>
    <small>H1 → категории H2 → поиск H3. После построения дерева эксперт проверяет и утверждает категории.</small>
    <label className="checkbox-label"><input type="radio" name={name} checked={!value} onChange={()=>onChange(false)}/>Без H2 — сразу к поиску</label>
    <small>H1 → поиск H3 по описаниям и техническим параметрам. Дерево категорий не строится, утверждать его не нужно; метрики H2 не рассчитываются.</small>
    {locked&&<p>Режим зафиксирован после начала обработки. Для работы в другом режиме создайте новую сессию.</p>}
  </fieldset>;
}

export function SessionTaxonomyPanel() {
  const w=useWorkspace();const session=w.data?.session;
  if(!session)return null;
  return <TaxonomyModePicker value={session.taxonomy_enabled!==false}
    disabled={!w.canEdit||w.busy||w.active||session.normalization_locked}
    locked={session.normalization_locked}
    onChange={value=>void w.act(()=>api.selectTaxonomy(w.sid,value),'Режим H2 сохранён')}/>;
}
