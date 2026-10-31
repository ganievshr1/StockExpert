"""python scripts/evaluate_h1.py gold.jsonl predictions.jsonl
Each line: {"id":"stable_source_id", "attributes":{"field":value_or_null}}.
Gold and predictions must use identical units/types. Extra/missing rows are errors.
"""
import json,sys
from collections import defaultdict

def load(path):
    values={}
    for line in open(path,encoding='utf-8'):
        obj=json.loads(line)
        if obj['id'] in values: raise ValueError('Duplicate id')
        values[obj['id']]=obj['attributes']
    return values

def evaluate(gold,pred):
    if not gold or set(gold)!=set(pred): raise ValueError('Evaluation requires nonempty identical ID sets')
    counters=defaultdict(lambda:[0,0,0]);exact=0
    for rid,expected in gold.items():
        actual=pred[rid];exact+=expected==actual
        for key in expected.keys()|actual.keys():
            g=expected.get(key);p=actual.get(key);c=counters[key]
            if g is not None and p==g: c[0]+=1
            else:
                if p is not None: c[1]+=1
                if g is not None: c[2]+=1
    def metrics(c):
        tp,fp,fn=c
        return {'tp':tp,'fp':fp,'fn':fn,'precision':tp/(tp+fp) if tp+fp else 0,'recall':tp/(tp+fn) if tp+fn else 0,'f1':2*tp/(2*tp+fp+fn) if 2*tp+fp+fn else 0}
    per={k:metrics(v) for k,v in counters.items()}
    active=[v['f1'] for v in per.values() if v['tp']+v['fp']+v['fn']]
    return {'micro':metrics([sum(v[i] for v in counters.values()) for i in range(3)]),'macro_f1':sum(active)/len(active) if active else 0,'per_field':per,'exact_passport_accuracy':exact/len(gold),'rows':len(gold)}
if __name__=='__main__': print(json.dumps(evaluate(load(sys.argv[1]),load(sys.argv[2])),ensure_ascii=False,indent=2))

