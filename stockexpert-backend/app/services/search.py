import re,hashlib
from functools import lru_cache
from qdrant_client import QdrantClient, models
from qdrant_client.http.exceptions import UnexpectedResponse
from rank_bm25 import BM25Okapi
from app.core.config import settings
from app.services import llm

def client(): return QdrantClient(url=settings.QDRANT_URL,timeout=60)
def tokens(text): return re.findall(r'[\w]+',text.lower())
def ensure_collection(c, size):
    """Concurrent first writers may both observe a missing collection."""
    try:
        c.create_collection(settings.QDRANT_COLLECTION, vectors_config=models.VectorParams(
            size=size, distance=models.Distance.COSINE))
    except UnexpectedResponse as exc:
        if exc.status_code != 409:
            raise
        # Only an already-created, compatible collection is a benign race.
        config = c.get_collection(settings.QDRANT_COLLECTION).config.params.vectors
        if not isinstance(config, models.VectorParams) or config.size != size or config.distance != models.Distance.COSINE:
            raise ValueError('Concurrent Qdrant collection has incompatible vector configuration') from exc
def index_items(sid,items,heartbeat=lambda:None):
    c=client();stats={'vectors_reused':0,'vectors_created':0}
    exists=c.collection_exists(settings.QDRANT_COLLECTION)
    for start in range(0,len(items),64):
        heartbeat();batch=items[start:start+64]
        ids=list({i.id for i in batch}|{i.passport.get('reuse',{}).get('item_id',i.id) for i in batch})
        cached={str(p.id):p for p in c.retrieve(settings.QDRANT_COLLECTION,ids=ids,with_vectors=True,with_payload=True)} if exists else {}
        vectors={};missing=[]
        for i in batch:
            signature=hashlib.sha256(i.source_text.encode()).hexdigest()
            own=cached.get(i.id);reuse=i.passport.get('reuse',{});old=cached.get(reuse.get('item_id'))
            hit=next((p for p in (own,old) if p and isinstance(p.vector,list) and p.payload.get('embedding_model')==settings.EMBEDDING_MODEL and p.payload.get('text_sha256')==signature and p.payload.get('session_id') in (sid,reuse.get('session_id'))),None)
            if hit:vectors[i.id]=hit.vector;stats['vectors_reused']+=1
            else:missing.append(i)
        for pos in range(0,len(missing),16):
            heartbeat();part=missing[pos:pos+16];generated=llm.embed([i.source_text for i in part])
            if len(generated)!=len(part):raise ValueError('Embedding count mismatch')
            vectors.update({i.id:v for i,v in zip(part,generated)});stats['vectors_created']+=len(part)
        heartbeat()
        if not exists:
            ensure_collection(c,len(next(iter(vectors.values()))));exists=True
        c.upsert(settings.QDRANT_COLLECTION,points=[models.PointStruct(id=i.id,vector=vectors[i.id],payload={'session_id':sid,'item_id':i.id,'category':i.category,'embedding_model':settings.EMBEDDING_MODEL,'text_sha256':hashlib.sha256(i.source_text.encode()).hexdigest()}) for i in batch],wait=True)
    return stats
def retrieve(sid,query,items,limit=500):
    c=client();v=llm.embed([query])[0]
    dense=c.query_points(collection_name=settings.QDRANT_COLLECTION,query=v,query_filter=models.Filter(must=[models.FieldCondition(key='session_id',match=models.MatchValue(value=sid)),models.HasIdCondition(has_id=[i.id for i in items])]),limit=limit).points
    corpus=[tokens(i.source_text) or ['_empty_'] for i in items]
    scores=BM25Okapi(corpus).get_scores(tokens(query))
    sparse=sorted(range(len(items)),key=lambda i:float(scores[i]),reverse=True)[:limit]
    fused={}
    for ranking in ([str(p.id) for p in dense],[items[i].id for i in sparse if scores[i]>0]):
        for rank,ident in enumerate(ranking,1): fused[ident]=fused.get(ident,0)+1/(60+rank)
    return dict(sorted(fused.items(),key=lambda pair:pair[1],reverse=True)[:limit])
@lru_cache(maxsize=1)
def reranker():
    from sentence_transformers import CrossEncoder
    return CrossEncoder(settings.RERANKER_MODEL,device='cpu',max_length=512,trust_remote_code=False)
def rerank(query,items):
    if not items: return {}
    if not settings.RERANKER_ENABLED: return {}  # explicit degraded mode, not fabricated scores
    scores=reranker().predict([(query,i.source_text) for i in items],batch_size=8,show_progress_bar=False)
    return {i.id:float(s) for i,s in zip(items,scores)}

def delete_session_index(sid):
    c=client()
    if c.collection_exists(settings.QDRANT_COLLECTION):
        c.delete(settings.QDRANT_COLLECTION,points_selector=models.FilterSelector(filter=models.Filter(must=[models.FieldCondition(key='session_id',match=models.MatchValue(value=sid))])),wait=True)

