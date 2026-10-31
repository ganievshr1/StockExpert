// @vitest-environment jsdom
import {afterEach,beforeEach,expect,it,vi} from 'vitest';
import {cleanup,render,screen,within,waitFor} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {LabDashboard} from '../src/components/LabDashboard';
const mocks=vi.hoisted(()=>({workspace:{} as any,dashboard:vi.fn(),methods:vi.fn(),action:vi.fn(),restore:vi.fn(),start:vi.fn(),export:vi.fn(),exportRun:vi.fn(),exportExperiment:vi.fn(),exportRevision:vi.fn()}));
vi.mock('../src/state/Workspace',()=>({useWorkspace:()=>mocks.workspace}));
vi.mock('../src/services/laboratory',()=>({labApi:mocks}));
vi.mock('../src/components/ModelPicker',()=>({ModelPicker:()=>null}));
afterEach(cleanup);
beforeEach(()=>{
 vi.clearAllMocks();mocks.workspace={sid:'s1',canExpert:true,busy:false,active:false,user:{role:'admin'},setTab:vi.fn(),act:async(fn:any)=>{await fn();return true;},data:{job:{status:'COMPLETED'}}};
 mocks.methods.mockResolvedValue({h2_kmeans:{hypothesis:'H2',label:'TF-IDF + k-means по исходным строкам'},h3_bm25:{hypothesis:'H3',label:'BM25'}});
 mocks.dashboard.mockResolvedValue({job_id:'job1',taxonomy_enabled:true,runs:[{id:'job1',kind:'match',created_at:'2026-10-20',snapshot_available:true}],manual:{job_id:'job1',finalized:true,f1:.25,text_f1:.5,accuracy:.75,recall_at_150:1,coverage:{text_tp:1,text_fp:1,text_fn:1,text_reviewed:2,stock_total:99,tp:1,fp:3,fn:3,fields_checked:4,categories_correct:3,categories_checked:4},fields:[]},references:[{id:'gold',title:'Эталон всей базы',version:'1'}],automatic:[{id:'auto',source:'automatic',job_id:'job1',dataset_id:'gold',created_at:'2026-10-20',result:{hypotheses:[{id:'H1',label:'F1 извлечения',value:0,target:.94,formula:'2TP / (2TP+FP+FN)',substitution:'0 / 4',scope:'98 строк',reason:''},{id:'H3',label:'Recall@150',value:1,target:.985,formula:'Среднее по запросам',substitution:'3 / 3',scope:'98 строк; 3 подходящих',reason:''}],h3_population:{catalog_rows:98,requirements:1,pairs_reviewed:98,pairs_total:98,relevant:3,top150:3,hits_150:3,retained_share:3/98,reduction_rate:95/98}}}],history:[],experiments:[]});
});
it('shows manual and automatic figures independently without loaded-reference priority',async()=>{
 render(<LabDashboard/>);
 const manual=await screen.findByRole('region',{name:'Дашборд ручной проверки'});const auto=screen.getByRole('region',{name:'Дашборд автоматического сравнения'});
 expect(within(manual).getByText('25%')).toBeTruthy();expect(within(auto).getByText('0%')).toBeTruthy();
 expect(within(auto).getByText('3 / 3 = 100%')).toBeTruthy();expect(within(auto).getByText(/3 \/ 98 =/)).toBeTruthy();
 expect(screen.getAllByText('TF-IDF + k-means по исходным строкам')).toHaveLength(2);
 await userEvent.click(within(manual).getByRole('button',{name:'Сбросить'}));
 expect(mocks.action).toHaveBeenLastCalledWith('s1','reset','job1','manual',undefined);
 await userEvent.click(within(auto).getByRole('button',{name:'Пересчитать'}));
 expect(mocks.action).toHaveBeenLastCalledWith('s1','recalculate','job1','automatic','gold');
});
it('selects historical run and reopens its saved figures',async()=>{
 const data=await mocks.dashboard();data.runs.push({id:'old',kind:'normalize',created_at:'2026-10-01',snapshot_available:true});mocks.dashboard.mockResolvedValue(data);
 render(<LabDashboard/>);await screen.findByRole('region',{name:'Дашборд ручной проверки'});
 await userEvent.selectOptions(screen.getByRole('combobox',{name:'История запусков'}),'old');
 await waitFor(()=>expect(mocks.dashboard).toHaveBeenCalledWith('s1','old'));
});

it('exports the selected historical run and separately selects requirement rows',async()=>{
 const data=await mocks.dashboard();data.runs.push({id:'old',kind:'normalize',created_at:'2026-10-01',snapshot_available:true});
 mocks.dashboard.mockImplementation((_sid:string,job:string)=>Promise.resolve({...data,job_id:job||'job1'}));
 render(<LabDashboard/>);const panel=await screen.findByRole('region',{name:'Выгрузка результатов запуска'});
 await userEvent.click(within(panel).getByRole('button',{name:'Скачать JSON'}));
 expect(mocks.exportRun).toHaveBeenLastCalledWith('s1','job1','h1','json','residual');
 await userEvent.selectOptions(screen.getByRole('combobox',{name:'История запусков'}),'old');
 await waitFor(()=>expect(screen.getByText(/Запуск: old/)).toBeTruthy());
 const old=screen.getByRole('region',{name:'Выгрузка результатов запуска'});
 await userEvent.selectOptions(within(old).getByRole('combobox',{name:'Позиции H1 / H2'}),'requirement');
 await userEvent.click(within(old).getByRole('button',{name:'Скачать Excel'}));
 expect(mocks.exportRun).toHaveBeenLastCalledWith('s1','old','h1','xlsx','requirement');
 expect((within(old).getByRole('option',{name:'H3 — результаты подбора'}) as HTMLOptionElement).disabled).toBe(true);
});

it('exports a saved experiment and a historical evaluation without mixing sources',async()=>{
 const data=await mocks.dashboard();
 data.experiments=[{id:'experiment-auto',job_id:'job1',source:'automatic',dataset_id:'gold',status:'COMPLETED',created_at:'2026-10-20',result:{methods:{}}}];
 data.history=[{id:'manual-version',job_id:'job1',source:'manual',action:'recalculate',created_at:'2026-10-20',result:{text_f1:.5}}];
 mocks.dashboard.mockResolvedValue(data);render(<LabDashboard/>);
 const auto=await screen.findByRole('region',{name:'Дашборд автоматического сравнения'});
 await userEvent.click(within(auto).getByRole('button',{name:'Эксперимент: JSON'}));
 expect(mocks.exportExperiment).toHaveBeenLastCalledWith('s1','experiment-auto','json');
 const manual=screen.getByRole('region',{name:'Дашборд ручной проверки'});
 expect(within(manual).queryByRole('button',{name:'Эксперимент: JSON'})).toBeNull();
 await userEvent.click(screen.getByRole('button',{name:'Открыть'}));
 await userEvent.click(screen.getByRole('button',{name:'Оценка: Excel'}));
 expect(mocks.exportRevision).toHaveBeenLastCalledWith('s1','manual-version','xlsx');
});
