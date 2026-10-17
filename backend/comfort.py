"""Offline, game-version-bound furniture comfort calculation and lossless planning.

The shipped rules contain derived numbers/object identities, never game assets.
Unknown profiles/classes fail closed rather than being assigned a zero score.
"""
from collections import Counter
from decimal import Decimal, InvalidOperation
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import re
import struct

from .gvas import Document, Reader, SaveError

MAXIMUM = 2**24 - 1  # The game's player controller accumulates into float32.
MAX_ADDITIONS = 2000
RULES = json.loads(Path(__file__).with_name('comfort_rules.json').read_text(encoding='utf-8'))


@lru_cache(maxsize=16)
def _hash_file(path, size, modified):
    with open(path, 'rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def verify_game(game_dir: Path):
    for relative, expected in RULES['fingerprints'].items():
        path = game_dir / relative
        if not path.is_file():
            raise SaveError('游戏文件缺失，无法核对舒适度规则版本，请检查游戏路径')
        st = path.stat()
        if _hash_file(str(path), st.st_size, st.st_mtime_ns) != expected:
            raise SaveError('当前游戏版本尚未验证舒适度规则；货币和高级参数仍可使用')


def home_name(name):
    match = re.fullmatch(r'AT_(?:ArkPark|Home)(\d+)\.sav', name)
    if not match:
        raise SaveError('请选取带编号的角色或家园存档以自动计算舒适度')
    return 'AT_Home' + match[1] + '.sav', match[1]


def group_version(snapshot):
    hashes = {name: hashlib.sha256(data).hexdigest() for name, data in sorted(snapshot.items())}
    return hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest()


def _tags(doc, entry):
    r = Reader(doc.data[entry['start']:entry['end']])
    result = []
    while True:
        start = r.position
        name = r.string()
        if name == 'None':
            end = r.data[start:r.position]
            break
        r.tree()
        size = r.unpack('i')
        flags = r.unpack('B')
        if flags & ~0x1F:
            raise SaveError('家具属性标签未验证')
        if flags & 1:
            r.read(4)
        if flags & 2:
            r.read(16)
        r.read(size)
        result.append((name, start, r.position))
    if r.position != len(r.data):
        raise SaveError('家具记录尾部未知')
    return result, end


def furniture(doc):
    array = next((a for a in doc.arrays if a['path'] == ['S_建造系统']), None)
    if array is None:
        raise SaveError('家园建造数组无法可靠读取')
    strings = {tuple(s['path'][:2]): s for s in doc.strings
               if len(s['path']) == 3 and s['path'][0] == 'S_建造系统' and s['path'][2].startswith('类_')}
    result = []
    for i, entry in enumerate(array['entries']):
        prefix = ['S_建造系统', f'[{i}]']
        saved = strings.get(tuple(prefix))
        key = saved['value'].split('.')[0] if saved else ''
        if key not in RULES['catalog']:
            raise SaveError(f'未验证的家具类：{key or str(i)}；无法可靠计算总值')
        score = RULES['catalog'][key]['comfort']
        tags, terminator = _tags(doc, entry)
        basic = [(n, a, b) for n, a, b in tags if n.startswith(('类_', '位置_'))]
        fields = [f for f in doc.fields if f.path[:2] == prefix]
        transform = [f for f in fields if f.path[-2] in ('Rotation', 'Translation', 'Scale3D')]
        reliable = len(basic) == 2 and len(transform) == 10 and not any(
            o['path'][:2] == prefix and o['path'][2].startswith(('类_', '位置_')) for o in doc.opaque)
        template = b''.join(doc.data[entry['start']+a:entry['start']+b] for _, a, b in basic) + terminator if reliable else None
        # The game writes an empty inventory tag on the next save even for new
        # furniture. Accept it only when its parsed array is proven empty.
        extras = [n for n, _, _ in tags if not n.startswith(('类_', '位置_'))]
        empty_inventory = all(n.startswith('物资_') and any(
            a['path'] == prefix+[n] and a['count'] == 0 for a in doc.arrays) for n in extras)
        removable = reliable and empty_inventory
        result.append({'index': i, 'class': key, 'label': key.rsplit('/', 1)[-1].removeprefix('BP_'),
                       'score': score, 'entry': entry, 'template': template,
                       'removable': removable, 'prefix': prefix})
    return array, result


def inspect(snapshot, name, game_dir, verify=True):
    if verify:
        verify_game(Path(game_dir))
    home, owner = home_name(name)
    if home not in snapshot:
        raise SaveError(f'缺少对应家园存档 {home}')
    relocations = [Document(data) for filename, data in snapshot.items()
                   if filename.startswith(f'AT_Home_Relocation_ID{owner}_')]
    if not relocations:
        raise SaveError('未找到已验证地图的搬迁记录，当前家园地图无法自动确认')
    for relocation in relocations:
        top = {s['path'][0]: s['value'] for s in relocation.strings if len(s['path']) == 1}
        summons = [s for s in relocation.strings if s['path'][-1] == 'ActorClass' and 'BP_家具_载具召唤装置.' in s['value']]
        if top.get('MapName') != RULES['map_name'] or top.get('OwnerSaveId') != owner or len(summons) != 1:
            raise SaveError('家园地图或搬迁家具配置尚未验证，拒绝推测总分')
    doc = Document(snapshot[home])
    array, items = furniture(doc)
    placed = sum(x['score'] for x in items)
    mapped = sum(x['comfort'] for x in RULES['map_actors'])
    total = placed + mapped
    if not 0 <= total <= MAXIMUM:
        raise SaveError('当前总值已超过游戏 float32 的安全整数范围')
    counts = Counter((x['label'], x['score']) for x in items if x['score'])
    removable = sum(x['score'] for x in items if x['removable'])
    public = {'available': True, 'reason': '', 'value': str(total), 'minimum': '0',
              'maximum': str(MAXIMUM), 'reachable_minimum': str(total-removable),
              'home_file': home, 'map': RULES['map_name'], 'placed': str(placed), 'map_total': str(mapped),
              'breakdown': [{'label': label, 'count': count, 'each': str(score), 'total': str(count*score)}
                            for (label, score), count in sorted(counts.items())],
              'map_breakdown': RULES['map_actors']}
    return public, doc, array, items


def parse_target(text):
    if not isinstance(text, str) or len(text) > 2048:
        raise SaveError('请输入有效舒适度数值（最多 2048 个字符）')
    try:
        number = Decimal(text.strip())
    except InvalidOperation as exc:
        raise SaveError('无效舒适度数值') from exc
    if not number.is_finite():
        raise SaveError('不允许 NaN 或无穷值')
    bounded = min(max(number, Decimal(0)), Decimal(MAXIMUM))
    return int(bounded), bounded != number, bounded != bounded.to_integral_value()


@lru_cache(maxsize=32)
def _addition_table(denominations):
    largest = max(denominations)
    limit = largest**2 + 2*largest
    costs = [10**9] * (limit+1)
    prev = [0] * (limit+1)
    costs[0] = 0
    for score in range(1, limit+1):
        for coin in denominations:
            if score >= coin and costs[score-coin]+1 < costs[score]:
                costs[score] = costs[score-coin]+1
                prev[score] = coin
    # This recurrence proves periodic optimal costs for all larger amounts.
    stable = all(costs[i] == costs[i-largest]+1 or costs[i] == costs[i-largest] == 10**9
                 for i in range(limit-largest+1, limit+1))
    if not stable:
        raise SaveError('家具组合超出已验证规划范围')
    return costs, prev


def plan(snapshot, name, game_dir, target, verify=True):
    current, doc, array, items = inspect(snapshot, name, game_dir, verify)
    goal, clamped, fractional = parse_target(target)
    total = int(current['value'])
    templates = {}
    for item in sorted(items, key=lambda x: (0 if x['label'] == '家具_唱片机' else 1,
                                             0 if x['label'] == '拉面龙纸盒' else 1, x['index'])):
        if item['score'] and item['template']:
            templates.setdefault(item['score'], item)
    if not templates:
        raise SaveError('当前家园没有可以安全复制的已验证家具')
    coins = tuple(sorted(templates, reverse=True))
    costs, prev = _addition_table(coins)
    limit, largest = len(costs)-1, coins[0]

    def add_solution(amount):
        if amount < 0:
            return None
        extra = max(0, (amount-limit+largest-1)//largest)
        remainder = amount-extra*largest
        # Choose the greatest reachable score <= requested, then minimize edits.
        while remainder >= 0 and costs[remainder] == 10**9:
            remainder -= 1
        if remainder < 0:
            return None
        return extra+costs[remainder], remainder, extra, extra*largest+remainder

    removable = [x for x in items if x['removable'] and x['score']]
    removal_sum = sum(x['score'] for x in removable)
    if total-goal > 100000:
        raise SaveError('本次需要移除超过 100000 分；首版请分批降低目标')
    if goal < total-removal_sum:
        raise SaveError(f'保留地图物件和带库存家具后，最低可达到 {total-removal_sum}；该目标无法通过安全家具调整实现')
    # Bounded subset DP, grouped in binary batches. Nodes are immutable so
    # reconstruction cannot accidentally reuse a furniture record.
    removals = {0: (0, None)}
    by_score = {}
    for item in removable:
        by_score.setdefault(item['score'], []).append(item['index'])
    for score, indices in by_score.items():
        indices = indices[:2000]
        position, batch_size = 0, 1
        while position < len(indices):
            batch = tuple(indices[position:position+batch_size])
            value = score*len(batch)
            for removed, (count, node) in list(removals.items()):
                dest = removed+value
                if dest > 100000 or count+len(batch) > 2000:
                    continue
                if dest not in removals or count+len(batch) < removals[dest][0]:
                    removals[dest] = (count+len(batch), (node, batch))
            position += len(batch)
            batch_size *= 2
    best = None
    for removed, (count, node) in removals.items():
        added = add_solution(goal-total+removed)
        if added is None:
            continue
        add_count, remainder, extra, achieved = added
        actual = total-removed+achieved
        candidate = (-actual, count+add_count, count, removed, remainder, extra, node)
        if best is None or candidate[:3] < best[:3]:
            best = candidate
    if best is None:
        raise SaveError('在本次最多移除 2000 件 / 100000 分的限制内未找到不超过目标的安全组合，请分批调整')
    negative, mutations, remove_count, removed, remainder, extra, node = best
    delete = set()
    while node:
        node, indices = node
        delete.update(indices)
    additions = Counter({largest: extra}) if extra else Counter()
    while remainder:
        coin = prev[remainder]
        additions[coin] += 1
        remainder -= coin
    if sum(additions.values()) > MAX_ADDITIONS:
        raise SaveError(f'该目标需新增 {sum(additions.values())} 件家具；首版每次最多新增 {MAX_ADDITIONS} 件，请分批调整。类型安全上限仍为 {MAXIMUM}')
    if array['count']-len(delete)+sum(additions.values()) > 200000:
        raise SaveError('家具数组超出解析器结构限制')
    records = [doc.data[e['start']:e['end']] for i, e in enumerate(array['entries']) if i not in delete]
    changes = []
    for label, count in sorted(Counter(items[i]['label'] for i in delete).items()):
        changes.append({'action': 'remove', 'label': label, 'count': count,
                        'indices': sorted(i for i in delete if items[i]['label'] == label)})
    sequence = 0
    for score, count in sorted(additions.items(), reverse=True):
        source = templates[score]
        template_doc = Document(doc.replace_top_array('S_建造系统', [source['template']]))
        x = next(f for f in template_doc.fields if f.path[-2:] == ['Translation', 'X'])
        y = next(f for f in template_doc.fields if f.path[-2:] == ['Translation', 'Y'])
        start = next(a for a in template_doc.arrays if a['path'] == ['S_建造系统'])['entries'][0]['start']
        for _ in range(count):
            clone = bytearray(source['template'])
            struct.pack_into('<d', clone, x.offset-start, x.value+100*(sequence%20+1))
            struct.pack_into('<d', clone, y.offset-start, y.value+100*(sequence//20+1))
            records.append(bytes(clone))
            sequence += 1
        changes.append({'action': 'add', 'label': source['label'], 'count': count,
                        'each': str(score), 'source_index': source['index']})
    output = doc.replace_top_array('S_建造系统', records)
    checked = dict(snapshot)
    checked[current['home_file']] = output
    actual = inspect(checked, name, game_dir, verify=False)[0]['value']
    if actual != str(-negative):
        raise SaveError('规划后的总分校验失败')
    public = {'original': current['value'], 'requested': target, 'target': str(goal),
              'value': actual, 'clamped': clamped, 'rounded_down': fractional,
              'nearest': actual != str(goal), 'home_file': current['home_file'],
              'mutations': mutations, 'changes': changes,
              'placement': '新增家具放在源家具旁的 1 米网格中；原家具保留位置，可能需要进游戏调整摆放。'}
    return output, public
