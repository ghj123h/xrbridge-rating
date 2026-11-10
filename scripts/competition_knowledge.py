"""Resolve the cloud's shared announcement facts in the current process only."""
import json
import re


class KnowledgeError(ValueError):
    pass


def event_group(event):
    raw = event.get('raw') or {}
    raw = json.loads(raw) if isinstance(raw, str) else raw
    group = (event.get('group') or raw.get('group') or {}).get('groupname')
    if isinstance(group, str) and group.strip():
        return group.strip()
    match = re.search(r'[-—](甲级(?:[AB])?组|乙级(?:[AB])?组|丙级(?:[AB])?组|无等级(?:[AB])?组)$', event.get('title') or '')
    return match[1] if match else ''


def _scope_group(name):
    match = re.fullmatch(r'(甲级|乙级|丙级|无等级)(?:[AB])?组?', name)
    return match[1] + '组' if match else name


def knowledge_stages(knowledge, event, config=None):
    if knowledge is None:
        raise KnowledgeError('not_a_competition_announcement')
    group = event_group(event)
    parent = _scope_group(group)
    applicable = [r for r in knowledge['rules'] if r['groups'] == 'all' or any(
        g == group or re.fullmatch(r'(甲级|乙级|丙级|无等级)组?', g) and _scope_group(g) == parent for g in r['groups'])]
    if not applicable:
        raise KnowledgeError('unresolved_knowledge_group')
    match = re.search(r'共\s*(\d+)\s*人参赛', str(event.get('remark') or ''))
    count = int(match[1]) if match and int(match[1]) > 0 else None
    conditional = [r for r in applicable if r.get('entrants')]
    if conditional and count is None:
        raise KnowledgeError('unresolved_knowledge_condition')
    selected = [r for r in applicable if not r.get('entrants') or (
        ('eq' not in r['entrants'] or count == r['entrants']['eq']) and
        ('lt' not in r['entrants'] or count < r['entrants']['lt']))]
    if not selected or conditional and len([r for r in selected if r.get('entrants')]) != 1:
        raise KnowledgeError('unresolved_knowledge_condition')
    stages = [s for r in selected for s in r['stages'] if not s.get('separate')]
    named = re.search(r'(预赛|决赛|淘汰赛)(?=[|｜]|$)', event.get('title') or '')
    root = next((s for s in stages if not s.get('after')), None)
    if named and root and root['name'] == '预赛':
        scoped = [root] if named[1] == '预赛' else [s for s in stages if s['format'] == 'knockout' and s.get('after') == root['name']]
        if len(scoped) == 1:
            stages = [{k: v for k, v in s.items() if k != 'after'} for s in scoped]
    if not stages:
        raise KnowledgeError('awaiting_board_rules')
    if len({s['name'] for s in stages}) != len(stages):
        raise KnowledgeError('ambiguous_phase_layout')
    return stages


def execution_rules(knowledge, event, config=None):
    stages = knowledge_stages(knowledge, event, config)
    starts, visiting = {}, set()

    def start(stage):
        name = stage['name']
        if name in starts:
            return starts[name]
        if name in visiting:
            raise KnowledgeError('cyclic_knowledge_stages')
        visiting.add(name)
        previous = next((s for s in stages if s['name'] == stage.get('after')), None)
        if stage.get('after') and previous is None:
            raise KnowledgeError('unknown_preceding_stage')
        value = 1
        if previous:
            previous_start = start(previous)
            value = None if previous_start is None or previous['rounds'] is None else previous_start + previous['rounds']
        visiting.remove(name)
        starts[name] = value
        return value

    phases = [{**s, 'phase': s['name'], 'global_from': start(s)} for s in stages]
    roots = [s for s in phases if not s.get('after')]
    if len(roots) != 1:
        raise KnowledgeError('ambiguous_phase_layout')
    root = roots[0]
    base = {'phase': root['name'], 'format': root['format'], 'boards_per_round': root['boards_per_round'],
            'event_group': '全部', 'manual_review': 0}
    overrides = [{'round_from': s['global_from'], 'round_to': s['global_from'] + s['rounds'] - 1,
                  'boards_per_round': s['boards_per_round'], 'isout': int(s['format'] == 'knockout')}
                 for s in phases if s.get('after') and s['global_from'] is not None and s['rounds'] is not None]
    return {'base': base, 'phases': phases, 'round_overrides': overrides}
