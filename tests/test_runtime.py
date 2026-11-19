"""Offline behavior checks for the embedded Dify runtime."""

import copy
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "code" / "runtime.py"
FIXTURE = ROOT / "tests" / "fixtures" / "serp-observations.json"


@pytest.fixture
def runtime():
    if not RUNTIME.exists():
        return SimpleNamespace()
    spec = importlib.util.spec_from_file_location("brief_runtime", RUNTIME)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def response(rows=None, **data):
    return {
        "ok": True,
        "status": 200,
        "engine": "google_web",
        "data": {
            "organic": rows
            if rows is not None
            else [
                {
                    "title": "API collection guide",
                    "link": "https://example.org/api",
                    "description": "An API guide.",
                    "position": 1,
                },
                {
                    "title": "Data tooling",
                    "link": "https://example.net/tools",
                    "description": "Tool selection.",
                    "position": 2,
                },
                {
                    "title": "API workflow",
                    "link": "https://example.com/workflow",
                    "description": "Workflow examples.",
                    "position": 3,
                },
            ],
            **data,
        },
    }


def evidence(runtime, **kwargs):
    config = runtime.validate_input("web scraping api", **kwargs)["config"]
    result = response(
        people_also_ask=[{"question": "How do scraping APIs work?"}],
        related_searches=[{"query": "scraping API examples"}],
    )
    return runtime.normalize_serp(result, config)["evidence"]


def claim(text="An editorial recommendation.", basis="editorial_inference", ids=None):
    return {"text": text, "basis": basis, "evidence_ids": ids or []}


def brief():
    return {
        "executive_summary": claim(
            "Search results discuss API workflows.", "serp_observation", ["P1"]
        ),
        "search_intent": {
            "primary": "mixed",
            "confidence": "low",
            "rationale": claim(ids=["P1"]),
        },
        "target_reader": claim("Developers evaluating data collection."),
        "competitor_observations": [
            {
                "source_id": "P1",
                "observations": [
                    claim(
                        "The snippet mentions an API guide.", "serp_observation", ["P1"]
                    )
                ],
            }
        ],
        "must_cover_topics": [claim("Explain API requests.", ids=["P1"])],
        "differentiation_angles": [claim("Add a reproducible example.")],
        "semantic_keywords": [
            {
                "term": "API collection",
                "basis": "editorial_inference",
                "evidence_ids": ["P1"],
            }
        ],
        "article_plan": {
            "format": "Practical guide",
            "suggested_word_count": {
                "min": 1200,
                "max": 1800,
                "rationale": claim("Editorial scope estimate."),
            },
            "outline": [
                {"h2": heading, "h3": ["Practical example"], "purpose": claim()}
                for heading in ["Overview", "Requests", "Tradeoffs", "Next steps"]
            ],
        },
        "eeat_recommendations": [claim("Demonstrate an API request.")],
        "meta": {
            "title": "A practical API guide",
            "description": "Learn how to evaluate API collection workflows.",
            "slug": "practical-api-guide",
        },
        "faq": {
            "observed_paa": [
                {
                    "question_id": "Q1",
                    "answer_direction": claim(
                        "Explain request and response flow.", ids=["Q1"]
                    ),
                }
            ],
            "suggested_questions": [
                {
                    "question": "How should teams test the workflow?",
                    "rationale": claim(),
                }
            ],
        },
        "cta": claim("Try a small representative request."),
        "internal_link_ids": [],
        "editor_checks": [
            claim("Verify claims against original pages before publication.")
        ],
    }


def valid_brief(evidence):
    """Return a deterministic valid model response for an available evidence pack."""
    candidate = brief()
    if not evidence.get("paa"):
        candidate["faq"]["observed_paa"] = []
    candidate["internal_link_ids"] = [
        item["id"]
        for item in evidence.get("internal_links", {}).get("candidates", [])[:2]
    ]
    return candidate


@pytest.mark.parametrize("maximum", [20001, 10**30])
def test_word_count_stays_within_editorial_and_dify_number_limits(runtime, maximum):
    source = evidence(runtime)
    candidate = valid_brief(source)
    candidate["article_plan"]["suggested_word_count"]["max"] = maximum
    assert not runtime.validate_brief(json.dumps(candidate), source)["valid"]


def test_long_sources_use_bounded_reference_links(runtime):
    rows = [
        {
            "title": "Source " + str(i),
            "link": "https://example.org/" + str(i) + "/" + "a" * 3800,
        }
        for i in range(5)
    ]
    e = runtime.normalize_serp(response(rows), runtime.validate_input("api")["config"])[
        "evidence"
    ]
    e = runtime.assemble_evidence(
        e, {"status": "skipped", "candidates": [], "warnings": []}
    )["evidence"]
    candidate = valid_brief(e)
    for key, count in [
        ("must_cover_topics", 12),
        ("differentiation_angles", 8),
        ("eeat_recommendations", 8),
        ("editor_checks", 10),
    ]:
        candidate[key] = [claim() for _ in range(count)]
    identifiers = [item["id"] for item in e["organic"]]

    def cite_all(value):
        if isinstance(value, dict):
            if "evidence_ids" in value:
                value["evidence_ids"] = identifiers
            for child in value.values():
                cite_all(child)
        elif isinstance(value, list):
            for child in value:
                cite_all(child)

    cite_all(candidate)
    assert runtime.validate_brief(json.dumps(candidate), e)["valid"]
    result = runtime.render_brief(candidate, e)
    assert result["status"] == "completed"
    assert len(result["brief_markdown"]) < 400000
    for source in e["organic"]:
        assert result["brief_markdown"].count(source["url"]) == 1


@pytest.mark.parametrize("market", ["us_en", "gb_en"])
def test_input_supported_english_markets(runtime, market):
    result = runtime.validate_input(
        "  web scraping api  ",
        market,
        "Developers",
        "Example brand",
        "Docs.Example.com",
    )
    assert result["valid"] and result["error_code"] == ""
    assert result["config"]["keyword"] == "web scraping api"
    assert result["config"]["site_domain"] == "docs.example.com"
    assert result["config"]["site_query"] == "site:docs.example.com web scraping api"
    assert result["config"]["output_language"] == "en"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"keyword": ""},
        {"keyword": "x" * 301},
        {"keyword": 3},
        {"keyword": "x", "market": "hk_zh"},
        {"keyword": "x", "site_domain": "https://user:pass@example.org"},
        {"keyword": "x", "site_domain": "example.org/path"},
        {"keyword": "x", "site_domain": "example.org:1234"},
    ],
)
def test_bad_input_stops_cleanly(runtime, kwargs):
    result = runtime.validate_input(**kwargs)
    assert result["valid"] is False
    assert result["error_code"]


@pytest.mark.parametrize("run_id", ["R1", "R2", "R3", "R4", "R5-FINAL", "R6"])
def test_all_recorded_successes_normalize(runtime, run_id):
    runs = json.loads(FIXTURE.read_text(encoding="utf-8"))["runs"]
    run = next(row for row in runs if row["case"]["id"] == run_id)
    config = runtime.validate_input(run["case"]["query"])["config"]
    result = runtime.normalize_serp(run["result"], config)
    if run_id == "R2":
        assert result["can_generate"] is False
        assert result["error_code"] == "invalid_serp_response"
        return
    assert result["can_generate"] is True
    normalized = result["evidence"]
    assert normalized["organic_count"] >= 1
    assert normalized["engine"] == "google_web"
    assert normalized["evidence_level"] == "serp_only"
    assert normalized["output_language"] == "en"
    assert all(
        row["id"] == "P" + str(i) for i, row in enumerate(normalized["organic"], 1)
    )


@pytest.mark.parametrize(
    "result,error",
    [
        ({}, "serp_tool_error"),
        ({"ok": False}, "serp_tool_error"),
        (
            {"ok": True, "status": 500, "engine": "google_web", "data": {}},
            "serp_tool_error",
        ),
        (
            {"ok": True, "status": 200, "engine": "bing", "data": {}},
            "invalid_serp_response",
        ),
        (
            {
                "ok": True,
                "status": 200,
                "engine": "google_web",
                "data": {"unexpected": []},
            },
            "invalid_serp_response",
        ),
        (response([]), "no_results"),
        (response("bad"), "invalid_serp_response"),
    ],
)
def test_serp_failure_paths(runtime, result, error):
    normalized = runtime.normalize_serp(result, runtime.validate_input("api")["config"])
    assert normalized["can_generate"] is False
    assert normalized["error_code"] == error


def test_normalization_preserves_absence_deduplicates_and_filters_urls(runtime):
    rows = [
        {"title": "First", "link": "https://EXAMPLE.org:443/path#first", "position": 7},
        {"title": "Duplicate", "link": "https://example.org/path#second"},
        {"title": "No rank", "link": "https://reddit.com/item"},
        {"title": "Credentials", "link": "https://user:pw@example.net/private"},
        {"title": "Script", "link": "javascript:alert(1)"},
        {"title": "Too long", "link": "https://example.com/" + "a" * 4096},
        {
            "title": "x" * 400,
            "link": "https://example.com/other",
            "snippet": "s" * 1600,
            "rank": 12,
        },
    ]
    result = runtime.normalize_serp(
        response(
            rows,
            people_also_ask=[
                {"question": "Observed?"},
                {"title": "Not a question field"},
            ],
            related_searches=[{"query": "related"}, {"text": "second"}],
        ),
        runtime.validate_input("api")["config"],
    )
    e = result["evidence"]
    assert e["organic_count"] == 3 and e["discarded_count"] == 4
    assert e["organic"][0]["url"] == "https://EXAMPLE.org:443/path#first"
    assert e["organic"][0]["rank"] == 7
    assert e["organic"][1]["rank"] is None and e["organic"][1]["snippet"] == ""
    assert e["organic"][2]["rank"] == 12 and e["organic"][2]["serp_order"] == 7
    assert (
        len(e["organic"][2]["title"]) == 300 and len(e["organic"][2]["snippet"]) == 1200
    )
    assert e["paa"] == [
        {
            "id": "Q1",
            "question": "Observed?",
            "answer_excerpt": None,
            "source_url": None,
        }
    ]
    assert [x["query"] for x in e["related"]] == ["related", "second"]
    assert e["quality"] == "sufficient"


def test_compatible_schema_wrong_optional_types_and_context(runtime):
    r = response(people_also_ask="bad", related_searches={"bad": 1})
    r["data"]["organic_results"] = r["data"].pop("organic")[:1]
    e = runtime.normalize_serp(
        r,
        runtime.validate_input(
            "api", audience="Developers", brand_context="Brand scope"
        )["config"],
    )["evidence"]
    assert e["quality"] == "limited" and e["paa"] == [] and e["related"] == []
    assert len(e["warnings"]) >= 2
    assert e["audience"] == {"id": "U1", "text": "Developers", "source": "user"}
    assert e["brand_context"] == {"id": "B1", "text": "Brand scope", "source": "user"}


def test_site_host_whitelist_and_optional_failure(runtime):
    config = runtime.validate_input("api", site_domain="example.com")["config"]
    r = response(
        [
            {"title": "Valid", "link": "https://example.com/page"},
            {"title": "Subdomain", "link": "https://docs.example.com/a"},
            {"title": "Lookalike", "link": "https://evil-example.com/a"},
            {"title": "Suffix", "link": "https://example.com.evil.org/a"},
        ]
    )
    site = runtime.normalize_site(r, config)["site"]
    assert site["status"] == "success"
    assert [c["id"] for c in site["candidates"]] == ["I1", "I2"]
    assert runtime.normalize_site({"ok": False}, config)["site"]["status"] == "failed"
    assert (
        runtime.normalize_site({}, runtime.validate_input("api")["config"])["site"][
            "status"
        ]
        == "skipped"
    )


def test_budget_trimming_preserves_referential_integrity(runtime):
    config = runtime.validate_input(
        "api", audience="a" * 500, brand_context="b" * 3000
    )["config"]
    rows = [
        {
            "title": "t" * 300,
            "link": "https://example.org/" + str(i) + "a" * 3500,
            "description": "s" * 1200,
        }
        for i in range(20)
    ]
    e = runtime.normalize_serp(
        response(
            rows,
            people_also_ask=[{"question": "q" * 300} for _ in range(20)],
            related_searches=[{"query": "r" * 300} for _ in range(20)],
        ),
        config,
    )["evidence"]
    e["untrusted_debug"] = "d" * 100000
    result = runtime.assemble_evidence(
        e, {"status": "skipped", "candidates": [], "warnings": []}
    )
    assert result["ready"]
    assert len(result["evidence_json"]) <= 24000
    bounded = json.loads(result["evidence_json"])
    assert bounded == result["evidence"] and "untrusted_debug" not in bounded
    assert bounded["organic_count"] == len(bounded["organic"]) >= 1
    assert bounded["warnings"]
    assert bounded["quality"] == (
        "limited" if len(bounded["organic"]) < 3 else "sufficient"
    )
    available = {row["id"] for row in bounded["organic"]}
    candidate = brief()
    candidate["faq"]["observed_paa"] = []
    assert runtime.validate_brief(json.dumps(candidate), bounded)["valid"]
    candidate["executive_summary"]["evidence_ids"] = ["P10"]
    assert "P10" in {row["id"] for row in e["organic"]}
    assert (
        "P10" not in available
        and not runtime.validate_brief(json.dumps(candidate), bounded)["valid"]
    )


def test_valid_brief_and_fenced_json(runtime):
    e = evidence(runtime)
    text = json.dumps(brief())
    assert runtime.validate_brief(text, e)["valid"]
    assert runtime.validate_brief("```json\n" + text + "\n```", e)["valid"]


@pytest.mark.parametrize(
    "change",
    [
        lambda b: b["executive_summary"].update(evidence_ids=["P999"]),
        lambda b: b["executive_summary"].update(evidence_ids=[]),
        lambda b: b["executive_summary"].update(
            basis="user_context", evidence_ids=["P1"]
        ),
        lambda b: b["executive_summary"].update(
            text="See https://invented.example/page"
        ),
        lambda b: b["executive_summary"].update(text="See [invented](relative-path)"),
        lambda b: b["executive_summary"].update(text=chr(0x4E2D)),
        lambda b: b["differentiation_angles"][0].update(
            basis="serp_observation", evidence_ids=["P1"]
        ),
        lambda b: b["article_plan"]["suggested_word_count"].update(min=2000, max=1000),
        lambda b: b["article_plan"]["suggested_word_count"].update(min=True),
        lambda b: b["article_plan"]["outline"].pop(),
        lambda b: b["faq"]["observed_paa"][0].update(question_id="Q999"),
        lambda b: b.update(internal_link_ids=["P1"]),
        lambda b: b.update(extra="Unknown field"),
        lambda b: b["meta"].update(extra="Unknown field"),
        lambda b: b["executive_summary"].update(text="x" * 2001),
    ],
)
def test_invalid_model_output_fails_validation(runtime, change):
    candidate = brief()
    change(candidate)
    result = runtime.validate_brief(json.dumps(candidate), evidence(runtime))
    assert result["valid"] is False and result["errors"]


@pytest.mark.parametrize(
    "text", ["not json", "[]", "{}", '{"a": NaN}', "```json\n{}", "null"]
)
def test_malformed_model_json_returns_safe_errors(runtime, text):
    assert not runtime.validate_brief(text, evidence(runtime))["valid"]


def test_renderer_returns_fixed_contract_and_source_links(runtime):
    e = evidence(runtime)
    candidate = brief()
    rendered = runtime.render_brief(candidate, e)
    assert set(rendered) == {
        "status",
        "brief_markdown",
        "brief_json",
        "evidence_json",
        "warnings",
        "error_code",
    }
    assert rendered["status"] == "completed" and rendered["error_code"] == ""
    markdown = rendered["brief_markdown"]
    assert len([line for line in markdown.splitlines() if line.startswith("## ")]) == 13
    assert "https://example.org/api" in markdown and "P1" in markdown
    assert "SERP-only" in markdown and "search volume" in markdown
    assert rendered["brief_json"] == candidate
    assert json.loads(rendered["evidence_json"])["organic_count"] == 3


def test_limited_render_and_safe_failure_result(runtime):
    e = evidence(runtime)
    e["organic"] = e["organic"][:1]
    e["organic_count"] = 1
    e["quality"] = "limited"
    assert "limited" in runtime.render_brief(brief(), e)["brief_markdown"].lower()
    assert runtime.render_brief(brief(), e)["status"] == "limited_evidence"
    failed = runtime.failure_result("generation_failed", "brief_validation_failed", e)
    assert (
        failed["status"] == "generation_failed"
        and failed["error_code"] == "brief_validation_failed"
    )
    assert failed["brief_markdown"] == "" and failed["brief_json"] == {}
    assert isinstance(failed["warnings"], list)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"keyword": "x" * 201},
        {"keyword": "x", "audience": "x" * 501},
        {"keyword": "x", "brand_context": "x" * 3001},
        {"keyword": "x", "site_domain": "https://example.com"},
        {"keyword": "x", "site_domain": "example.com/"},
        {"keyword": "x", "site_domain": "example.com:443"},
    ],
)
def test_input_contract_limits_and_plain_domains(runtime, kwargs):
    assert runtime.validate_input(**kwargs)["error_code"] == "invalid_input"


def test_organic_contract_flags_and_first_ten_candidates(runtime):
    rows = [
        {"title": "Result", "link": "https://example.org/" + str(i)} for i in range(11)
    ]
    rows[0]["description"] = "x" * 1201
    rows[0]["date"] = "Nov 1, 2026"
    rows[1]["link"] = "invalid"
    e = runtime.normalize_serp(response(rows), runtime.validate_input("api")["config"])[
        "evidence"
    ]
    assert e["organic_count"] == 9
    assert e["organic"][0]["snippet_truncated"] is True
    assert e["organic"][0]["snippet_missing"] is False
    assert e["organic"][0]["display_date"] == "Nov 1, 2026"
    assert e["organic"][1]["snippet_missing"] is True
    assert e["organic"][1]["display_date"] is None
    assert all(row["serp_order"] <= 10 for row in e["organic"])


def test_internal_link_object_and_empty_failure_contract(runtime):
    e = evidence(runtime)
    site = {"status": "failed", "candidates": [], "warnings": ["Site search failed."]}
    assembled = runtime.assemble_evidence(e, site)
    assert assembled["evidence"]["internal_links"] == site
    assert assembled["evidence"]["warnings"] == site["warnings"]
    failed = runtime.failure_result("search_failed", "serp_tool_error")
    assert (
        failed["brief_json"] == {}
        and failed["evidence_json"] == "{}"
        and failed["warnings"] == []
    )
    assert (
        failed["status"] == "search_failed"
        and failed["error_code"] == "serp_tool_error"
    )


@pytest.mark.parametrize(
    "field,maximum",
    [
        ("semantic_keywords", 15),
        ("differentiation_angles", 8),
        ("eeat_recommendations", 8),
    ],
)
def test_schema_array_limits(runtime, field, maximum):
    candidate = brief()
    candidate[field] = candidate[field] * (maximum + 1)
    assert not runtime.validate_brief(json.dumps(candidate), evidence(runtime))["valid"]


@pytest.mark.parametrize(
    "checks",
    [
        [],
        None,
        "Verify source claims before publication.",
        ["Check original sources.", "Verify dates."],
        [claim()] * 12,
        claim(),
    ],
)
def test_editor_checks_normalize_without_rejecting_a_valid_brief(runtime, checks):
    candidate = brief()
    candidate["editor_checks"] = checks
    e = evidence(runtime)
    validated = runtime.validate_brief(json.dumps(candidate), e)
    assert validated["valid"]
    assert 1 <= len(validated["brief"]["editor_checks"]) <= 10
    assert validated["warnings"]
    assert all(isinstance(item, dict) for item in validated["brief"]["editor_checks"])
    result = runtime.finish_generation(json.dumps(candidate), json.dumps(e), False)
    final = runtime.unpack_result(result["result_json"])
    assert final["status"] == "completed"
    assert any("Editor checks" in warning for warning in final["warnings"])


def test_invalid_editor_checks_are_validated_before_trimming(runtime):
    candidate = brief()
    candidate["editor_checks"] = [claim()] * 10 + [claim(ids=["P999"])]
    result = runtime.validate_brief(json.dumps(candidate), evidence(runtime))
    assert not result["valid"] and "evidence_ids" in result["errors"]


@pytest.mark.parametrize(
    "checks",
    [
        42,
        {"unexpected_private_key": "private value"},
        ["See https://invented.invalid/"],
        [""],
    ],
)
def test_editor_checks_normalization_does_not_bypass_content_checks(runtime, checks):
    candidate = brief()
    candidate["editor_checks"] = checks
    result = runtime.validate_brief(json.dumps(candidate), evidence(runtime))
    assert not result["valid"]
    assert "private value" not in result["errors"]


def test_array_diagnostic_reports_type_or_count(runtime):
    candidate = brief()
    candidate["must_cover_topics"] = "private model text"
    result = runtime.validate_brief(json.dumps(candidate), evidence(runtime))
    assert "Expected a JSON array" in result["errors"] and "str" in result["errors"]
    assert "private model text" not in result["errors"]
    candidate["must_cover_topics"] = []
    result = runtime.validate_brief(json.dumps(candidate), evidence(runtime))
    assert "1-12" in result["errors"] and "0" in result["errors"]


def test_rank_falls_back_to_valid_provider_rank(runtime):
    rows = [
        {"title": "Result", "link": "https://example.org/", "position": None, "rank": 8}
    ]
    e = runtime.normalize_serp(response(rows), runtime.validate_input("api")["config"])[
        "evidence"
    ]
    assert e["organic"][0]["rank"] == 8


@pytest.mark.parametrize(
    "url",
    [
        "https://example..org/a",
        "https://-invalid.example/a",
        "https://invalid-.example/a",
        "https://example.org:99999/a",
    ],
)
def test_malformed_url_authorities_are_discarded(runtime, url):
    rows = [{"title": "Invalid", "link": url}]
    result = runtime.normalize_serp(
        response(rows), runtime.validate_input("api")["config"]
    )
    assert not result["can_generate"]
    assert result["evidence"]["discarded_count"] == 1


def test_optional_null_values_produce_diagnostic_warnings(runtime):
    result = runtime.normalize_serp(
        response(people_also_ask=None, related_searches=None),
        runtime.validate_input("api")["config"],
    )
    assert len(result["evidence"]["warnings"]) == 2


def test_user_context_and_missing_source_citations(runtime):
    e = evidence(runtime, audience="Developers", brand_context="Acme")
    candidate = brief()
    candidate["target_reader"] = claim("Developers", "user_context", ["U1"])
    assert runtime.validate_brief(json.dumps(candidate), e)["valid"]
    assert not runtime.validate_brief(json.dumps(candidate), evidence(runtime))["valid"]


def test_assembly_keeps_inputs_unchanged(runtime):
    e = evidence(runtime)
    original = copy.deepcopy(e)
    site = {
        "status": "success",
        "candidates": [
            {
                "id": "I1",
                "title": "Docs",
                "url": "https://example.org/docs",
                "snippet": "Reference",
            }
        ],
        "warnings": [],
    }
    bounded = runtime.assemble_evidence(e, site)["evidence"]
    assert e == original
    candidate = valid_brief(bounded)
    assert runtime.validate_brief(json.dumps(candidate), bounded)["valid"]
    assert (
        "https://example.org/docs"
        in runtime.render_brief(candidate, bounded)["brief_markdown"]
    )


def test_duplicate_json_keys_are_rejected(runtime):
    text = json.dumps(brief())
    text = text[:-1] + ', "cta": ' + json.dumps(claim()) + "}"
    assert not runtime.validate_brief(text, evidence(runtime))["valid"]


def test_multiline_brand_context_is_preserved(runtime):
    context = "Product scope:\nPublic data collection.\r\n\tAudience: developers."
    result = runtime.validate_input("api", brand_context=context)
    assert result["valid"]
    assert result["config"]["brand_context"] == context
    assert not runtime.validate_input("api", brand_context="Invalid\x00context")[
        "valid"
    ]


def test_international_domain_names_are_normalized(runtime):
    result = runtime.validate_input("api", site_domain="b\u00fccher.example")
    assert result["valid"]
    assert result["config"]["site_domain"] == "xn--bcher-kva.example"
    assert result["config"]["site_query"] == "site:xn--bcher-kva.example api"


def test_display_urls_preserve_anchors_case_and_ports(runtime):
    original_url = "https://EXAMPLE.org:443/API#QuickStart"
    paa_url = "https://DOCS.example.org:443/FAQ#Answer"
    rows = [
        {"title": "Original anchor", "link": " " + original_url + " "},
        {"title": "Duplicate anchor", "link": "https://example.org/API#different"},
    ]
    normalized = runtime.normalize_serp(
        response(
            rows,
            people_also_ask=[{"question": "How does it work?", "source_url": paa_url}],
        ),
        runtime.validate_input("api")["config"],
    )["evidence"]
    assert normalized["organic_count"] == 1
    assert normalized["organic"][0]["url"] == original_url
    assert normalized["paa"][0]["source_url"] == paa_url
    rendered = runtime.render_brief(valid_brief(normalized), normalized)[
        "brief_markdown"
    ]
    assert "[P1][source-P1]" in rendered
    assert "[source-P1]: <" + original_url + ">" in rendered
    assert "[Q1][source-Q1]" in rendered
    assert "[source-Q1]: <" + paa_url + ">" in rendered


def test_unspecified_audience_requires_editorial_target_reader(runtime):
    candidate = brief()
    candidate["target_reader"] = claim("Developers", "serp_observation", ["P1"])
    result = runtime.validate_brief(json.dumps(candidate), evidence(runtime))
    assert not result["valid"] and "target_reader" in result["errors"]


@pytest.mark.parametrize(
    "optional", [{}, {"people_also_ask": [], "related_searches": []}]
)
def test_missing_optional_serp_features_are_explicit(runtime, optional):
    normalized = runtime.normalize_serp(
        response(**optional), runtime.validate_input("api")["config"]
    )["evidence"]
    assert "No People Also Ask questions were returned." in normalized["warnings"]
    assert "No related searches were returned." in normalized["warnings"]
    candidate = valid_brief(normalized)
    rendered = runtime.render_brief(candidate, normalized)["brief_markdown"]
    assert "No People Also Ask questions were returned." in rendered
    assert "No related searches were returned." in rendered
    assert "API collection" in rendered


def test_observed_but_unselected_paa_is_distinguished(runtime):
    candidate = brief()
    candidate["faq"]["observed_paa"] = []
    rendered = runtime.render_brief(candidate, evidence(runtime))["brief_markdown"]
    assert "Observed PAA questions were returned but none were selected." in rendered
    assert "No People Also Ask questions were returned." not in rendered


@pytest.mark.parametrize("variant", ["organic", "organic_results"])
def test_schema_variant_and_title_truncation_survive_assembly(runtime, variant):
    raw = response()
    raw["data"]["organic"][0]["title"] = "t" * 301
    if variant == "organic_results":
        raw["data"][variant] = raw["data"].pop("organic")
    normalized = runtime.normalize_serp(raw, runtime.validate_input("api")["config"])[
        "evidence"
    ]
    assert normalized["schema_variant"] == variant
    assert normalized["organic"][0]["title_truncated"] is True
    assert normalized["organic"][1]["title_truncated"] is False
    assembled = runtime.assemble_evidence(
        normalized, {"status": "skipped", "candidates": [], "warnings": []}
    )["evidence"]
    assert assembled["schema_variant"] == variant
    assert assembled["organic"][0]["title_truncated"] is True


def test_site_search_filters_first_ten_rows_before_selecting_five(runtime):
    rows = [
        {"title": "Wrong host", "link": "https://other.org/" + str(i)} for i in range(5)
    ]
    rows += [
        {"title": "Site candidate", "link": "https://example.org/" + str(i)}
        for i in range(6)
    ]
    site = runtime.normalize_site(
        response(rows),
        runtime.validate_input("api", site_domain="example.org")["config"],
    )["site"]
    assert len(site["candidates"]) == 5
    assert [item["serp_order"] for item in site["candidates"]] == [6, 7, 8, 9, 10]


def test_assembled_warnings_are_unique_and_bounded(runtime):
    normalized = evidence(runtime)
    normalized["warnings"] = ["Same warning."] * 40
    site = {
        "status": "failed",
        "candidates": [],
        "warnings": ["Warning " + str(i) for i in range(40)],
    }
    assembled = runtime.assemble_evidence(normalized, site)["evidence"]
    assert len(assembled["warnings"]) <= 30
    assert len(assembled["warnings"]) == len(set(assembled["warnings"]))
