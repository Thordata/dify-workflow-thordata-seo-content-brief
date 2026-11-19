"""Verify the distributable graph and execute its actual embedded code."""

import importlib.util
import json
import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
DSL = ROOT / "thordata-seo-content-brief.yml"


@pytest.fixture
def workflow():
    if not DSL.exists():
        pytest.skip("The workflow does not exist yet")
    return yaml.safe_load(DSL.read_text(encoding="utf-8"))


def test_distributable_exists():
    assert DSL.exists(), "The importable workflow has not been built"


def test_existing_app_code_patches_match_the_export(workflow):
    nodes = {
        node["id"]: node["data"] for node in workflow["workflow"]["graph"]["nodes"]
    }
    for node_id, filename in (
        ("finish_brief", "validate-and-format.py"),
        ("finish_repair", "finalize-repaired-brief.py"),
    ):
        patch = ROOT / "patches" / filename
        assert patch.exists(), (
            "A paste-ready patch is required for existing Dify applications"
        )
        assert patch.read_text(encoding="utf-8") == nodes[node_id]["code"]


def test_readable_canvas(workflow):
    nodes = workflow["workflow"]["graph"]["nodes"]
    assert len(nodes) <= 18
    assert len([n for n in nodes if n["data"]["type"] == "end"]) == 1
    by_id = {n["id"]: n for n in nodes}
    branches = {
        "site_search",
        "finish_site",
        "site_guard",
        "repair_brief",
        "finish_repair",
    }
    assert all(n["position"]["y"] == 0 for n in nodes if n["id"] not in branches)
    assert all(by_id[key]["position"]["y"] == 400 for key in branches)
    assert all(n["data"]["desc"].strip() for n in nodes)
    assert all(n["position"] == n["positionAbsolute"] for n in nodes)
    for edge in workflow["workflow"]["graph"]["edges"]:
        assert (
            by_id[edge["source"]]["position"]["x"]
            < by_id[edge["target"]]["position"]["x"]
        )
    assert all(
        len(n["data"]["tool_configurations"]) <= 3
        for n in nodes
        if n["data"]["type"] == "tool"
    )


def test_english_only_distribution(workflow):
    for path in ROOT.rglob("*"):
        if path.suffix not in {".py", ".md", ".json", ".yml", ".txt"}:
            continue
        assert not re.search(r"[\u3400-\u9fff]", path.read_text(encoding="utf-8")), path
    assert workflow["app"]["mode"] == "workflow"
    assert workflow["workflow"]["environment_variables"] == []


def test_graph_has_valid_references_and_is_acyclic(workflow):
    graph = workflow["workflow"]["graph"]
    nodes = {node["id"]: node for node in graph["nodes"]}
    assert len(nodes) == len(graph["nodes"])
    incoming = {key: 0 for key in nodes}
    outgoing = {key: [] for key in nodes}
    for edge in graph["edges"]:
        assert edge["source"] in nodes and edge["target"] in nodes
        incoming[edge["target"]] += 1
        outgoing[edge["source"]].append(edge["target"])
    ready = [key for key, count in incoming.items() if count == 0]
    assert ready == ["start"]
    visited = []
    while ready:
        key = ready.pop()
        visited.append(key)
        for target in outgoing[key]:
            incoming[target] -= 1
            if not incoming[target]:
                ready.append(target)
    assert len(visited) == len(nodes)
    assert any(n["data"]["type"] == "end" for n in nodes.values())
    for node in nodes.values():
        data = node["data"]
        for var in data.get("variables", []):
            if isinstance(var, dict) and "value_selector" in var:
                assert var["value_selector"][0] in nodes
        for match in re.finditer(r"\{\{#([^.]+)\.", json.dumps(data)):
            assert match.group(1) in nodes


def test_native_search_nodes_and_bounded_requests(workflow):
    graph = workflow["workflow"]["graph"]
    nodes = {n["id"]: n["data"] for n in graph["nodes"]}
    tools = {key: data for key, data in nodes.items() if data["type"] == "tool"}
    assert set(tools) == {"search", "site_search"}
    for data in tools.values():
        assert data["tool_name"] == "google_web"
        assert data["provider_id"] == "thordata/thordata_serp/thordata_serp"
        config = data["tool_configurations"]
        assert config["extra_parameters_json"] == {
            "type": "mixed",
            "value": "{{#prepare.search_options#}}",
        }
        assert config["num"]["value"] == 10
        assert "location" not in config
        assert not data["retry_config"]["retry_enabled"]
        assert data["error_strategy"] == "default-value"
    assert not any(data["type"] == "http-request" for data in nodes.values())
    assert len([d for d in nodes.values() if d["type"] == "llm"]) == 2
    outgoing = {key: [] for key in nodes}
    for e in graph["edges"]:
        outgoing[e["source"]].append(e["target"])

    def walk(key, searches=0, models=0):
        searches += nodes[key]["type"] == "tool"
        models += nodes[key]["type"] == "llm"
        assert searches <= 2 and models <= 2
        for target in outgoing[key]:
            walk(target, searches, models)

    walk("start")


def test_code_nodes_compile_and_handle_invalid_input(workflow):
    nodes = {n["id"]: n["data"] for n in workflow["workflow"]["graph"]["nodes"]}
    for key, data in nodes.items():
        if data["type"] == "code":
            scope = {}
            exec(compile(data["code"], key, "exec"), scope)  # noqa: S102 - Execute the local generated DSL.
            assert callable(scope["main"])
    scope = {}
    exec(nodes["prepare"]["code"], scope)  # noqa: S102 - Execute the local generated DSL.
    result = scope["main"]("", "us_en", "", "", "")
    assert result["valid"] is False
    assert json.loads(result["result_json"])["error_code"] == "invalid_input"


def test_generated_file_is_current(workflow):
    spec = importlib.util.spec_from_file_location(
        "workflow_builder", ROOT / "build_workflow.py"
    )
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    assert builder.build() == workflow
