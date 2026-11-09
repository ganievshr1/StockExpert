import pytest
from app.services.regex_normalization import extract, VERSION, RULES_SHA256


def values(text):
    return extract(text)[0]['attributes']


def test_cable_groups_are_separate_and_grounded():
    source = 'Кабель силовой КГ-ХЛ 3х95+1х25'
    passport, issues = extract(source)
    assert passport['attributes']['core_count'] == 3
    assert passport['attributes']['core_cross_section_mm2'] == 95
    assert passport['attributes']['additional_core_count'] == 1
    assert passport['attributes']['additional_core_cross_section_mm2'] == 25
    assert passport['attributes']['designation'] == 'КГ-ХЛ'
    assert passport['provenance']['llm_calls'] == 0
    assert passport['provenance']['rules_version'] == VERSION
    assert passport['provenance']['rules_sha256'] == RULES_SHA256
    for evidence in passport['evidence'].values():
        assert source[evidence['start']:evidence['end']] == evidence['quote']


def test_dimensions_are_not_interpreted_as_cable_without_type():
    assert 'core_count' not in values('Размер упаковки 3х95')
    assert 'nominal_diameter_mm' not in values('Кран шаровой 100х25')


def test_composite_pipe_does_not_turn_outer_diameter_into_dn():
    passport, issues = extract('Труба 219х6 В 09Г2С+2ф34х3-ВНП-ППУ421ОЦ')
    assert passport['attributes']['outside_diameter_mm'] == 219
    assert passport['attributes']['wall_thickness_mm'] == 6
    assert 'nominal_diameter_mm' not in passport['attributes']
    assert any(i['code'] == 'COMPOSITE_PIPE_NEEDS_REVIEW' for i in issues)


def test_units_and_abbreviated_motor_are_deterministic():
    source = 'Эд.в. модель: АИР100 0,38 кВ 7,5 кВт 50 Гц 1500 об/мин'
    p, _ = extract(source)
    assert p['type_hints'] == ['motor']
    assert p['attributes']['voltage_v'] == 380
    assert p['attributes']['power_kw'] == 7.5
    assert p['attributes']['frequency_hz'] == 50
    assert p['attributes']['rotation_speed_rpm'] == 1500
    assert p['attributes']['designation'] == 'АИР100'
    assert values('Давление 16 bar')['pressure_mpa'] == 1.6
    assert values('Длина 2,5 м')['length_mm'] == 2500


def test_absence_and_ambiguous_alternatives_are_not_guessed():
    a = values('Шкаф ПРС-М 63А IP54, без вилок')
    assert a['current_a'] == 63 and a['protection_rating'] == 'IP54'
    assert a['accessories'] == 'без вилок'
    a, issues = extract('Двигатель 220 В или 380 В')
    assert a['attributes']['voltage_v'] is None
    assert any(i['code'] == 'CONFLICTING_VALUES' for i in issues)
    assert 'voltage_v' not in values('Не 380 В; уточнить у поставщика')
    assert 'voltage_v' not in values('Напряжение 220–380 В')
    assert 'voltage_v' not in values('Напряжение до 1000 В')


def test_same_value_in_different_units_is_not_a_conflict():
    assert values('Напряжение 380 В (0,38 кВ)')['voltage_v'] == 380


def test_unknown_item_retains_source_without_fake_attributes():
    p, issues = extract('Изделие специальное АБВ-777')
    assert p['attributes'] == {'name': 'Изделие специальное АБВ-777'}
    assert p['category_id'] is None
    assert any(i['code'] == 'NO_RECOGNIZED_PROPERTIES' for i in issues)
    assert extract('Изделие специальное АБВ-777') == (p, issues)
    with pytest.raises(ValueError):
        extract('a' * 16001)
