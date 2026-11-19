"""Optional offline checks using an installed Dify 1.17.1 Python environment.

Run this script with Dify's Python interpreter. It needs graphon and PyYAML,
but no server, database, sandbox, network requests, or credentials.
"""

import argparse
import ast
import hashlib
import json
import sys
from datetime import UTC, datetime
from enum import StrEnum, auto
from pathlib import Path
from types import SimpleNamespace

import yaml
from graphon.entities.graph_config import NodeConfigDictAdapter
from graphon.enums import WorkflowNodeExecutionStatus
from graphon.graph_engine.error_handler import ErrorHandler
from graphon.graph_events.node import NodeRunFailedEvent
from graphon.node_events.base import NodeRunResult
from graphon.nodes.code.code_node import CodeNode
from graphon.nodes.code.entities import CodeNodeData
from graphon.nodes.code.limits import CodeNodeLimits
from graphon.nodes.end.entities import EndNodeData
from graphon.nodes.if_else.entities import IfElseNodeData
from graphon.nodes.llm.entities import LLMNodeData
from graphon.nodes.start.entities import StartNodeData
from graphon.nodes.tool.entities import ToolNodeData
from graphon.nodes.variable_aggregator.entities import VariableAggregatorNodeData
from graphon.nodes.variable_aggregator.variable_aggregator_node import (
    VariableAggregatorNode,
)
from graphon.runtime import VariablePool
from graphon.variables.template_resolution import convert_template
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parents[1]


def load_definitions(path, names, namespace, parent=None):
    """Load exact source definitions without importing application configuration."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    if parent:
        tree = next(
            n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == parent
        )
    selected = [
        n
        for n in tree.body
        if isinstance(n, (ast.ClassDef, ast.FunctionDef)) and n.name in names
    ]
    assert {node.name for node in selected} == set(names), (path, names)
    if parent:
        selected = [
            ast.ClassDef(
                name=parent, bases=[], keywords=[], body=selected, decorator_list=[]
            )
        ]
    module = ast.fix_missing_locations(ast.Module(body=selected, type_ignores=[]))
    source = "from __future__ import annotations\n" + ast.unparse(module)
    exec(compile(source, str(path), "exec"), namespace)  # noqa: S102 - Checked local source only.


def configuration_resolver(api):
    namespace = {
        "StrEnum": StrEnum,
        "auto": auto,
        "VariablePool": VariablePool,
        "convert_template": convert_template,
    }
    load_definitions(
        api / "core/entities/parameter_entities.py", {"CommonParameterType"}, namespace
    )
    load_definitions(
        api / "core/plugin/entities/parameters.py",
        {"PluginParameterType", "MCPServerParameterType"},
        namespace,
    )
    load_definitions(
        api / "core/tools/entities/tool_entities.py",
        {"ToolParameterType", "ToolParameterForm"},
        namespace,
        "ToolParameter",
    )
    load_definitions(
        api / "core/tools/tool_manager.py",
        {
            "_convert_tool_parameters_type",
            "_extract_runtime_selector_value",
            "_is_selector_value",
        },
        namespace,
        "ToolManager",
    )
    return namespace["ToolManager"]._convert_tool_parameters_type


def check_dependencies(api, dependencies):
    namespace = {"BaseModel": BaseModel, "StrEnum": StrEnum, "auto": auto}
    load_definitions(
        api / "core/plugin/entities/plugin.py",
        {"PluginInstallationSource", "PluginDependencyType", "PluginDependency"},
        namespace,
    )
    model = namespace["PluginDependency"]
    model.model_rebuild(_types_namespace=namespace)
    for raw in dependencies:
        parsed = model.model_validate(raw)
        assert parsed.type == "marketplace"
        assert isinstance(parsed.value, model.Marketplace)


def check_default_output(ident, data):
    expected = (
        {"result": {}}
        if data.type == "tool"
        else {"text": "__DIFY_MODEL_CALL_FAILED__"}
    )
    assert data.default_value_dict == expected
    graph = SimpleNamespace(
        nodes={ident: SimpleNamespace(default_value_dict=data.default_value_dict)}
    )
    handler = ErrorHandler(graph, None)
    failure = NodeRunFailedEvent(
        id="offline-run",
        node_id=ident,
        node_type=data.type,
        start_at=datetime.now(UTC),
        error="Offline fixture failure",
        node_run_result=NodeRunResult(
            status=WorkflowNodeExecutionStatus.FAILED,
            error="Offline fixture failure",
            error_type="FixtureError",
        ),
    )
    result = handler._handle_default_value(failure).node_run_result
    assert result.status == WorkflowNodeExecutionStatus.EXCEPTION
    assert result.edge_source_handle == "source"
    assert all(result.outputs[key] == value for key, value in expected.items())
    return result.outputs


def aggregate(data, available):
    pool = VariablePool()
    for selector, value in available:
        pool.add(selector, value)
    node = VariableAggregatorNode.__new__(VariableAggregatorNode)
    node._node_data = VariableAggregatorNodeData.model_validate(data)
    node.graph_runtime_state = SimpleNamespace(variable_pool=pool)
    return node._run().outputs["output"].value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dify-api", type=Path, default=Path(sys.executable).absolute().parents[2]
    )
    parser.add_argument(
        "--plugin-path",
        type=Path,
        default=ROOT.parent / "thordata-serp-plugin",
        help="Thordata Dify plugin checkout that provides the google_web tool descriptor.",
    )
    args = parser.parse_args()
    dsl = ROOT / "thordata-seo-content-brief.yml"
    document = yaml.safe_load(dsl.read_text(encoding="utf-8"))
    graph = document["workflow"]["graph"]
    nodes = {node["id"]: node for node in graph["nodes"]}
    schemas = {
        "start": StartNodeData,
        "code": CodeNodeData,
        "if-else": IfElseNodeData,
        "variable-aggregator": VariableAggregatorNodeData,
        "tool": ToolNodeData,
        "llm": LLMNodeData,
        "end": EndNodeData,
    }
    defaults = {}
    for node in nodes.values():
        NodeConfigDictAdapter.validate_python(node)
        data = schemas[node["data"]["type"]].model_validate(node["data"])
        if data.type in {"tool", "llm"}:
            assert data.error_strategy == "default-value"
            assert {
                e["sourceHandle"] for e in graph["edges"] if e["source"] == node["id"]
            } == {"source"}
            defaults[node["id"]] = check_default_output(node["id"], data)
    check_dependencies(args.dify_api, document["dependencies"])
    tool_path = args.plugin_path / "tools/generated/google_web.yaml"
    configuration_scenarios = 0
    if tool_path.exists():
        resolver = configuration_resolver(args.dify_api)
        definition = yaml.safe_load(tool_path.read_text(encoding="utf-8"))
        parameters = [
            SimpleNamespace(**parameter) for parameter in definition["parameters"]
        ]
    else:
        print(
            "Skipping tool configuration checks: "
            f"{tool_path} was not found (pass --plugin-path).",
            file=sys.stderr,
        )
    validator = CodeNode.__new__(CodeNode)
    validator._limits = CodeNodeLimits(
        max_string_length=400000,
        max_number=9223372036854775807,
        min_number=-9223372036854775807,
        max_precision=20,
        max_depth=5,
        max_number_array_length=1000,
        max_string_array_length=30,
        max_object_array_length=30,
    )
    executed = set()

    def run(ident, **arguments):
        node = nodes[ident]
        data = CodeNodeData.model_validate(node["data"])
        namespace = {}
        exec(compile(data.code, ident, "exec"), namespace)  # noqa: S102 - Local DSL only.
        result = namespace["main"](**arguments)
        validator.transform_result(result, data.outputs)
        executed.add(ident)
        return result

    helper_source = ast.parse(
        (ROOT / "tests" / "test_runtime.py").read_text(encoding="utf-8")
    )
    functions = [
        node
        for node in helper_source.body
        if isinstance(node, ast.FunctionDef)
        and node.name in {"claim", "brief", "valid_brief"}
    ]
    helpers = {}
    exec(  # noqa: S102 - Select local fixture helpers, without pytest imports.
        compile(
            ast.Module(body=functions, type_ignores=[]), "test-fixture-helpers", "exec"
        ),
        helpers,
    )
    research = ROOT / "tests" / "fixtures" / "serp-observations.json"
    runs = json.loads(research.read_text(encoding="utf-8"))["runs"]
    main_result = next(r["result"] for r in runs if r["case"]["id"] == "R4")
    site_result = next(r["result"] for r in runs if r["case"]["id"] == "R6")
    if tool_path.exists():
        for market, gl in (("us_en", "us"), ("gb_en", "gb")):
            prepared = run(
                "prepare",
                keyword="best serp api for seo",
                market=market,
                audience="Developers",
                brand_context="",
                site_domain="thordata.com",
            )
            pool = VariablePool()
            pool.add(["prepare", "search_options"], prepared["search_options"])
            for ident in ("search", "site_search"):
                configuration = resolver(
                    parameters, pool, nodes[ident]["data"]["tool_configurations"]
                )
                assert json.loads(configuration["extra_parameters_json"]) == {
                    "gl": gl,
                    "hl": "en",
                }
                assert configuration["num"] == 10 and configuration["start"] == 0
        configuration_scenarios = 4
    else:
        prepared = run(
            "prepare",
            keyword="best serp api for seo",
            market="gb_en",
            audience="Developers",
            brand_context="",
            site_domain="thordata.com",
        )
    config = prepared["config"]
    initial = run("prepare_evidence", result=main_result, config=config)
    assert initial["action"] == "site"
    site = run(
        "finish_site",
        result=site_result,
        config=config,
        evidence_json=initial["evidence_json"],
    )
    assert site["ready"]
    evidence_json = aggregate(
        nodes["evidence"]["data"],
        [
            (["prepare_evidence", "evidence_json"], initial["evidence_json"]),
            (["finish_site", "evidence_json"], site["evidence_json"]),
        ],
    )
    assert evidence_json == site["evidence_json"]
    brief = helpers["valid_brief"](json.loads(evidence_json))
    finished = run("finish_brief", text=json.dumps(brief), evidence_json=evidence_json)
    assert not finished["needs_repair"]
    assert run("output", result_json=finished["result_json"])["status"] == "completed"
    invalid = run(
        "finish_brief", text="invalid model JSON", evidence_json=evidence_json
    )
    assert invalid["needs_repair"] and invalid["result_json"] == ""
    repaired = run("finish_repair", text=json.dumps(brief), evidence_json=evidence_json)
    final = aggregate(
        nodes["result"]["data"],
        [
            (["finish_brief", "result_json"], invalid["result_json"]),
            (["finish_repair", "result_json"], repaired["result_json"]),
        ],
    )
    assert run("output", result_json=final)["status"] == "completed"
    invalid_input = run("prepare", keyword="", market="us_en")
    assert (
        run("output", result_json=invalid_input["result_json"])["status"]
        == "invalid_input"
    )
    failed_search = run(
        "prepare_evidence", result=defaults["search"]["result"], config=config
    )
    assert failed_search["action"] == "finish"
    assert (
        run("output", result_json=failed_search["result_json"])["status"]
        == "search_failed"
    )
    assert run(
        "finish_site",
        result=defaults["site_search"]["result"],
        config=config,
        evidence_json=initial["evidence_json"],
    )["ready"]
    for ident, model in (
        ("finish_brief", "generate_brief"),
        ("finish_repair", "repair_brief"),
    ):
        failed_model = run(
            ident, text=defaults[model]["text"], evidence_json=evidence_json
        )
        assert not failed_model["needs_repair"]
        assert (
            run("output", result_json=failed_model["result_json"])["error_code"]
            == "llm_call_failed"
        )
    scenarios = 0
    for ident in ("evidence", "result"):
        selectors = nodes[ident]["data"]["variables"]
        for first in range(len(selectors)):
            available = [
                (selector, "value-" + str(index))
                for index, selector in enumerate(selectors)
                if index >= first
            ]
            assert aggregate(nodes[ident]["data"], available) == "value-" + str(first)
            scenarios += 1
    assert executed == {key for key, n in nodes.items() if n["data"]["type"] == "code"}
    print(
        json.dumps(
            {
                "nodes_validated": len(nodes),
                "code_outputs_validated": len(executed),
                "aggregator_scenarios": scenarios + 2,
                "default_outputs_validated": len(defaults),
                "configuration_scenarios": configuration_scenarios,
                "marketplace_dependencies": len(document["dependencies"]),
                "yaml_sha256": hashlib.sha256(dsl.read_bytes()).hexdigest(),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
