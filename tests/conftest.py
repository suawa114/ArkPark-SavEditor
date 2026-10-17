from pathlib import Path
import struct
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def fstring(value: str) -> bytes:
    if value.isascii():
        raw = value.encode() + b'\0'
        return struct.pack('<i', len(raw)) + raw
    raw = value.encode('utf-16le') + b'\0\0'
    return struct.pack('<i', -len(raw)//2) + raw


def node(name, *children):
    return fstring(name) + struct.pack('<i', len(children)) + b''.join(children)


def tag(name, kind, payload, flags=0):
    return fstring(name) + kind + struct.pack('<iB', len(payload), flags) + payload


def fixture_save(extra=b''):
    header = b'GVAS' + struct.pack('<iiiHHHI', 3, 522, 1017, 5, 6, 1, 0)
    header += fstring('++UE5+Release-5.6') + struct.pack('<ii', 3, 0) + fstring('/Game/SG_C') + b'\0'
    vector = node('StructProperty', node('Vector2D', node('/Script/CoreUObject')))
    money = tag('货币', vector, struct.pack('<dd', 38000, 10771.621658259623), 8)
    inner = tag('数量_5_ABCD', node('IntProperty'), struct.pack('<i', 3)) + fstring('None')
    array = tag('背包', node('ArrayProperty', node('StructProperty', node('S_背包'))), struct.pack('<i', 2) + inner + inner)
    unknown = tag('未知数据', node('TextProperty'), b'opaque\0\xff\x12')
    number = tag('64位整数', node('Int64Property'), struct.pack('<q', 2**63-2))
    return header + money + array + number + unknown + extra + fstring('None') + b'\0'*4
