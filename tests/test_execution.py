"""Exercise exported Code blocks and branches with recorded network boundaries."""

import copy
import json
from pathlib import Path

import pytest
import yaml
from test_runtime import valid_brief

ROOT = Path(__file__).resolve().parents[1]
RESEARCH = ROOT / "tests" / "fixtures" / "serp-observations.json"


def execute(
    *,
    market="us_en",
    site=False,
    search_error=False,
    empty=False,
    site_error=False,
    model_error=False,
    invalid_attempts=0,
    repair_error=False,
    limited=False,
    keyword="best serp api for seo",
):
    graph = yaml.safe_load(
        (ROOT / "thordata-seo-content-brief.yml").read_text(encoding="utf-8")
    )["workflow"]["graph"]
    nodes = {n["id"]: n["data"] for n in graph["nodes"]}
    runs = json.loads(RESEARCH.read_text(encoding="utf-8"))["runs"]
    primary = copy.deepcopy(next(r["result"] for r in runs if r["case"]["id"] == "R4"))
    if market == "gb_en":
        primary = json.loads(
            (ROOT / "tests/fixtures/google-web-gb.json").read_text(encoding="utf-8")
        )["result"]
    if empty:
        primary["data"]["organic"] = []
    elif limited:
        primary["data"]["organic"] = primary["data"]["organic"][:2]
    site_response = next(r["result"] for r in runs if r["case"]["id"] == "R6")
    values = {
        "start": {
            "keyword": keyword,
            "market": market,
            "audience": "Developers",
            "brand_context": "",
            "site_domain": "thordata.com" if site else "",
        }
    }
    visited, tool_count, model_count = [], 0, 0

    def get(selector):
        value = values[selector[0]]
        for part in selector[1:]:
            value = value[part]
        return value

    def check_type(value, kind):
        if kind.startswith("array["):
            assert isinstance(value, list)
            for item in value:
                check_type(item, kind[6:-1])
        else:
            expected = {
                "object": dict,
                "string": str,
                "boolean": bool,
                "number": (int, float),
            }[kind]
            assert isinstance(value, expected), (kind, type(value))

    current = "start"
    while True:
        assert len(visited) < 80, "Unexpected loop"
        visited.append(current)
        node = nodes[current]
        handle = "source"
        if node["type"] == "code":
            scope = {}
            exec(compile(node["code"], current, "exec"), scope)  # noqa: S102 - Execute the local generated DSL.
            result = scope["main"](
                **{v["variable"]: get(v["value_selector"]) for v in node["variables"]}
            )
            assert set(result) == set(node["outputs"]), current
            for key, definition in node["outputs"].items():
                check_type(result[key], definition["type"])
            values[current] = result
        elif node["type"] == "if-else":
            handle = "false"
            for case in node["cases"]:
                condition = case["conditions"][0]
                actual = get(condition["variable_selector"])
                accepted = (
                    bool(actual)
                    if condition["comparison_operator"] == "not empty"
                    else actual == condition["value"]
                )
                if accepted:
                    handle = case["case_id"]
                    break
        elif node["type"] == "tool":
            tool_count += 1
            is_site = current.startswith("site_")
            options = json.loads(values["prepare"]["search_options"])
            assert options == {"gl": market[:2], "hl": "en"}
            if search_error and not is_site or site_error and is_site:
                assert node["error_strategy"] == "default-value"
                values[current] = {
                    "result": json.loads(node["default_value"][0]["value"])
                }
            else:
                values[current] = {
                    "result": copy.deepcopy(site_response if is_site else primary)
                }
        elif node["type"] == "llm":
            model_count += 1
            if model_error or (repair_error and current == "repair_brief"):
                assert node["error_strategy"] == "default-value"
                values[current] = {"text": node["default_value"][0]["value"]}
            else:
                data = valid_brief(json.loads(values["evidence"]["output"]))
                if model_count <= invalid_attempts:
                    data["executive_summary"]["evidence_ids"] = ["P999"]
                values[current] = {"text": json.dumps(data)}
        elif node["type"] == "variable-aggregator":
            available = [get(s) for s in node["variables"] if s[0] in values]
            assert available, current
            values[current] = {"output": available[0]}
        elif node["type"] == "end":
            return {
                "output": {
                    v["variable"]: get(v["value_selector"]) for v in node["outputs"]
                },
                "nodes": visited,
                "searches": tool_count,
                "models": model_count,
                "values": values,
            }
        elif node["type"] != "start":
            raise AssertionError("Unsupported test node: " + node["type"])
        targets = [
            e["target"]
            for e in graph["edges"]
            if e["source"] == current and e["sourceHandle"] == handle
        ]
        assert len(targets) == 1, (current, handle, targets)
        current = targets[0]


@pytest.mark.parametrize("market", ["us_en", "gb_en"])
@pytest.mark.parametrize("site", [False, True])
def test_valid_paths(market, site):
    result = execute(market=market, site=site)
    assert result["output"]["status"] == "completed"
    assert result["searches"] == 1 + int(site)
    assert result["models"] == 1
    assert "# SEO Content Brief" in result["output"]["brief_markdown"]


def test_repair_does_not_select_the_invalid_first_response():
    result = execute(invalid_attempts=1)
    assert result["output"]["status"] == "completed"
    assert result["models"] == 2 and result["searches"] == 1
    assert "P999" not in json.dumps(result["output"]["brief_json"])


def test_failed_repair_stops():
    result = execute(invalid_attempts=2)
    assert result["output"]["status"] == "generation_failed"
    assert result["output"]["error_code"] == "brief_validation_failed"
    assert result["models"] == 2 and result["searches"] == 1


@pytest.mark.parametrize(
    "parameters,status",
    [
        ({"keyword": ""}, "invalid_input"),
        ({"empty": True}, "no_results"),
        ({"search_error": True}, "search_failed"),
        ({"model_error": True}, "generation_failed"),
    ],
)
def test_failure_paths(parameters, status):
    result = execute(**parameters)
    assert result["output"]["status"] == status
    assert result["output"]["brief_json"] == {}
    assert result["output"]["brief_markdown"] == ""
    if status != "generation_failed":
        assert result["models"] == 0


def test_optional_site_failure_preserves_main_brief():
    result = execute(site=True, site_error=True)
    assert result["output"]["status"] == "completed"
    assert result["searches"] == 2 and result["models"] == 1
    evidence = json.loads(result["output"]["evidence_json"])
    assert evidence["internal_links"]["status"] == "failed"
    assert evidence["internal_links"]["candidates"] == []
    assert result["output"]["warnings"]


def test_repair_service_failure_is_terminal():
    result = execute(invalid_attempts=1, repair_error=True)
    assert result["output"]["error_code"] == "llm_call_failed"
    assert result["models"] == 2 and result["searches"] == 1


def test_limited_sample_preserves_status():
    result = execute(limited=True)
    assert result["output"]["status"] == "limited_evidence"
    assert json.loads(result["output"]["evidence_json"])["organic_count"] == 2
