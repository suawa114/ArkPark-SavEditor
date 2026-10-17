"""Version-bound display mappings and lossless, schema-checked weapon skill edits."""
from __future__ import annotations

from functools import lru_cache
import hashlib
import json
from pathlib import Path
import re
import struct

from .gvas import Document, SaveError, decimal_text

CATALOG = json.loads(Path(__file__).with_name('gameplay_catalog.json').read_text(encoding='utf-8'))
MAIN_CLASS = '/Game/ArkPark/Modes/GMs/主菜单/SG_ArkPark2.SG_ArkPark2_C'
WEAPONS = 'S_当前使用的的发射器'
SKILLS = '携带的核心'
STATS = ('制作美食数量', '拉面龙总营业额度', '完成派送外卖单数', '派送次数',
         '拾取次数', '捞桶次数', '世界岛日期', '世界岛星期')


def label(name: str) -> str:
    return re.sub(r'_\d+_[0-9a-fA-F]{32}$', '', name)


@lru_cache(maxsize=16)
def _file_hash(path: str, size: int, modified: int, changed: int) -> str:
    with open(path, 'rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def catalog_reason(game_dir: str) -> str:
    for relative, expected in CATALOG['fingerprints'].items():
        path = Path(game_dir) / relative
        try:
            stat = path.stat()
            actual = _file_hash(str(path.resolve()), stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
        except OSError:
            return '无法核对游戏资源版本，请在设置中确认游戏目录；名称映射、技能及剧情修改暂不可用。'
        if actual != expected:
            return '游戏资源版本与已验证映射不符，名称映射、技能及剧情修改暂不可用。'
    return ''


def _array(doc: Document, path: list[str]):
    found = [a for a in doc.arrays if a['path'] == path]
    return found[0] if len(found) == 1 else None


def _child(items, path: list[str], name: str):
    found = [item for item in items if len(item['path']) == len(path)+1
             and item['path'][:-1] == path and label(item['path'][-1]) == name]
    return found[0] if len(found) == 1 else None


def _numeric(doc: Document, path: list[str], name: str):
    found = [f for f in doc.fields if f.path[:-1] == path and label(f.path[-1]) == name]
    return found[0] if len(found) == 1 else None


def quest_text(stage: str) -> dict:
    return {key: value.replace('\\r\\n', '\n').replace('\\n', '\n').replace('\r\n', '\n')
            for key, value in CATALOG['quests'].get(stage, {}).items()}


def story_options() -> list[dict]:
    return [{'stage': stage, **quest_text(stage),
             'selectable': bool(quest_text(stage).get('name') and quest_text(stage).get('objective'))}
            for stage in sorted(CATALOG['quests'], key=int)]


def patch_numbers(data: bytes, changes: dict[str, str], game_dir: str):
    doc = Document(data)
    stage = _numeric(doc, [], '剧情任务索引')
    story = None
    if stage and stage.id in changes:
        if doc.save_class != MAIN_CLASS or stage.type != 'IntProperty':
            raise SaveError('剧情索引类型未核实')
        reason = catalog_reason(game_dir)
        if reason:
            raise SaveError(reason)
        # Let the numeric encoder validate syntax and precision before semantic validation.
        _, actual, _ = stage.encode(changes[stage.id])
        text = str(actual)
        target = quest_text(text)
        if not target.get('name') or not target.get('objective'):
            raise SaveError('请选择有任务标题与目标的已核实剧情阶段；空白阶段不能视作通关')
        old = decimal_text(stage.value)
        if old != text:
            story = {'old': {'stage': old, **quest_text(old)}, 'value': {'stage': text, **target},
                     'warning': '仅修改剧情索引，不补发奖励或同步角色、地图和前置任务状态；可能出现任务无法继续。'}
    output, applied = doc.patch(changes)
    return output, applied, story


def _skill_state(doc: Document, index: int):
    if doc.save_class != MAIN_CLASS:
        raise SaveError('仅支持已核实的主存档发射器结构')
    outer = _array(doc, [WEAPONS])
    prop = _child(doc.properties, [], WEAPONS)
    if outer is None or prop is None or not 0 <= index < outer['count']:
        raise SaveError('发射器编号不存在或结构无法完整解析')
    tree = prop['type_tree']
    if (tree.name != 'ArrayProperty' or len(tree.children) != 1
            or tree.children[0].name != 'StructProperty' or not tree.children[0].children
            or tree.children[0].children[0].name != 'S_发射器数据'):
        raise SaveError('发射器数组类型未核实')
    prefix = [WEAPONS, f'[{index}]']
    prop = _child(doc.properties, prefix, SKILLS)
    if prop is None:
        raise SaveError('附加技能数组未完整解析')
    tree = prop['type_tree']
    if (tree.name != 'ArrayProperty' or len(tree.children) != 1
            or tree.children[0].name != 'ByteProperty' or len(tree.children[0].children) != 1
            or tree.children[0].children[0].name != 'E_发射器元素核心'):
        raise SaveError('附加技能枚举类型未核实')
    path = prop['path']
    array = _array(doc, path)
    if array is None:
        raise SaveError('附加技能数组未完整解析')
    values = []
    for position in range(array['count']):
        string = _child(doc.strings, path, f'[{position}]')
        if string is None:
            raise SaveError('附加技能元素未完整解析')
        values.append(string['value'])
    return path, values


def collection_statistics(doc: Document, home: Document | None = None) -> list[dict]:
    """Count saved collections; missing or incomplete sources never imply zero."""
    def array(path):
        return _array(doc, path)

    def count(path):
        found = array(path)
        return found['count'] if found is not None else None

    def names(path, child=None):
        found = array(path)
        if found is None:
            return None
        values = []
        for index in range(found['count']):
            prefix = path + [f'[{index}]']
            item = _child(doc.strings, prefix, child) if child else _child(doc.strings, path, prefix[-1])
            if item is None or not item['value'] or item['value'] == 'None':
                return None
            values.append(item['value'])
        return values

    def distinct(path, child=None):
        values = names(path, child)
        return len(set(values)) if values is not None else None

    def nested_count(path, child):
        found = array(path)
        if found is None:
            return None
        total = 0
        for index in range(found['count']):
            prefix = path + [f'[{index}]']
            prop = _child(doc.properties, prefix, child)
            nested = array(prop['path']) if prop else None
            if nested is None:
                return None
            total += nested['count']
        return total

    def member_array(parent, child):
        prop = _child(doc.properties, [parent], child)
        return prop['path'] if prop else [parent, child]

    on_menu = names(['已上架的菜谱'], '名字')
    off_menu = names(['未上架的菜谱'], '名字')
    recipe_types = len(set(on_menu + off_menu)) if on_menu is not None and off_menu is not None else None
    home_array = _array(home, ['S_建造系统']) if home and home.save_class == MAIN_CLASS else None
    metrics = [
        ('已上架菜谱', count(['已上架的菜谱']), '当前上架列表的条目数。'),
        ('未上架菜谱', count(['未上架的菜谱']), '当前未上架列表的条目数。'),
        ('菜谱总种类', recipe_types, '上架与未上架菜谱按名称合并去重。'),
        ('店员列表人数', count(['店员BB']), '存档店员列表人数，不代表当前实际在岗人数。'),
        ('休假店员人数', count(['休假的店员BB']), '休假列表人数，不与店员列表直接相加。'),
        ('养殖动物数量', count(['养殖小动物']), '当前养殖记录数，不是累计捕获数量。'),
        ('养殖动物种类', distinct(['养殖小动物'], '小动物'), '按存档中的动物类去重。'),
        ('已有大脑袋数量', distinct(member_array('S_先行者套装', '拥有的大脑袋')), '拥有的大脑袋列表按名称去重。'),
        ('已有衣服数量', distinct(member_array('S_先行者套装', '拥有的衣服')), '拥有的衣服列表按名称去重。'),
        ('已解锁船只数量', distinct(['C_解锁的船只']), '解锁列表按船只类去重。'),
        ('已认识发射器数量', distinct(['认识的发射器']), '认识列表按名称去重，不代表已拥有数量。'),
        ('发射器皮肤记录数', nested_count(['S_已解锁的发射器配件'], '已解锁的皮肤'), '各武器皮肤记录数合计，同名皮肤在不同武器上分别计数。'),
        ('收集档案数量', count(['档案']), '存档档案列表的条目数。'),
        ('地区见闻记录数', nested_count(['S_地区信息'], '见闻'), '各地区见闻条目数合计，同名见闻在不同地区分别计数。'),
        ('背包物品种类', distinct(['S_背包物资'], '表格索引'), '按物品编号去重，相同物品占多个位置只计一种。'),
        ('家园建造记录数', home_array['count'] if home_array else None, '读取当前槽位配对家园，包含墙、地板及家具等建造记录。'),
    ]
    return [{'label': name, 'value': str(value) if value is not None else '未记录',
             'note': note + (' 当前字段缺失、无法完整解析或配对家园不可读。' if value is None else '')}
            for name, value, note in metrics]


def inspect(doc: Document, game_dir: str, home: Document | None = None) -> dict:
    result = {'available': False, 'weapons': [], 'skill_options': [], 'story_options': [], 'inventory': [],
              'story': {'available': False, 'editable': False,
                        'reason': '剧情进度未记录：当前存档没有剧情阶段字段，无法查看或修改；不影响其他已解析参数的编辑。'},
              'statistics': []}
    if doc.save_class != MAIN_CLASS:
        result['reason'] = '当前文件不是已核实的主存档，玩法面板不可用。'
        return result
    result['available'] = True
    reason = catalog_reason(game_dir)
    if reason:
        result['reason'] = reason
    else:
        result['skill_options'] = [{'id': key, 'name': value['name']} for key, value in
                                   sorted(CATALOG['skills'].items(), key=lambda item: item[1]['ordinal'])]
    outer = _array(doc, [WEAPONS])
    for index in range(outer['count'] if outer else 0):
        prefix = [WEAPONS, f'[{index}]']
        name = _child(doc.strings, prefix, '发射器ID')
        fields = []
        for f in doc.fields:
            if ((f.path[:-1] == prefix and label(f.path[-1]) == '等级') or
                (len(f.path) == 4 and f.path[:2] == prefix and label(f.path[2]) == '增益'
                 and label(f.path[-1]) in ('火力', '备弹', '射速', '移速', '稳定'))):
                fields.append({'label': label(f.path[-1]), 'field_id': f.id})
        skills, editable, skill_reason = [], False, reason
        try:
            _, values = _skill_state(doc, index)
            skills = [{'id': key, 'name': CATALOG['skills'][key]['name'] if not reason and key in CATALOG['skills']
                       else f'未识别技能 ({key})'} for key in values]
            if any(key not in CATALOG['skills'] for key in values):
                skill_reason = '存在未识别技能，仅查看，保留原始内容。'
            else:
                editable = not reason
        except SaveError as exc:
            skill_reason = str(exc)
        result['weapons'].append({'index': index, 'name': name['value'] if name else '未识别发射器',
                                  'fields': fields, 'skills': skills, 'skills_editable': editable,
                                  'skills_reason': skill_reason,
                                  'zoom': {'field_id': '', 'value': '', 'editable': False,
                                           'reason': '倍率由游戏资源决定，当前存档不支持修改；此功能已暂缓。'}})
    backpack = _array(doc, ['S_背包物资'])
    for index in range(backpack['count'] if backpack else 0):
        prefix = ['S_背包物资', f'[{index}]']
        item = _child(doc.strings, prefix, '表格索引')
        quantity = _numeric(doc, prefix, '数量')
        item_id = item['value'] if item else '?'
        metadata = CATALOG['items'].get(item_id, {}) if not reason else {}
        result['inventory'].append({'index': index, 'item_id': item_id,
                                    'name': metadata.get('name') or f'未识别物品 ({item_id})',
                                    'description': metadata.get('description', ''),
                                    'quantity': decimal_text(quantity.value) if quantity else '无法读取',
                                    'field_id': quantity.id if quantity else None})
    stage = _numeric(doc, [], '剧情任务索引')
    if stage:
        text = decimal_text(stage.value)
        quest = quest_text(text) if not reason else {}
        available = bool(quest and any(quest.values()))
        result['story_options'] = story_options() if not reason else []
        result['story'] = {'available': available, 'stage': text, **quest, 'field_id': stage.id,
                           'editable': not reason and stage.type == 'IntProperty',
                           'reason': '' if available else reason or '当前阶段没有已核实的任务文本，不能据此判定剧情已完成。'}
    for name in STATS:
        f = _numeric(doc, [], name)
        if f:
            result['statistics'].append({'label': name, 'value': decimal_text(f.value), 'field_id': f.id})
    result['statistics'].extend(collection_statistics(doc, home))
    return result


def patch_skills(data: bytes, changes: dict[str, list[str]], game_dir: str) -> tuple[bytes, list[dict]]:
    if not isinstance(changes, dict) or len(changes) > 64:
        raise SaveError('附加技能提交格式无效')
    if not changes:
        return data, []
    reason = catalog_reason(game_dir)
    if reason:
        raise SaveError(reason)
    applied = []
    output = data
    for key, requested in changes.items():
        if not isinstance(key, str) or not re.fullmatch(r'0|[1-9][0-9]{0,5}', key):
            raise SaveError('发射器编号无效')
        if not isinstance(requested, list):
            raise SaveError('附加技能必须是列表')
        if any(not isinstance(v, str) or v not in CATALOG['skills'] for v in requested):
            raise SaveError('包含未核实的附加技能枚举')
        doc = Document(output)
        path, original = _skill_state(doc, int(key))
        if any(v not in CATALOG['skills'] for v in original):
            raise SaveError('原技能列表超出已验证范围，仅允许查看')
        # Names are verified FString enum identifiers, not enum ordinal integers.
        records = []
        for value in requested:
            raw = value.encode('utf-16le') + b'\0\0'
            records.append(struct.pack('<i', -len(raw)//2) + raw)
        if original != requested:
            output = doc.replace_array(path, records)
            if _skill_state(Document(output), int(key))[1] != requested:
                raise SaveError('修改后附加技能校验失败')
            applied.append({'index': int(key), 'old': original, 'value': requested})
    return output, applied
