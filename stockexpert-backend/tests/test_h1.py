from scripts.evaluate_h1 import evaluate

def test_wrong_value_counts_fp_and_fn():
    r=evaluate({'a':{'x':1,'y':2}},{'a':{'x':1,'y':3}})
    assert r['micro']['tp']==1 and r['micro']['fp']==1 and r['micro']['fn']==1
    assert r['micro']['f1']==0.5

