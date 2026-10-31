"""Flat, readable H2 tables. No fabricated values for unrun methods or tree reviews."""

def tables(data):
    result=data.get('result',{})
    methods={k:v for k,v in result.get('methods',{}).items() if v.get('hypothesis')=='H2'}
    if not methods:return []
    rows={r['item_id']:r for r in data.get('rows',[])}
    options=result.get('options',{})
    summary=[]; detail=[]; splits=[]; catalogue=[]; groups=[]; edges=[]
    for key,value in methods.items():
        m=value.get('metrics',{});ci=m.get('accuracy_ci95') or [None,None];dc=m.get('delta_ci95') or [None,None]
        summary.append([key,value.get('label'),value.get('status'),value.get('train_count'),m.get('n_test',value.get('test_count')),
                        m.get('n_correct'),m.get('accuracy'),ci[0],ci[1],m.get('unassigned_count'),m.get('unassigned_rate'),
                        m.get('noise_rate'),m.get('ari'),m.get('v_measure'),m.get('silhouette'),m.get('parent_errors'),
                        m.get('accuracy_l1'),m.get('accuracy_l2'),m.get('accuracy_l3'),m.get('edge_accuracy'),m.get('edge_reviewed'),
                        m.get('delta_accuracy'),dc[0],dc[1],value.get('seconds'),m.get('cluster_accuracy_hungarian'),
                        value.get('reference_confirmed',False),value.get('protocol','legacy'),value.get('note')])
        for ident,pred in value.get('predictions',{}).items():
            row=rows.get(ident,{})
            detail.append([key,ident,row.get('source_filename'),row.get('sheet'),row.get('row'),row.get('source_text'),
                           pred.get('expected_category_id'),' / '.join(pred.get('expected_path',[])) or pred.get('expected'),
                           pred.get('category_id'),' / '.join(pred.get('category_path',[])) or pred.get('mapped_category'),
                           pred.get('cluster'),pred.get('correct'),pred.get('unassigned'),pred.get('parent_error'),pred.get('error')])
        for r in value.get('split',[]):
            row=rows.get(r['item_id'],{})
            splits.append([key,r['item_id'],row.get('source_filename'),row.get('sheet'),row.get('row'),row.get('source_text'),r['part'],r['expected_category_id'],' / '.join(r['expected_path']),r['class_in_train'],r.get('split_group','')])
        for c in value.get('approved_tree',[]):catalogue.append([key,c['id'],*c['path'],*([None]*(3-len(c['path'])))])
        for cluster,category in value.get('mapping',{}).items():groups.append([key,cluster,category])
        for e in value.get('edges',[]):edges.append([key,e['id'],' / '.join(e['parent_path']),' / '.join(e['child_path']),None,None])
    comparisons=[]
    for c in result.get('conclusions',{}).get('h2',{}).get('comparisons',[]):
        if c['left'] not in methods or c['right'] not in methods:continue
        ci=c.get('delta_ci95') or [None,None]
        comparisons.append([c['purpose'],c['left'],c['right'],c['same_test'],c['n'],c['delta_accuracy'],ci[0],ci[1]])
    protocol=[['Основная метрика','Accuracy = N_correct / N_test. Совпадение по ID конечного узла фиксированного дерева. Отказы входят в N_test.'],
              ['Условие H2','Полный метод: Accuracy ≥0.85 и Δ к TF-IDF+k-means >0 на том же тесте. Парный 95% ДИ сообщается отдельно.'],
              ['Уровни','Accuracy L1/L2/L3 — точное совпадение пути до данного уровня. Главная Accuracy — конечный узел для всей тестовой части.'],
              ['ARI','Каждая позиция без группы/категории имеет отдельную метку одиночного кластера.'],
              ['Отказы и шум','Без категории — итоговое назначение пусто. Шум — выход HDBSCAN до назначения. Для методов без HDBSCAN шум не рассчитывается.'],
              ['Верность связей','Только экспертно оценённые предложенные связи / все проверенные предложенные связи. Пока оценки нет, значение пусто. Лист H2_связи предназначен для отдельной экспертной проверки.'],
              ['Венгерская accuracy','Дополнительная оптимистическая диагностика групп на тесте. Не использовать для подтверждения H2.'],
              ['Протокол',result.get('protocol')],['Эталон подтверждён',bool(options.get('h2_reference_confirmed'))],
              ['Seed',options.get('seed')],['Доля обучения',options.get('train_fraction')],['k',options.get('clusters')],
              ['Дубли','Точные текстовые дубли и смысловые группы split_group из эталона не разделяются между train и test. Группы проверяет эксперт до запуска.'],
              ['Минимум HDBSCAN',options.get('min_cluster_size')],['Вес названия',options.get('h2_name_weight',1)],['Вес атрибутов',options.get('h2_attribute_weight',2)],
              ['Модель LLM',result.get('model_label')],['Embedding',result.get('models',{}).get('embedding')],
              ['Контроль входов',result.get('input_sha256')],['Старые расчёты','legacy использует прежнюю методику. Перезапустите методы для нового отчёта.']]
    return [
      ('H2_сводка',['Метод','Название','Статус','Train','N_test','N_correct','Accuracy','Accuracy ДИ низ','Accuracy ДИ верх','Без категории N','Без категории доля','Шум HDBSCAN','ARI','V-мера','Силуэт','Ошибки родителей N','Accuracy L1','Accuracy L2','Accuracy L3','Верность связей','Проверено связей','Δ к k-means','Δ ДИ низ','Δ ДИ верх','Время с','Венгерская диагностика','Эталон подтверждён','Протокол','Ограничения'],summary),
      ('H2_сравнения',['Сравнение','Метод A','Метод B','Тот же тест','N пар','Δ Accuracy B−A','ДИ низ','ДИ верх'],comparisons),
      ('H2_построчно',['Метод','ID позиции','Файл','Лист','Строка','Исходное описание','ID эталона','Путь эталона','ID предсказания','Путь предсказания','Кластер','Верно','Без категории','Ошибка родителя','Причина'],detail),
      ('H2_разделение',['Метод','ID позиции','Файл','Лист','Строка','Исходное описание','Часть','ID категории','Путь категории','Класс есть в train','Группа дублей'],splits),
      ('H2_эталонное_дерево',['Метод','ID листа','L1','L2','L3'],catalogue),
      ('H2_соответствие',['Метод','Кластер','ID конечной категории'],groups),
      ('H2_связи',['Метод','ID связи','Предложенный родитель','Предложенный потомок','Эксперт: верно 1 или 0','Комментарий эксперта'],edges),
      ('H2_методика',['Поле','Значение'],protocol)]
