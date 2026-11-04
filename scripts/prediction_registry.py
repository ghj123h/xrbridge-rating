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
        "parent_matchtype": 60, "child_matchtype": 6,
        "title_pattern": r"^(?=.*月赛).*?" + _DATE, "total_rounds": 9,
        "ranking": {"endpoint": "cpmatch/result", "id_parameter": "cmid", "dtype": 1},
        "supported_formats": ["vp_swiss"],
        "group_rules": [
            {"id": "a", "name": "甲级组", "title_pattern": r"甲级", "boards": 12, "promote": 0, "stay": 600},
            {"id": "b-a", "name": "乙级A组", "title_pattern": r"乙级\s*[AaＡａ]", "boards": 12, "promote": 200, "stay": 500},
            {"id": "b-b", "name": "乙级B组", "title_pattern": r"乙级\s*[BbＢｂ]", "boards": 12, "promote": 200, "stay": 500},
            {"id": "c-a", "name": "丙级A组", "title_pattern": r"丙级\s*[AaＡａ]", "boards": 8, "promote": 300, "stay": 400},
            {"id": "c-b", "name": "丙级B组", "title_pattern": r"丙级\s*[BbＢｂ]", "boards": 8, "promote": 300, "stay": 400},
            {"id": "u-a", "name": "无等级A组", "title_pattern": r"无等级\s*[AaＡａ]", "boards": 8, "promote": 300, "relegate": 0},
            {"id": "u-b", "name": "无等级B组", "title_pattern": r"无等级\s*[BbＢｂ]", "boards": 8, "promote": 300, "relegate": 0},
        ],
    },
    {
        "id": "team", "family": "team", "provider": "prediction_providers:team",
        "parent_matchtype": 64, "child_matchtype": 53,
        "title_pattern": r"^(?!.*双人)(?=.*团体(?:等级)?赛).*?" + _DATE, "total_rounds": 9, "event_total_rounds": 14,
        "ranking": {"endpoint": "orgmatch/teamresultsearch", "id_parameter": "orgid",
                    "parameters": {"myattention": 1, "teamname": ""}, "entity_kind": "team"},
        "supported_formats": [], "group_rules": [],
    },
    {
        "id": "pair", "family": "pair", "provider": "prediction_providers:pair",
        "parent_matchtype": 64, "child_matchtype": 53,
        "title_pattern": r"^(?=.*双人团体(?:等级)?赛).*?" + _DATE, "total_rounds": 9, "event_total_rounds": 14,
        "ranking": {"endpoint": "orgmatch/teamresultsearch", "id_parameter": "orgid",
                    "parameters": {"myattention": 1, "teamname": ""}, "entity_kind": "team"},
        "supported_formats": [], "group_rules": [],
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
