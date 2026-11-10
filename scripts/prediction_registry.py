"""Versioned prediction adapters and catalog selection; contains no business rows.

Adding an event adapter changes this registry/provider code, not the Actions DAG.
Collectors may import this module without loading NumPy or a simulator.
"""
from __future__ import annotations

from copy import deepcopy
import importlib

REGISTRY_VERSION = "xinrui-prediction-registry-v1"
_DATE = r"(?:(?P<year>20\d{2})年\s*(?P<month>1[0-2]|0?[1-9])月|(?P<edition>20\d{2}(?:0[1-9]|1[0-2])))"
PREDICTION_REGISTRY = (
    {
        "id": "monthly", "family": "monthly", "provider": "prediction_providers:monthly",
        "divisions": ["甲级", "乙级", "丙级", "无等级"],
        "parent_matchtype": 60, "child_matchtype": 6,
        "title_pattern": r"^(?=.*月赛).*?" + _DATE, "total_rounds": 9,
        "ranking": {"endpoint": "cpmatch/result", "id_parameter": "cmid", "dtype": 1},
        "supported_formats": ["vp_swiss"],
        "excluded_group_patterns": [r"无等级"],
        "group_rules": [
            {"id": "a", "name": "甲级组", "title_pattern": r"甲级", "boards": 12, "promote": 0, "stay": 600},
            {"id": "b-a", "name": "乙级A组", "title_pattern": r"乙级\s*[AaＡａ]", "boards": 12, "promote": 200, "stay": 500},
            {"id": "b-b", "name": "乙级B组", "title_pattern": r"乙级\s*[BbＢｂ]", "boards": 12, "promote": 200, "stay": 500},
            {"id": "c-a", "name": "丙级A组", "title_pattern": r"丙级\s*[AaＡａ]", "boards": 8, "promote": 300, "stay": 400},
            {"id": "c-b", "name": "丙级B组", "title_pattern": r"丙级\s*[BbＢｂ]", "boards": 8, "promote": 300, "stay": 400},
        ],
    },
    {
        "id": "team", "family": "team", "provider": "prediction_providers:team",
        "divisions": ["甲级", "乙级"],
        "parent_matchtype": 64, "child_matchtype": 53,
        "title_pattern": r"^(?!.*双人)(?=.*团体(?:等级)?赛).*?" + _DATE, "total_rounds": 9, "event_total_rounds": 14,
        "ranking": {"endpoint": "orgmatch/teamresultsearch", "id_parameter": "orgid",
                    "parameters": {"myattention": 1, "teamname": ""}, "entity_kind": "team"},
        "supported_formats": [], "group_rules": [],
        "swiss_prediction": {
            "provider": "prediction_team_provider:forecast", "format": "team_matchpoint_swiss",
            "team_size": 4, "total_rounds": 9, "qualify": 32,
            "group_rules": [
                {"id": "a", "name": "甲级组", "boards": 12, "promote": 0, "stay": 120},
                {"id": "b", "name": "乙级组", "boards": 8, "promote": 60, "stay": None},
            ],
        },
    },
    {
        "id": "pair", "family": "pair", "provider": "prediction_providers:pair",
        "divisions": ["甲级", "乙级"],
        "parent_matchtype": 64, "child_matchtype": 53,
        "title_pattern": r"^(?=.*双人团体(?:等级)?赛).*?" + _DATE, "total_rounds": 9, "event_total_rounds": 14,
        "ranking": {"endpoint": "orgmatch/teamresultsearch", "id_parameter": "orgid",
                    "parameters": {"myattention": 1, "teamname": ""}, "entity_kind": "team"},
        "supported_formats": [], "group_rules": [],
        "swiss_prediction": {
            "provider": "prediction_team_provider:forecast", "format": "team_matchpoint_swiss",
            "team_size": 2, "total_rounds": 9, "qualify": 32,
            "group_rules": [
                {"id": "a", "name": "甲级组", "boards": 12, "promote": 0, "stay": 120},
                {"id": "b", "name": "乙级组", "boards": 8, "promote": 60, "stay": None},
            ],
        },
    },
)


def get_prediction_registry():
    return deepcopy(PREDICTION_REGISTRY)


def get_prediction_spec(family, registry=None):
    entries = get_prediction_registry() if registry is None else registry
    matches = [entry for entry in entries if entry["family"] == family]
    if len(matches) != 1:
        raise ValueError("prediction_family_not_registered")
    return matches[0]


def load_provider(spec):
    module, name = spec["provider"].split(":", 1)
    return getattr(importlib.import_module(module), name)


def probability_applicability(family, group):
    """League boundaries include divisions excluded from the forecast roster."""
    key = group['id'].split('-')[0]
    division = {'a':'甲级','b':'乙级','c':'丙级','ungraded':'无等级'}.get(key)
    divisions = get_prediction_spec(family)['divisions']
    return {'promotion_applicable': division != divisions[0],
            'relegation_applicable': division != divisions[-1]}
