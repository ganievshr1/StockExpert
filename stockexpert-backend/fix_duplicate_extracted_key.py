"""Run with python3 from the StockExpert backend directory. No database changes."""
import ast
from pathlib import Path
import shutil

OLD = '''    by_key={}
    for x in e.values:
        if x.key in by_key: raise ValueError('Duplicate extracted key')
        by_key[x.key]=x
'''
NEW = '''    by_key={};conflicted=set();duplicates=set()
    for x in e.values:
        if x.key not in by_key:
            by_key[x.key]=x
        else:
            duplicates.add(x.key)
            if x.model_dump()!=by_key[x.key].model_dump():
                conflicted.add(x.key)
    for key in sorted(duplicates):
        if key in conflicted:
            by_key[key]=ExtractedValue(key=key,value=None,unit=None,evidence=None)
            issues.append({'key':key,'code':'DUPLICATE_EXTRACTED_KEY_CONFLICT'})
        else:
            issues.append({'key':key,'code':'DUPLICATE_EXTRACTED_KEY_COLLAPSED'})
'''
RULE_OLD="            if k not in allowed: continue"
RULE_NEW="            if k not in allowed or k in conflicted: continue"

def patch_text(text):
    if NEW in text and RULE_NEW in text:
        return text
    if text.count(OLD)!=1 or text.count(RULE_OLD)!=1:
        raise ValueError('Версия файла отличается от ожидаемой. Ничего не изменено.')
    result=text.replace(OLD,NEW,1).replace(RULE_OLD,RULE_NEW,1)
    ast.parse(result)
    return result

if __name__=='__main__':
    path=Path('app/services/normalization.py')
    if not path.is_file():
        raise SystemExit('Запустите скрипт из каталога stockexpert-backend.')
    source=path.read_text(encoding='utf-8')
    try:result=patch_text(source)
    except ValueError as exc:raise SystemExit(str(exc))
    if result==source:
        print('Исправление уже установлено.')
    else:
        backup=path.with_name('normalization.py.before_duplicate_fix')
        if backup.exists():raise SystemExit('Резервная копия уже существует. Ничего не изменено.')
        shutil.copy2(path,backup)
        temporary=path.with_name('normalization.py.duplicate_fix_tmp')
        temporary.write_text(result,encoding='utf-8')
        temporary.replace(path)
        print('Исправлено. Резервная копия: '+str(backup))
        print('Теперь пересоберите backend, worker и beat, затем повторите неудачную задачу в интерфейсе.')

