"""Lossless UE 5.6 tagged GVAS reader. Unknown payloads are never rewritten."""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
import hashlib
import math
import struct
import sys


class SaveError(ValueError):
    pass


FORMATS = {
    "Int8Property": ("b", -(2**7), 2**7 - 1),
    "Int16Property": ("h", -(2**15), 2**15 - 1),
    "IntProperty": ("i", -(2**31), 2**31 - 1),
    "Int64Property": ("q", -(2**63), 2**63 - 1),
    "UInt16Property": ("H", 0, 2**16 - 1),
    "UInt32Property": ("I", 0, 2**32 - 1),
    "UInt64Property": ("Q", 0, 2**64 - 1),
    "ByteProperty": ("B", 0, 255),
    "FloatProperty": ("f", -3.4028234663852886e38, 3.4028234663852886e38),
    "DoubleProperty": ("d", -sys.float_info.max, sys.float_info.max),
}


def decimal_text(value: int | float) -> str:
    return str(value) if isinstance(value, int) else repr(value)


@dataclass
class Numeric:
    id: str
    path: list[str]
    type: str
    offset: int
    value: int | float
    minimum: int | float
    maximum: int | float
    shortcut: str | None = None
    readonly_reason: str = ''

    def public(self) -> dict:
        return {"id": self.id, "path": self.path, "name": self.path[-1],
                "type": self.type, "value": decimal_text(self.value),
                "minimum": decimal_text(self.minimum), "maximum": decimal_text(self.maximum),
                "shortcut": self.shortcut, "editable": math.isfinite(self.value) and not self.readonly_reason,
                "readonly_reason": self.readonly_reason}

    def encode(self, text: str) -> tuple[bytes, str, bool]:
        if not isinstance(text, str) or len(text) > 2048:
            raise SaveError("请输入有效的十进制数值（最多 2048 个字符）")
        try:
            number = Decimal(text.strip())
        except InvalidOperation as exc:
            raise SaveError("无效数值") from exc
        if not number.is_finite():
            raise SaveError("不允许 NaN 或无穷值")
        fmt, _, _ = FORMATS[self.type]
        lo = Decimal.from_float(self.minimum) if isinstance(self.minimum, float) else Decimal(self.minimum)
        hi = Decimal.from_float(self.maximum) if isinstance(self.maximum, float) else Decimal(self.maximum)
        clamped = min(max(number, lo), hi)
        if fmt not in ("f", "d"):
            if clamped != clamped.to_integral_value():
                raise SaveError("整数参数不能填写小数")
            value = int(clamped)
        else:
            value = float(clamped)
        encoded = struct.pack("<" + fmt, value)
        actual = struct.unpack("<" + fmt, encoded)[0]
        if not math.isfinite(actual):
            raise SaveError("数值超出有限范围")
        return encoded, decimal_text(actual), clamped != number


@dataclass
class TypeNode:
    name: str
    children: list[TypeNode] = field(default_factory=list)


class Reader:
    def __init__(self, data: bytes):
        self.data = data
        self.position = 0
        self.limit = len(data)

    def read(self, size: int) -> bytes:
        if size < 0 or self.position + size > self.limit:
            raise SaveError(f"存档截断或字段长度无效（位置 {self.position}）")
        result = self.data[self.position:self.position + size]
        self.position += size
        return result

    def unpack(self, fmt: str):
        return struct.unpack("<" + fmt, self.read(struct.calcsize("<" + fmt)))[0]

    def string(self) -> str:
        size = self.unpack("i")
        if abs(size) > 1_000_000:
            raise SaveError("字符串长度无效")
        if size == 0:
            return ""
        raw = self.read(size if size > 0 else -2 * size)
        terminator = b"\0" if size > 0 else b"\0\0"
        if not raw.endswith(terminator):
            raise SaveError("字符串缺少结束符")
        try:
            return raw[:-len(terminator)].decode("utf-8" if size > 0 else "utf-16le")
        except UnicodeDecodeError as exc:
            raise SaveError("字符串编码无效") from exc

    def tree(self, depth=0) -> TypeNode:
        if depth > 32:
            raise SaveError("类型嵌套过深")
        name = self.string()
        count = self.unpack("i")
        if not 0 <= count <= 32:
            raise SaveError("类型参数数量无效")
        return TypeNode(name, [self.tree(depth + 1) for _ in range(count)])


class Document:
    def __init__(self, data: bytes):
        self.data = data
        self.version = hashlib.sha256(data).hexdigest()
        self.fields: list[Numeric] = []
        self.opaque: list[dict] = []
        self.top_properties: list[dict] = []
        self.properties: list[dict] = []
        self.strings: list[dict] = []
        self.arrays: list[dict] = []
        self.reader = Reader(data)
        r = self.reader
        if r.read(4) != b"GVAS" or r.unpack("i") != 3:
            raise SaveError("仅支持 GVAS v3 / UE5.6 存档")
        self.package_version = r.unpack("i")
        self.ue5_version = r.unpack("i")
        self.engine = [r.unpack("H") for _ in range(3)]
        r.unpack("I")
        self.branch = r.string()
        if self.engine[:2] != [5, 6]:
            raise SaveError("当前解析器仅验证过 UE5.6，拒绝写入其他版本")
        if r.unpack("i") != 3:
            raise SaveError("不支持的自定义版本格式")
        count = r.unpack("i")
        if not 0 <= count <= 10000:
            raise SaveError("自定义版本数量无效")
        r.read(count * 20)
        self.save_class = r.string()
        if r.unpack("B") != 0:
            raise SaveError("不支持的存档属性格式")
        self.stream([], 0)
        if r.read(r.limit - r.position) not in (b"", b"\0\0\0\0"):
            raise SaveError("存档尾部格式未识别")
        self.by_id = {item.id: item for item in self.fields}

    def add(self, path: list[str], kind: str, offset: int):
        # Relocation schema/revision are serialization metadata, not gameplay values.
        if len(path) == 1 and path[0] in ('Schema', 'Revision', 'ContentHash'):
            return
        fmt, lo, hi = FORMATS[kind]
        value = struct.unpack_from("<" + fmt, self.data, offset)[0]
        shortcut = None
        if path == ["货币", "Y"]:
            lo, hi, shortcut = 0, 2**53 - 1, "money"
        elif path[-1] in ("舒适度", "舒适值") and len(path) == 1:
            lo, shortcut = 0, "comfort"
            if fmt in ("d", "f"):
                hi = 2**(53 if fmt == "d" else 24) - 1
        key = "/".join(part.replace("~", "~0").replace("/", "~1") for part in path)
        reason = '搬迁存档含内部内容校验，校验算法验证前仅允许查看和完整备份恢复' if self.save_class == '/Script/ArkPark2.RamenRelocationSaveGame' else ''
        self.fields.append(Numeric(key, path, kind, offset, value, lo, hi, shortcut, reason))

    def stream(self, path: list[str], depth: int):
        if depth > 64:
            raise SaveError("属性嵌套过深")
        r = self.reader
        seen: dict[str, int] = {}
        for _ in range(200000):
            tag_start = r.position
            name = r.string()
            if name == "None":
                return
            node = r.tree()
            size_offset = r.position
            size = r.unpack("i")
            flags = r.unpack("B")
            if flags & ~0x3F:
                raise SaveError("未识别的属性标志")
            index = r.unpack("i") if flags & 1 else 0
            if flags & 2:
                r.read(16)
            # UE extension tags are not yet supported; fail closed rather than guess.
            if flags & 0x20:
                raise SaveError("未支持的扩展属性标签")
            start = r.position
            end = start + size
            if size < 0 or end > r.limit:
                raise SaveError("属性长度越界")
            occurrence = seen.get(name, 0)
            seen[name] = occurrence + 1
            label = name if not index and not occurrence else f"{name}[{index}:{occurrence}]"
            child_path = path + [label]
            properties_start = len(self.properties)
            prop = {"name": name, "path": child_path, "type": node.name, "type_tree": node,
                    "offset": tag_start, "size": end-tag_start, "size_offset": size_offset,
                    "payload_offset": start, "payload_size": size}
            self.properties.append(prop)
            if not path:
                self.top_properties.append({"name": name, "type": node.name, "offset": tag_start,
                                            "size": end-tag_start, "size_offset": size_offset,
                                            "payload_offset": start, "payload_size": size})
            old_limit, r.limit = r.limit, end
            fields_start, opaque_start = len(self.fields), len(self.opaque)
            strings_start, arrays_start = len(self.strings), len(self.arrays)
            try:
                self.value(node, child_path, size, depth + 1)
                if r.position != end:
                    raise SaveError("属性包含未解析数据")
            except (SaveError, struct.error, RecursionError):
                del self.fields[fields_start:]
                del self.opaque[opaque_start:]
                del self.strings[strings_start:]
                del self.arrays[arrays_start:]
                del self.properties[properties_start:]
                self.opaque.append({"path": child_path, "type": node.name, "size": size,
                                    "reason": "未支持的结构，保留原始内容"})
            finally:
                r.limit, r.position = old_limit, end
        raise SaveError("属性数量过多")

    def value(self, node: TypeNode, path: list[str], size: int | None, depth: int):
        r = self.reader
        kind = node.name
        if depth > 64:
            raise SaveError("嵌套过深")
        if kind in FORMATS and not (kind == "ByteProperty" and node.children and node.children[0].name != "None"):
            length = struct.calcsize("<" + FORMATS[kind][0])
            if size is not None and size != length:
                raise SaveError("数值长度不符")
            offset = r.position
            r.read(length)
            self.add(path, kind, offset)
        elif kind in ("StrProperty", "NameProperty", "ObjectProperty", "EnumProperty", "ByteProperty"):
            start = r.position
            text = r.string()
            self.strings.append({'path': path, 'value': text, 'offset': start, 'size': r.position-start})
        elif kind == "BoolProperty":
            if size is None:
                r.read(1)
            elif size != 0:
                raise SaveError("布尔属性长度未知")
        elif kind == "StructProperty":
            if not node.children:
                raise SaveError("缺少结构类型")
            struct_name = node.children[0].name
            native = {"Vector2D": ["X", "Y"], "Vector": ["X", "Y", "Z"],
                      "Vector4": ["X", "Y", "Z", "W"], "Quat": ["X", "Y", "Z", "W"],
                      "Rotator": ["Pitch", "Yaw", "Roll"], "LinearColor": ["R", "G", "B", "A"]}
            if struct_name in native:
                labels = native[struct_name]
                expected = 4 if struct_name == "LinearColor" else 8
                if size is not None and size != len(labels) * expected:
                    raise SaveError("原生结构长度不符")
                for label in labels:
                    self.value(TypeNode("FloatProperty" if expected == 4 else "DoubleProperty"), path+[label], expected, depth+1)
            elif struct_name in ("DateTime", "Timespan"):
                self.value(TypeNode("Int64Property"), path+["Ticks"], size, depth+1)
            elif struct_name == "Color":
                if size not in (None, 4):
                    raise SaveError("颜色长度不符")
                for label in ["B", "G", "R", "A"]:
                    self.value(TypeNode("ByteProperty"), path+[label], 1, depth+1)
            elif struct_name in ("SoftObjectPath", "SoftClassPath"):
                # UE5 top-level asset path stores package and asset FNames,
                # followed by the FString subobject path. Save archives use
                # strings for FNames; retain all three components for research.
                start = r.position
                package, asset, subpath = r.string(), r.string(), r.string()
                text = package + ('.' + asset if asset else '') + (':' + subpath if subpath else '')
                self.strings.append({'path': path, 'value': text, 'offset': start,
                                     'size': r.position-start})
            else:
                self.stream(path, depth + 1)
        elif kind == "ArrayProperty" and len(node.children) == 1:
            count_offset = r.position
            count = r.unpack("i")
            if not 0 <= count <= 200000:
                raise SaveError("数组长度无效")
            entries = []
            for i in range(count):
                start = r.position
                self.value(node.children[0], path+[f"[{i}]"], None, depth+1)
                entries.append({'start': start, 'end': r.position})
            self.arrays.append({'path': path, 'count_offset': count_offset, 'count': count, 'entries': entries})
        else:
            raise SaveError("未支持的属性类型")

    def patch(self, changes: dict[str, str]) -> tuple[bytes, list[dict]]:
        result = bytearray(self.data)
        applied = []
        for key, value in changes.items():
            item = self.by_id.get(key)
            if item is None:
                raise SaveError(f"字段不存在或不可编辑：{key}")
            if item.readonly_reason:
                raise SaveError(item.readonly_reason)
            if not math.isfinite(item.value):
                raise SaveError("原始数值非有限，拒绝修改")
            raw, actual, clamped = item.encode(value)
            result[item.offset:item.offset+len(raw)] = raw
            applied.append({"id": key, "old": decimal_text(item.value), "value": actual, "clamped": clamped})
        output = bytes(result)
        checked = Document(output)
        if checked.by_id.keys() != self.by_id.keys():
            raise SaveError("修改后结构校验失败")
        return output, applied

    def replace_top_array(self, name: str, records: list[bytes]) -> bytes:
        """Rebuild one fully parsed top-level array, retaining every other byte."""
        if len(records) > 200000:
            raise SaveError('数组数量超出已验证解析范围')
        arrays = [a for a in self.arrays if a['path'] == [name]]
        properties = [p for p in self.top_properties if p['name'] == name]
        if len(arrays) != 1 or len(properties) != 1:
            raise SaveError('只能调整完整解析且名称唯一的顶层数组')
        p, a = properties[0], arrays[0]
        if p['type'] != 'ArrayProperty' or a['count_offset'] != p['payload_offset']:
            raise SaveError('数组结构校验失败')
        payload = struct.pack('<i', len(records)) + b''.join(records)
        if len(payload) > 2**31-1:
            raise SaveError('数组内容过大')
        prefix = bytearray(self.data[:p['payload_offset']])
        struct.pack_into('<i', prefix, p['size_offset'], len(payload))
        output = bytes(prefix) + payload + self.data[p['payload_offset']+p['payload_size']:]
        checked = Document(output)
        parsed = [item for item in checked.arrays if item['path'] == [name]]
        if len(parsed) != 1 or parsed[0]['count'] != len(records):
            raise SaveError('调整后的数组无法完整解析')
        return output

    def replace_array(self, path: list[str], records: list[bytes]) -> bytes:
        """Replace a tagged array payload, updating all containing tag lengths.

        Only fully parsed arrays with a unique tag are accepted. Callers validate
        gameplay-specific record types and values before invoking this primitive.
        """
        if len(records) > 200000 or any(not isinstance(r, bytes) for r in records):
            raise SaveError('数组内容或数量无效')
        arrays = [a for a in self.arrays if a['path'] == path]
        props = [p for p in self.properties if p['path'] == path]
        if len(arrays) != 1 or len(props) != 1:
            raise SaveError('只能调整完整解析且路径唯一的数组')
        array, prop = arrays[0], props[0]
        start, size = prop['payload_offset'], prop['payload_size']
        end = start + size
        if prop['type'] != 'ArrayProperty' or array['count_offset'] != start:
            raise SaveError('数组结构校验失败')
        payload = struct.pack('<i', len(records)) + b''.join(records)
        delta = len(payload) - size
        prefix = bytearray(self.data[:start])
        for parent in self.properties:
            if parent['payload_offset'] <= start and parent['payload_offset'] + parent['payload_size'] >= end:
                new_size = parent['payload_size'] + delta
                if not 0 <= new_size <= 2**31-1:
                    raise SaveError('数组内容过大')
                struct.pack_into('<i', prefix, parent['size_offset'], new_size)
        output = bytes(prefix) + payload + self.data[end:]
        checked = Document(output)
        parsed = [a for a in checked.arrays if a['path'] == path]
        if len(parsed) != 1 or parsed[0]['count'] != len(records):
            raise SaveError('调整后的数组无法完整解析')
        # Containing structures must stay parsed; a swallowed parser failure is
        # not a successful write, even if the target metadata survived.
        if [o['path'] for o in checked.opaque] != [o['path'] for o in self.opaque]:
            raise SaveError('调整后出现无法解析的结构')
        return output
