// @vitest-environment jsdom
import {afterEach,expect,it,vi} from 'vitest';
import {cleanup,render,screen,waitFor} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {HypothesisComparison} from '../src/components/HypothesisComparison';
const mocks=vi.hoisted(()=>({request:vi.fn(),exportRevision:vi.fn(),exportExperiment:vi.fn()}));
vi.mock('../src/services/api',()=>({request:mocks.request}));
vi.mock('../src/services/laboratory',()=>({labApi:mocks}));
vi.mock('../src/state/Workspace',()=>({useWorkspace:()=>({user:{role:'admin'},busy:false,act:async(fn:any)=>{await fn();return true;}})}));
afterEach(()=>{cleanup();vi.clearAllMocks();});
it('selects a hypothesis and exports exactly its saved revision',async()=>{
 mocks.request.mockResolvedValue({total:1,items:[{id:'r',session_id:'s',session_title:'Расчёт Qwen',job_id:'j',created_at:'2026-10-24',reference:'Разметка',kind:'revision',method:'llm',model:'Qwen',taxonomy_enabled:false,value:.9,metrics:{},cohort:'same-cohort',scope:'Снимок',status:'READY'}]});
 render(<HypothesisComparison source="automatic"/>);
 await screen.findByText('Расчёт Qwen');
 await userEvent.click(screen.getByRole('tab',{name:'H2',exact:true}));
 await waitFor(()=>expect(mocks.request).toHaveBeenLastCalledWith(expect.stringContaining('hypothesis=H2&source=automatic')));
 await userEvent.click(screen.getByRole('button',{name:'Excel H2'}));
 expect(mocks.exportRevision).toHaveBeenCalledWith('s','r','xlsx','h2');
});
it('keeps manual requests separate and exposes unavailable metrics',async()=>{
 mocks.request.mockResolvedValue({total:0,items:[]});render(<HypothesisComparison source="manual"/>);
 await screen.findByText(/Сохранённых расчётов H1 пока нет/);
 expect(mocks.request).toHaveBeenCalledWith(expect.stringContaining('source=manual'));
});
