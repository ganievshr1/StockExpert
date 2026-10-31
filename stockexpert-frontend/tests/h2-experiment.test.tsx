// @vitest-environment jsdom
import {afterEach,expect,it,vi} from 'vitest';
import {cleanup,render,screen,waitFor} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {H2ExperimentTools,H2_METHODS} from '../src/components/H2ExperimentTools';
const mocks=vi.hoisted(()=>({upload:vi.fn(),checkH2:vi.fn()}));
vi.mock('../src/services/developer',()=>({developerApi:mocks}));
vi.mock('../src/services/laboratory',()=>({labApi:mocks}));
vi.mock('../src/state/Workspace',()=>({useWorkspace:()=>({sid:'s',user:{role:'admin'},busy:false,act:async(fn:any)=>{await fn();return true;}})}));
afterEach(()=>{cleanup();vi.clearAllMocks();});
it('uploads the selected reference and runs an isolated H2 comparison',async()=>{
 const reference=vi.fn(),run=vi.fn();mocks.upload.mockResolvedValue({id:'gold-h2'});
 render(<H2ExperimentTools source="automatic" payload={{job_id:'j',source:'automatic'}} disabled={false} onReference={reference} onRun={run}/>);
 const file=new File(['xlsx'],'gold.xlsx');
 await userEvent.upload(screen.getByLabelText(/Загрузить экспертную разметку H2/),file);
 await waitFor(()=>expect(reference).toHaveBeenCalledWith('gold-h2'));
 expect(mocks.upload).toHaveBeenCalledWith(file);
 await userEvent.click(screen.getByRole('button',{name:'Запустить сравнение H2: 5 методов'}));
 expect(run).toHaveBeenCalledOnce();
});
it('shows closed-test coverage and invalidates it when the seed changes',async()=>{
 mocks.checkH2.mockResolvedValue({rows:100,train_count:74,test_count:26,classes:62,singletons:49,unseen_test_count:13,train_mapping_accuracy_ceiling:.5,warnings:['Редкие категории требуют пополнения']});
 const props={source:'automatic' as const,disabled:false,onReference:vi.fn(),onRun:vi.fn()};
 const {rerender}=render(<H2ExperimentTools {...props} payload={{job_id:'j',dataset_id:'r',seed:42}}/>);
 await userEvent.click(screen.getByRole('button',{name:'Проверить разметку и разделение H2'}));
 await screen.findByText('Редкие категории требуют пополнения');
 expect(mocks.checkH2).toHaveBeenCalledWith('s',expect.objectContaining({dataset_id:'r',seed:42,methods:H2_METHODS}));
 rerender(<H2ExperimentTools {...props} payload={{job_id:'j',dataset_id:'r',seed:43}}/>);
 await waitFor(()=>expect(screen.queryByText('Редкие категории требуют пополнения')).toBeNull());
});
it('does not start with an incomplete selection',async()=>{
 const run=vi.fn();render(<H2ExperimentTools source="manual" payload={{}} disabled={true} onReference={vi.fn()} onRun={run}/>);
 await userEvent.click(screen.getByRole('button',{name:'Запустить сравнение H2: 5 методов'}));
 expect(run).not.toHaveBeenCalled();expect(screen.queryByLabelText(/Загрузить экспертную разметку H2/)).toBeNull();
});
