import { request } from './api';
import type { Quality } from './quality';
import type { Hypothesis, H3Population } from '../components/HypothesisMetrics';
export type Source='manual'|'automatic';
export type ExportStage='h1'|'h2'|'h3'|'all';
export type ExportFormat='json'|'xlsx';
export type ExportItems='residual'|'requirement'|'all';
export interface Method {hypothesis:string;label:string;}
export interface H2Check {rows:number;train_count:number;test_count:number;classes:number;singletons:number;unseen_test_count:number;train_mapping_accuracy_ceiling:number;warnings:string[];}
export interface MethodResult extends Method {status:string;metrics:Record<string,number|number[]|null>;note?:string;population?:number;seconds?:number|null;index_seconds?:number;train_count?:number;test_count?:number;query_samples?:number;protocol?:string;reference_confirmed?:boolean;diagnostics?:H2Check;}
export interface Experiment {id:string;job_id:string;source:Source;dataset_id:string|null;status:string;created_at:string;error?:string;result:{model_label?:string;methods?:Record<string,MethodResult>;input_sha256?:string;options?:Record<string,unknown>;conclusions?:Record<string,unknown>};}
export interface Revision {id:string;job_id:string;source:Source;dataset_id:string|null;action:string;created_at:string;result:Partial<Quality>&{hypotheses?:Hypothesis[];h3_population?:H3Population;note?:string};}
export interface Dashboard {job_id:string|null;runs:{id:string;kind:string;created_at:string;snapshot_available:boolean;taxonomy_enabled:boolean}[];manual:Quality;automatic:Revision[];references:{id:string;title:string;version:string}[];history:Revision[];experiments:Experiment[];taxonomy_enabled:boolean;legacy_snapshot?:boolean;}
const post=(data:unknown)=>({method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
async function download(path:string,filename:string){
 const blob=await request<Blob>(path,{},true);const url=URL.createObjectURL(blob);
 const a=document.createElement('a');a.href=url;a.download=filename;document.body.appendChild(a);a.click();a.remove();
 setTimeout(()=>URL.revokeObjectURL(url),1000);
}
export const labApi={
 dashboard:(sid:string,job?:string)=>request<Dashboard>(`/laboratory/${sid}${job?`?job_id=${job}`:''}`),
 methods:(sid:string)=>request<Record<string,Method>>(`/laboratory/${sid}/methods`),
 action:(sid:string,action:'reset'|'recalculate',job_id:string,source:Source,dataset_id?:string)=>request(`/laboratory/${sid}/${action}`,post({job_id,source,...(dataset_id?{dataset_id}:{})})),
 restore:(sid:string,id:string)=>request(`/laboratory/${sid}/history/${id}/recalculate`,post({})),
 start:(sid:string,data:unknown)=>request(`/laboratory/${sid}/experiments`,post(data)),
 checkH2:(sid:string,data:unknown)=>request<H2Check>(`/laboratory/${sid}/h2-check`,post(data)),
 export:(sid:string,job?:string)=>download(`/laboratory/${sid}/export${job?`?job_id=${job}`:''}`,`StockExpert_metrics_${job||'latest'}.xlsx`),
 exportRun:(sid:string,job:string,stage:ExportStage,format:ExportFormat,items:ExportItems='residual')=>download(`/runs/${sid}/${job}/export?${new URLSearchParams({stage,format,item_kind:items})}`,`StockExpert_${stage}_${job}.${format}`),
 exportExperiment:(sid:string,id:string,format:ExportFormat,method?:string,stage:ExportStage='all')=>download(`/laboratory/${sid}/experiments/${id}/export?${new URLSearchParams({format,stage,...(method?{method}:{})})}`,`StockExpert_experiment_${id}${method?'_'+method:''}.${format}`),
 exportRevision:(sid:string,id:string,format:ExportFormat,stage:ExportStage='all')=>download(`/laboratory/${sid}/history/${id}/export?${new URLSearchParams({format,stage})}`,`StockExpert_evaluation_${id}.${format}`),
};
