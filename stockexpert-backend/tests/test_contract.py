from app.db import SessionLocal,Job,Match,Item,Document


def test_contract_and_empty_summaries(client,auth):
    assert client.get('/api/v1/system/config',headers=auth).json()['api_contract_version']=='3.6'
    sid=client.post('/api/v1/procurement/sessions',headers=auth,json={'title':'Contract'}).json()['id']
    with SessionLocal() as db:
        d=Document(session_id=sid,kind='residual',filename='test.xlsx',object_key='test',sha256='0'*64,meta={});db.add(d);db.flush()
        db.add(Item(session_id=sid,document_id=d.id,kind='residual',row_index=1,raw={'0':'test'},source_text='test',passport=None));db.commit()
    metrics=client.get(f'/api/v1/procurement/sessions/{sid}/metrics',headers=auth).json()
    assert metrics['total_rows']==1 and metrics['normalized_items']==0
    summary=client.get(f'/api/v1/reports/{sid}/summary?include_items=false',headers=auth).json()
    assert summary['items']==[] and summary['pairs_evaluated']==0


def test_candidates_database_pagination_and_filtering(client,auth):
    sid=client.post('/api/v1/procurement/sessions',headers=auth,json={'title':'Pagination'}).json()['id']
    with SessionLocal() as db:
        d=Document(session_id=sid,kind='residual',filename='test.xlsx',object_key='test',sha256='0'*64,meta={});db.add(d);db.flush()
        req=Item(session_id=sid,document_id=d.id,kind='requirement',row_index=0,raw={},source_text='Потребность');db.add(req)
        items=[Item(session_id=sid,document_id=d.id,kind='residual',row_index=i+1,raw={},source_text=f'Остаток {i}') for i in range(3)];db.add_all(items)
        j=Job(session_id=sid,kind='match',status='COMPLETED');db.add(j);db.flush()
        for i,item in enumerate(items):db.add(Match(session_id=sid,job_id=j.id,requirement_id=req.id,item_id=item.id,result={'rank':i+1 if i<2 else None,'is_shortlisted':i<2,'state':'NEEDS_REVIEW' if i<2 else 'CONTRADICTED','reason':'Test'}))
        db.commit()
    first=client.get(f'/api/v1/matching/{sid}/candidates?limit=1',headers=auth).json()
    second=client.get(f'/api/v1/matching/{sid}/candidates?limit=1&offset=1',headers=auth).json()
    assert first['total']==2 and first['items'][0]['rank']==1
    assert second['items'][0]['rank']==2 and first['items'][0]['residual']['source_text']=='Остаток 0'
    rejected=client.get(f'/api/v1/matching/{sid}/candidates?shortlisted=false&status=CONTRADICTED',headers=auth).json()
    assert rejected['total']==1 and rejected['items'][0]['rank'] is None
    summary=client.get(f'/api/v1/reports/{sid}/summary?include_items=false',headers=auth).json()
    assert summary['pairs_evaluated']==3 and summary['contradicted_pairs']==1 and summary['shortlisted_pairs']==2

