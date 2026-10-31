"""Semantic checks based on source strings, including misleading model numbers."""
import pytest
from app.services.regex_normalization import extract


def attrs(text):
    return extract(text)[0]['attributes']


@pytest.mark.parametrize('mark,dims,voltage', [
    ('КПвПпБП-150', '3х70', '6,0кВ'),
    ('К1-КПсПпБП-90', '4x25', '1.0kV'),
    ('КПпБПТ-110', '4*70', '3,3кВ'),
])
def test_cable_delimiters_are_not_voltage_ranges(mark, dims, voltage):
    a = attrs(f'Кабель {mark} {dims}-{voltage}')
    assert a['designation'] == mark
    assert a['core_count'] == int(dims[0])
    assert a['core_cross_section_mm2'] == int(dims[2:])
    assert a['voltage_v'] == float(voltage[:-2].replace(',', '.')) * 1000
    assert 'voltage_v_min' not in a


def test_pressure_sensor_keeps_signal_range_and_fractional_thread():
    source = 'Датчик давления ДД-400-4,0МПа-G1/2-4-20мА-ХЛ1'
    p, issues = extract(source)
    a = p['attributes']
    assert a['pressure_mpa'] == 4
    assert a['current_a_min'] == .004 and a['current_a_max'] == .020
    assert 'current_a' not in a
    assert a['pipe_thread'] == 'G1/2'
    assert a['designation'] == 'ДД-400'
    assert not issues
    for e in p['evidence'].values():
        assert source[e['start']:e['end']] == e['quote']


def test_explicit_units_inside_hyphenated_descriptions():
    a = attrs('Радиостанция РС-212-470МГц-10Вт')
    assert a['frequency_hz'] == 470_000_000 and a['power_kw'] == .01
    a = attrs('Установка для гидр.испыт.пож.рукавов УГИР-5-30МПа-220В')
    assert a['pressure_mpa'] == 30 and a['voltage_v'] == 220
    a = attrs('Устройство InfiNet AUX-ODU-LPU-G17-12V-IP66')
    assert a['voltage_v'] == 12 and a['protection_rating'] == 'IP66'


@pytest.mark.parametrize('source', [
    'Агрегат АЭНЦСН 100-250 УХЛ4,1ВАО-560MA-4',
    'Установка УЭВНМТ-ВН1-П-5А-55К1000',
    'Термостабилизатор ТК25/14,5.М7-04 УХЛ1',
    'Тр-р ТМПНГ-250-10-1600-3500-25-2200-660',
    'Эл.дв. ПВЭДН55-130-1490-3.5/201-10-10,ГЗНМ',
])
def test_opaque_codes_do_not_become_physical_parameters(source):
    p, issues = extract(source)
    assert p['attributes']['designation']
    for key in ('current_a', 'power_kw', 'voltage_v', 'rotation_speed_rpm', 'thread'):
        assert key not in p['attributes']
    assert any(i['code'] == 'OPAQUE_MODEL_SEGMENTS' for i in issues)


def test_dimensions_preserve_units_and_axis_uncertainty():
    a = attrs('Плита ПДН,м (6х1,75х0,16м)-АтVI;60; F250')
    assert a['dimensions_mm'] == '6000×1750×160'
    assert a['reinforcement_mark'] == 'АтVI' and a['f_mark'] == 'F250'
    p, issues = extract('Шкаф пожарный ШПК-320-Н-О-456х1271х250')
    assert p['attributes']['dimensions_text'] == '456×1271×250'
    assert 'dimensions_mm' not in p['attributes']
    assert not any(k in p['attributes'] for k in ('length_mm', 'width_mm', 'height_mm'))
    assert any(i['code'] == 'DIMENSION_UNIT_UNSPECIFIED' for i in issues)


def test_threads_capacity_and_valve_ambiguity():
    a = attrs('Шпилька АМ20-6gх500.40.40.II.3 с гайками')
    assert a['thread'] == 'М20' and a['thread_tolerance'] == '6g'
    assert a['accessories'] == 'с гайками'
    a = attrs('Склад баллонов с азотом 36шт 3796/3')
    assert a['cylinder_capacity'] == 36 and a['reference_code'] == '3796/3'
    a = attrs('Задвижка 30лс541нж 250х25 эл/прив. фл. У1')
    assert a['drive'] == 'эл/прив.'
    assert 'pressure_mpa' not in a and 'nominal_diameter_mm' not in a


def test_ranges_and_negations_are_not_scalars():
    a = attrs('Напряжение 220–380 В')
    assert 'voltage_v' not in a and a['voltage_v_min'] == 220 and a['voltage_v_max'] == 380
    assert 'voltage_v_min' not in attrs('не 220–380 В')
    p, issues = extract('Сигнал 20-4мА')
    assert 'current_a' not in p['attributes'] and 'current_a_min' not in p['attributes']
    assert any(i['code'] == 'INVALID_RANGE' for i in issues)
