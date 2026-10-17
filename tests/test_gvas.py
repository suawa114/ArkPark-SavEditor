from decimal import Decimal
from pathlib import Path
import struct
import sys

import pytest

from backend.gvas import Document, Numeric, SaveError, FORMATS
from conftest import fixture_save, tag, node


def test_lossless_and_structural_lookup():
    data = fixture_save()
    doc = Document(data)
    output, applied = doc.patch({})
    assert output == data and not applied
    assert doc.by_id['货币/Y'].value == 10771.621658259623
    assert doc.by_id['背包/[1]/数量_5_ABCD'].value == 3
    assert doc.by_id['64位整数'].value == 2**63-2
    assert doc.opaque[0]['path'] == ['未知数据']


def test_only_target_bytes_change():
    doc = Document(fixture_save())
    field = doc.by_id['货币/Y']
    output, applied = doc.patch({field.id: '99999.5'})
    assert Document(output).by_id[field.id].value == 99999.5
    assert output[:field.offset] == doc.data[:field.offset]
    assert output[field.offset+8:] == doc.data[field.offset+8:]
    assert applied[0]['value'] == '99999.5'


@pytest.mark.parametrize('kind', FORMATS)
def test_numeric_bounds(kind):
    fmt, lo, hi = FORMATS[kind]
    field = Numeric('field', ['field'], kind, 0, 1, lo, hi)
    for text, expected in [('1e1000', hi), ('-1e1000', lo)]:
        encoded, actual, clamped = field.encode(text)
        assert clamped
        assert struct.unpack('<'+fmt, encoded)[0] == expected
        assert Decimal(actual).is_finite()


@pytest.mark.parametrize('text', ['NaN', 'Infinity', '-Infinity', '', 'abc', '1,000'])
def test_invalid_input(text):
    with pytest.raises(SaveError):
        Document(fixture_save()).patch({'货币/Y': text})


def test_integer_fractions_and_unknown_keys():
    doc = Document(fixture_save())
    with pytest.raises(SaveError): doc.patch({'64位整数': '1.1'})
    with pytest.raises(SaveError): doc.patch({'未知数据': '123'})


def test_currency_safe_integer_limit_and_nonnegative():
    doc = Document(fixture_save())
    output, applied = doc.patch({'货币/Y': '1e400'})
    assert Document(output).by_id['货币/Y'].value == 2**53-1
    assert applied[0]['clamped']
    output, applied = doc.patch({'货币/Y': '-5'})
    assert Document(output).by_id['货币/Y'].value == 0


def test_bad_files():
    for data in [b'', b'badfile', fixture_save()[:-15], fixture_save()+b'BAD']:
        with pytest.raises(SaveError): Document(data)


def test_float_and_vector_boundaries():
    extra = tag('浮点', node('FloatProperty'), struct.pack('<f', 1.25))
    doc = Document(fixture_save(extra))
    output, applied = doc.patch({'浮点': '1e500'})
    assert Document(output).by_id['浮点'].value == FORMATS['FloatProperty'][2]
    assert applied[0]['clamped']


def test_failed_structure_discards_all_partial_metadata():
    from conftest import fstring
    inner = tag('部分字符串', node('StrProperty'), fstring('床'))
    inner += tag('数组', node('ArrayProperty', node('IntProperty')), struct.pack('<ii', 1, 7))
    # Valid subproperties followed by a truncated tag make the outer struct opaque.
    extra = tag('坏结构', node('StructProperty', node('Custom')), inner + b'bad')
    doc = Document(fixture_save(extra))
    assert not any(s['path'][0] == '坏结构' for s in doc.strings)
    assert not any(a['path'][0] == '坏结构' for a in doc.arrays)
    assert not any(f.path[0] == '坏结构' for f in doc.fields)
    assert not any(p['path'][0] == '坏结构' for p in doc.properties)
    assert doc.patch({})[0] == doc.data


def test_nested_array_resize_retains_opaque_and_other_records():
    from conftest import fstring
    skill_type = node('ArrayProperty', node('EnumProperty', node('E_技能')))
    def weapon(skills):
        return (tag('技能', skill_type, struct.pack('<i', len(skills)) + b''.join(fstring(s) for s in skills))
                + tag('原样保留', node('TextProperty'), b'unknown\x00\xff') + fstring('None'))
    records = [weapon([]), weapon(['E_技能::NewEnumerator10'])]
    extra = tag('发射器', node('ArrayProperty', node('StructProperty', node('S_发射器'))),
                struct.pack('<i', 2) + b''.join(records))
    doc = Document(fixture_save(extra))
    target = ['发射器', '[0]', '技能']
    original = doc.data
    assert doc.replace_array(target, []) == original
    new = Document(doc.replace_array(target, [fstring('E_技能::NewEnumerator2'), fstring('雷电')]))
    assert [s['value'] for s in new.strings if s['path'][:3] == target] == ['E_技能::NewEnumerator2', '雷电']
    assert new.opaque == doc.opaque
    outer = next(a for a in new.arrays if a['path'] == ['发射器'])
    sibling = outer['entries'][1]
    assert new.data[sibling['start']:sibling['end']] == records[1]
    assert new.replace_array(target, []) == original
    with pytest.raises(SaveError):
        doc.replace_array(target, [b'bad'])
    with pytest.raises(SaveError):
        doc.replace_array(['不存在'], [])


def test_relocation_metadata_cannot_be_edited():
    extra = b''.join(tag(name, node('IntProperty'), struct.pack('<i', 1)) for name in ('Schema', 'Revision', 'ContentHash'))
    doc = Document(fixture_save(extra))
    assert not {'Schema', 'Revision', 'ContentHash'} & doc.by_id.keys()
    with pytest.raises(SaveError):
        doc.patch({'Schema': '0'})


def test_array_rebuild_preserves_surrounding_data():
    doc = Document(fixture_save())
    array = next(a for a in doc.arrays if a['path'] == ['背包'])
    records = [doc.data[e['start']:e['end']] for e in array['entries']]
    assert doc.replace_top_array('背包', records) == doc.data
    output = doc.replace_top_array('背包', records + [records[0]])
    result = Document(output)
    assert result.by_id['背包/[2]/数量_5_ABCD'].value == 3
    assert result.opaque == doc.opaque
    prop = next(p for p in doc.top_properties if p['name'] == '背包')
    assert output[:prop['size_offset']] == doc.data[:prop['size_offset']]
    assert output[-(len(doc.data)-prop['payload_offset']-prop['payload_size']):] == doc.data[prop['payload_offset']+prop['payload_size']:]
    with pytest.raises(SaveError):
        doc.replace_top_array('背包', [b'bad'])


def test_soft_class_path_unicode_and_truncation_preserved():
    from conftest import fstring
    payload = fstring('/Game/家具/床') + fstring('床_C') + fstring('')
    extra = tag('演员类', node('StructProperty', node('SoftClassPath')), payload)
    doc = Document(fixture_save(extra))
    assert next(s['value'] for s in doc.strings if s['path'] == ['演员类']) == '/Game/家具/床.床_C'
    assert doc.patch({})[0] == doc.data
    broken = tag('演员类', node('StructProperty', node('SoftClassPath')), payload[:-2])
    doc = Document(fixture_save(broken))
    assert not any(s['path'] == ['演员类'] for s in doc.strings)
    assert any(o['path'] == ['演员类'] for o in doc.opaque)
    assert doc.patch({})[0] == doc.data


REAL = Path.home() / 'AppData/Local/ArkPark2/Saved/SaveGames'


@pytest.mark.skipif(not REAL.is_dir(), reason='此机器没有真实存档样本')
def test_all_real_saves_readonly():
    for path in REAL.glob('*.sav'):
        original = path.read_bytes()
        doc = Document(original)
        assert doc.patch({})[0] == original
        if '货币/Y' in doc.by_id:
            field = doc.by_id['货币/Y']
            output, _ = doc.patch({'货币/Y': '123456.25'})
            assert output[:field.offset] == original[:field.offset]
            assert output[field.offset+8:] == original[field.offset+8:]
            assert Document(output).by_id['货币/Y'].value == 123456.25
