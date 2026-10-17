"""Four game slots, mapped by verified save/load names rather than file order."""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP, localcontext

from . import gameplay
from .gvas import Document, SaveError, decimal_text


def number(doc: Document, path: list[str]) -> str | None:
    fields = [f for f in doc.fields if f.path == path]
    return decimal_text(fields[0].value) if len(fields) == 1 else None


def rounded_money(value: str | None) -> str | None:
    if value is None:
        return None
    amount = Decimal(value)
    if not amount.is_finite():
        return None
    with localcontext() as context:
        context.prec = max(32, amount.adjusted() + 3)
        return str(amount.quantize(Decimal(1), rounding=ROUND_HALF_UP))


def inspect(snapshot: dict[str, bytes], metadata: dict[str, dict], game_dir: str) -> dict:
    slots = []
    for index in range(4):
        main = f'AT_ArkPark{index}.sav'
        home = 'AT_Home.sav' if index == 0 else f'AT_Home{index}.sav'
        related = sorted(name for name in snapshot
                         if name.startswith(f'AT_Home_Relocation_ID{index}_'))
        # A double-dot filename is evidence of an anomaly, never a valid home.
        suspicious = home[:-4] + '..sav'
        if suspicious in snapshot:
            related.append(suspicious)
        present = [name for name in (main, home) if name in snapshot]
        missing = [name for name in (main, home) if name not in snapshot]
        issues = []
        summary = None
        for name in present:
            try:
                doc = Document(snapshot[name])
                if doc.save_class != gameplay.MAIN_CLASS:
                    raise SaveError('存档类型不符')
                if name == home:
                    if gameplay._array(doc, ['S_建造系统']) is None:
                        raise SaveError('缺少可解析的家园建造结构')
                    continue
                # Optional progress fields can be absent in otherwise readable saves.
                # Keep main-save anchors so a home file renamed as main is rejected.
                main_progress = (number(doc, ['剧情任务索引']) is not None
                                 or gameplay._array(doc, [gameplay.WEAPONS]) is not None
                                 or gameplay._array(doc, ['S_背包物资']) is not None)
                if number(doc, ['货币', 'Y']) is None or not main_progress:
                    raise SaveError('缺少可解析的主进度字段')
                view = gameplay.inspect(doc, game_dir)
                summary = {
                    'story': view['story'],
                    'money': rounded_money(number(doc, ['货币', 'Y'])),
                    'weapons': [{'index': i, 'name': next((w['name'] for w in view['weapons']
                                                          if w['index'] == i), '未记录')}
                                for i in range(2)],
                    'world_day': number(doc, ['世界岛日期']),
                    'world_weekday': number(doc, ['世界岛星期']),
                    'revenue': number(doc, ['拉面龙总营业额度']),
                    'cumulative_days': None,
                    'days_note': '未找到独立的游戏累计天数字段；世界岛日期按存档原值显示，不作累计天数推算。',
                }
            except SaveError as exc:
                issues.append(f'{name}：{exc}')
        if suspicious in snapshot and home not in snapshot:
            issues.append(f'{suspicious} 命名异常，不能代替 {home}')
        status = 'empty' if not present and not related else 'missing' if missing or issues else 'ready'
        slots.append({'id': index + 1, 'index': index, 'main_file': main, 'home_file': home,
                      'status': status, 'missing_files': missing if status != 'empty' else [],
                      'issues': issues, 'related_files': related, 'summary': summary,
                      'size': metadata.get(main, {}).get('size'),
                      'modified_at': metadata.get(main, {}).get('modified_at')})
    preferred = None
    if 'AT_GameSettings.sav' in snapshot:
        try:
            raw = number(Document(snapshot['AT_GameSettings.sav']), ['游戏默认存档'])
            preferred = int(raw) + 1 if raw is not None else None
        except (SaveError, ValueError, OverflowError):
            pass
    ready = [slot for slot in slots if slot['status'] == 'ready']
    fallback = ready or [slot for slot in slots if slot['status'] == 'missing']
    selected = next((slot['id'] for slot in ready if slot['id'] == preferred), None)
    if selected is None:
        selected = max(fallback, key=lambda slot: slot['modified_at'] or '')['id'] if fallback else 1
    return {'slots': slots, 'selected': selected}
