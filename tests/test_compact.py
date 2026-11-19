"""Offline behavior checks for the composed compact workflow nodes."""

import importlib.util
import json

import pytest
from test_runtime import RUNTIME, response, valid_brief


@pytest.fixture
def runtime():
    spec = importlib.util.spec_from_file_location("compact_runtime", RUNTIME)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def baseline(runtime, site_domain=""):
    prepared = runtime.prepare_run("web scraping api", "us_en", site_domain=site_domain)
    search = runtime.prepare_search(response(), prepared["config"])
    return prepared, search


def test_competitor_mismatch_repairs_then_omits_only_unattributable_observation(
    runtime,
):
    _, search = baseline(runtime)
    evidence = json.loads(search["evidence_json"])
    candidate = valid_brief(evidence)
    candidate["competitor_observations"][0]["observations"].append(
        {
            "text": "A claim attributed to a different page.",
            "basis": "serp_observation",
            "evidence_ids": ["P2"],
        }
    )
    raw = json.dumps(candidate)
    first = runtime.finish_generation(raw, search["evidence_json"])
    assert first["needs_repair"]
    assert "competitor_observations[0].observations[1]" in first["errors"]
    assert "P1" in first["errors"]
    final_node = runtime.finish_generation(raw, search["evidence_json"], False)
    final = runtime.unpack_result(final_node["result_json"])
    assert final["status"] == "completed"
    assert final["error_code"] == ""
    assert len(final["brief_json"]["competitor_observations"][0]["observations"]) == 1
    assert "A claim attributed to a different page." not in final["brief_markdown"]
    assert final["brief_json"]["executive_summary"] == candidate["executive_summary"]
    assert any(
        "omitted" in warning and "P1" in warning for warning in final["warnings"]
    )
    assert "omitted" in final["brief_markdown"]
    assert final["evidence_json"] == search["evidence_json"]


def test_all_competitor_mismatches_leave_sources_and_explicit_disclosure(runtime):
    _, search = baseline(runtime)
    candidate = valid_brief(json.loads(search["evidence_json"]))
    candidate["competitor_observations"][0]["observations"][0]["evidence_ids"] = ["P2"]
    final = runtime.unpack_result(
        runtime.finish_generation(
            json.dumps(candidate), search["evidence_json"], False
        )["result_json"]
    )
    assert final["status"] == "completed"
    assert final["brief_json"]["competitor_observations"] == []
    assert (
        "No individually validated competitor observations" in final["brief_markdown"]
    )
    assert "P1" in final["brief_markdown"]


@pytest.mark.parametrize("ids", [["P999"], [], ["P2", "P2"]])
def test_mismatch_fallback_still_rejects_invalid_references(runtime, ids):
    _, search = baseline(runtime)
    candidate = valid_brief(json.loads(search["evidence_json"]))
    candidate["competitor_observations"][0]["observations"][0]["evidence_ids"] = ids
    final = runtime.unpack_result(
        runtime.finish_generation(
            json.dumps(candidate), search["evidence_json"], False
        )["result_json"]
    )
    assert final["status"] == "generation_failed"


def test_repaired_competitor_citation_is_preserved_without_omission(runtime):
    _, search = baseline(runtime)
    candidate = valid_brief(json.loads(search["evidence_json"]))
    candidate["competitor_observations"][0]["observations"][0]["evidence_ids"] = [
        "P1",
        "P2",
    ]
    final = runtime.unpack_result(
        runtime.finish_generation(
            json.dumps(candidate), search["evidence_json"], False
        )["result_json"]
    )
    assert final["status"] == "completed"
    assert final["brief_json"] == candidate
    assert not any("omitted" in w for w in final["warnings"])


@pytest.mark.parametrize("market,country", [("us_en", "us"), ("gb_en", "gb")])
def test_prepare_run_normalizes_optional_none_and_search_options(
    runtime, market, country
):
    result = runtime.prepare_run("  api  ", market, None, None, None)
    assert set(result) == {"valid", "config", "search_options", "result_json"}
    assert result["valid"] and result["result_json"] == ""
    assert result["config"]["keyword"] == "api"
    assert result["config"]["gl"] == country
    assert result["config"]["audience"] == ""
    assert json.loads(result["search_options"]) == {"gl": country, "hl": "en"}


def test_prepare_run_invalid_input_is_final_safe_result(runtime):
    result = runtime.prepare_run("", "us_en")
    assert not result["valid"] and result["config"] == {}
    assert result["search_options"] == "{}"
    final = runtime.unpack_result(result["result_json"])
    assert final["status"] == "invalid_input" and final["error_code"] == "invalid_input"
    assert final["brief_json"] == {} and final["evidence_json"] == "{}"


@pytest.mark.parametrize("site,action", [("", "generate"), ("example.org", "site")])
def test_prepare_search_routes_with_bounded_evidence(runtime, site, action):
    prepared, search = baseline(runtime, site)
    assert search["action"] == action and search["result_json"] == ""
    evidence = json.loads(search["evidence_json"])
    assert len(search["evidence_json"]) <= 24000
    assert evidence["internal_links"]["status"] == "skipped"
    assert evidence["organic_count"] == 3
    assert evidence["query"] == prepared["config"]["keyword"]


@pytest.mark.parametrize(
    "raw,status,code",
    [
        ({}, "search_failed", "serp_tool_error"),
        (response([]), "no_results", "no_results"),
        (
            {"ok": True, "status": 200, "engine": "google_web", "data": {}},
            "search_failed",
            "invalid_serp_response",
        ),
    ],
)
def test_prepare_search_failure_returns_final_result(runtime, raw, status, code):
    config = runtime.prepare_run("api", "us_en")["config"]
    result = runtime.prepare_search(raw, config)
    assert result["action"] == "finish"
    final = runtime.unpack_result(result["result_json"])
    assert final["status"] == status and final["error_code"] == code


def test_prepare_search_and_site_keep_budget_and_observation_time(runtime):
    config = runtime.prepare_run("api", "us_en", site_domain="example.org")["config"]
    rows = [
        {
            "title": "t" * 300,
            "link": "https://example.org/" + str(i) + "a" * 3500,
            "description": "s" * 1200,
        }
        for i in range(10)
    ]
    search = runtime.prepare_search(response(rows), config)
    before = json.loads(search["evidence_json"])
    assert len(search["evidence_json"]) <= 24000
    assert before["organic_count"] < 10
    finished = runtime.finish_site(response(rows), config, search["evidence_json"])
    after = json.loads(finished["evidence_json"])
    assert finished["ready"] and finished["result_json"] == ""
    assert len(finished["evidence_json"]) <= 24000
    assert after["observed_at"] == before["observed_at"]
    assert after["organic_count"] >= 1


def test_optional_site_failure_preserves_ready_main_evidence(runtime):
    prepared, search = baseline(runtime, "example.org")
    result = runtime.finish_site({}, prepared["config"], search["evidence_json"])
    evidence = json.loads(result["evidence_json"])
    assert result["ready"] and result["result_json"] == ""
    assert evidence["internal_links"]["status"] == "failed"
    assert evidence["organic_count"] == 3 and evidence["warnings"]
    assert evidence["observed_at"] == json.loads(search["evidence_json"])["observed_at"]


def test_finish_generation_valid_original_and_repair_render_safely(runtime):
    _, search = baseline(runtime)
    evidence = json.loads(search["evidence_json"])
    text = json.dumps(valid_brief(evidence))
    for allow_repair in (True, False):
        result = runtime.finish_generation(text, search["evidence_json"], allow_repair)
        assert not result["needs_repair"] and result["errors"] == ""
        final = runtime.unpack_result(result["result_json"])
        assert set(final) == {
            "status",
            "brief_markdown",
            "brief_json",
            "evidence_json",
            "warnings",
            "error_code",
        }
        assert final["status"] == "completed" and "SERP-only" in final["brief_markdown"]
        assert isinstance(final["brief_json"], dict) and isinstance(
            final["warnings"], list
        )


def test_bad_generation_has_one_repair_then_fails_without_raw_markup(runtime):
    _, search = baseline(runtime)
    raw = "<script>invented unsafe model response</script>"
    first = runtime.finish_generation(raw, search["evidence_json"])
    assert first["needs_repair"] and first["errors"] and first["result_json"] == ""
    second = runtime.finish_generation(raw, search["evidence_json"], False)
    assert not second["needs_repair"]
    final = runtime.unpack_result(second["result_json"])
    assert final["status"] == "generation_failed"
    assert final["error_code"] == "brief_validation_failed"
    assert raw not in second["result_json"] and final["brief_markdown"] == ""


@pytest.mark.parametrize(
    "problem", ["missing_field", "unknown_reference", "invalid_json"]
)
def test_terminal_validation_reason_reaches_final_warnings(runtime, problem):
    _, search = baseline(runtime)
    evidence = json.loads(search["evidence_json"])
    candidate = valid_brief(evidence)
    if problem == "missing_field":
        del candidate["article_plan"]
    elif problem == "unknown_reference":
        candidate["executive_summary"]["evidence_ids"] = ["P999"]
    text = (
        "<private-model-output>" if problem == "invalid_json" else json.dumps(candidate)
    )
    result = runtime.finish_generation(text, search["evidence_json"], False)
    final = runtime.unpack_result(result["result_json"])
    assert "Brief validation failed: " + result["errors"] in final["warnings"]
    assert "<private-model-output>" not in result["result_json"]
    assert final["evidence_json"] == search["evidence_json"]
    assert len(final["warnings"]) <= 30
    if problem == "missing_field":
        assert "article_plan" in result["errors"]


def test_final_diagnostic_fits_warning_limit(runtime):
    _, search = baseline(runtime)
    evidence = json.loads(search["evidence_json"])
    evidence["warnings"] = ["Existing warning " + str(i) for i in range(30)]
    result = runtime.finish_generation("not JSON", json.dumps(evidence), False)
    final = runtime.unpack_result(result["result_json"])
    assert len(final["warnings"]) == 30
    assert final["warnings"][-1].startswith("Brief validation failed:")


@pytest.mark.parametrize("text", [None, "__DIFY_MODEL_CALL_FAILED__"])
def test_generation_call_failure_without_repair(runtime, text):
    _, search = baseline(runtime)
    result = runtime.finish_generation(text, search["evidence_json"])
    assert not result["needs_repair"]
    assert (
        runtime.unpack_result(result["result_json"])["error_code"] == "llm_call_failed"
    )


@pytest.mark.parametrize("text", ["", "  \n "])
def test_empty_successful_generation_gets_one_repair(runtime, text):
    search = runtime.prepare_search(
        response(), runtime.prepare_run("api", "us_en")["config"]
    )
    result = runtime.finish_generation(text, search["evidence_json"])
    assert result["needs_repair"] and result["result_json"] == ""
    terminal = runtime.finish_generation(
        text, search["evidence_json"], allow_repair=False
    )
    assert not terminal["needs_repair"]
    assert (
        runtime.unpack_result(terminal["result_json"])["error_code"]
        == "brief_validation_failed"
    )


@pytest.mark.parametrize(
    "raw", ["not json", "[]", "null", "{}", '{"status":"completed"}', None]
)
def test_unpack_malformed_transport_returns_safe_contract(runtime, raw):
    result = runtime.unpack_result(raw)
    assert result == runtime.failure_result(
        "generation_failed", "brief_validation_failed"
    )


def test_invalid_evidence_transport_fails_safely(runtime):
    config = runtime.prepare_run("api", "us_en")["config"]
    site = runtime.finish_site({}, config, "bad json")
    assert (
        not site["ready"]
        and runtime.unpack_result(site["result_json"])["status"] == "generation_failed"
    )
    generation = runtime.finish_generation("{}", "bad json")
    assert not generation["needs_repair"]
    assert (
        runtime.unpack_result(generation["result_json"])["error_code"]
        == "brief_validation_failed"
    )


def test_evidence_budget_failure_preserves_search_failed_status(runtime):
    prepared, search = baseline(runtime, "example.org")
    runtime.EVIDENCE_BUDGET = 1
    main = runtime.prepare_search(response(), prepared["config"])
    site = runtime.finish_site(response(), prepared["config"], search["evidence_json"])
    for result in (main, site):
        final = runtime.unpack_result(result["result_json"])
        assert final["status"] == "search_failed"
        assert final["error_code"] == "evidence_too_large"


def test_full_serialized_result_limit_includes_json_escaping(runtime):
    _, search = baseline(runtime)
    evidence = json.loads(search["evidence_json"])
    evidence["warnings"] = ["\\" * 80000]
    candidate = valid_brief(evidence)
    candidate["executive_summary"]["text"] = '\\"' * 1000
    text = json.dumps(candidate)
    rendered = runtime.render_brief(candidate, evidence)
    assert len(rendered["brief_markdown"]) < 400000
    assert len(runtime._json(rendered)) >= 400000
    result = runtime.finish_generation(text, json.dumps(evidence))
    assert len(result["result_json"]) < 400000
    assert not result["needs_repair"]
    final = runtime.unpack_result(result["result_json"])
    assert final["status"] == "generation_failed"
    assert final["error_code"] == "brief_validation_failed"
