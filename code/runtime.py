"""Standard-library processing shared by self-contained Dify Code nodes."""

import copy
import datetime
import json
import re
from urllib.parse import quote, urlsplit, urlunsplit

EVIDENCE_BUDGET = 24000
BASES = {"serp_observation", "editorial_inference", "user_context"}
EVIDENCE_FIELDS = (
    "schema_version",
    "schema_variant",
    "query",
    "market",
    "output_language",
    "engine",
    "observed_at",
    "evidence_level",
    "quality",
    "organic_count",
    "discarded_count",
    "organic",
    "paa",
    "related",
    "internal_links",
    "audience",
    "brand_context",
    "warnings",
)


def _text(value, limit):
    if not isinstance(value, str):
        return ""
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", value).strip()[:limit]


def _json(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def _warn(warnings, message):
    if message not in warnings and len(warnings) < 30:
        warnings.append(message)


def _url_key(value):
    if not isinstance(value, str):
        return ""
    value = value.strip()
    if not value or len(value) > 4096 or re.search(r"[\s\x00-\x1f\x7f\\]", value):
        return ""
    try:
        parts = urlsplit(value)
        if parts.scheme.lower() not in {"http", "https"} or not parts.hostname:
            return ""
        if parts.username is not None or parts.password is not None:
            return ""
        host = parts.hostname.lower().encode("idna").decode("ascii").rstrip(".")
        if not re.fullmatch(r"[a-z0-9.:-]+", host):
            return ""
        if ":" not in host and (
            len(host) > 253
            or not all(
                re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
                for label in host.split(".")
            )
        ):
            return ""
        port = parts.port
        authority = "[" + host + "]" if ":" in host else host
        if port is not None and (parts.scheme.lower(), port) not in {
            ("https", 443),
            ("http", 80),
        }:
            authority += ":" + str(port)
        return urlunsplit(
            (parts.scheme.lower(), authority, parts.path or "/", parts.query, "")
        )
    except (ValueError, UnicodeError):
        return ""


def _url(value):
    return value.strip() if _url_key(value) else ""


def validate_input(
    keyword: str,
    market: str = "us_en",
    audience: str = "",
    brand_context: str = "",
    site_domain: str = "",
) -> dict:
    values = {
        "keyword": keyword,
        "market": market,
        "audience": audience,
        "brand_context": brand_context,
        "site_domain": site_domain,
    }
    limits = {
        "keyword": 200,
        "market": 20,
        "audience": 500,
        "brand_context": 3000,
        "site_domain": 253,
    }
    for name, value in values.items():
        controls = (
            r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]"
            if name == "brand_context"
            else r"[\x00-\x1f\x7f]"
        )
        if (
            not isinstance(value, str)
            or len(value.strip()) > limits[name]
            or re.search(controls, value)
        ):
            return {"valid": False, "config": {}, "error_code": "invalid_input"}
    keyword, market = keyword.strip(), market.strip()
    if not keyword or market not in {"us_en", "gb_en"}:
        return {"valid": False, "config": {}, "error_code": "invalid_input"}
    domain = site_domain.strip().lower()
    if domain:
        try:
            domain = domain.encode("idna").decode("ascii")
            labels = domain.split(".")
            valid_domain = (
                len(domain) <= 253
                and len(labels) >= 2
                and all(
                    re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
                    for label in labels
                )
            )
            if not valid_domain:
                raise ValueError("Invalid domain")
        except (ValueError, UnicodeError):
            return {"valid": False, "config": {}, "error_code": "invalid_input"}
    config = {
        "keyword": keyword,
        "market": market,
        "output_language": "en",
        "audience": audience.strip(),
        "brand_context": brand_context.strip(),
        "site_domain": domain,
        "site_query": "site:" + domain + " " + keyword if domain else "",
    }
    return {"valid": True, "config": config, "error_code": ""}


def _quality(count):
    return "empty" if count == 0 else "limited" if count < 3 else "sufficient"


def _empty_evidence(config):
    return {
        "schema_version": "1.0",
        "query": config.get("keyword", ""),
        "market": config.get("market", "us_en"),
        "output_language": "en",
        "engine": "google_web",
        "observed_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "evidence_level": "serp_only",
        "quality": "empty",
        "organic_count": 0,
        "discarded_count": 0,
        "organic": [],
        "paa": [],
        "related": [],
        "internal_links": {"status": "skipped", "candidates": [], "warnings": []},
        "audience": {"id": "U1", "text": config["audience"], "source": "user"}
        if config.get("audience")
        else None,
        "brand_context": {"id": "B1", "text": config["brand_context"], "source": "user"}
        if config.get("brand_context")
        else None,
        "warnings": [],
    }


def _response_rows(result):
    if not isinstance(result, dict) or result.get("ok") is not True:
        return None, "serp_tool_error"
    status = result.get("status")
    if type(status) is not int or not 200 <= status < 300:
        return None, "serp_tool_error"
    data = result.get("data")
    if result.get("engine") != "google_web" or not isinstance(data, dict):
        return None, "invalid_serp_response"
    rows = data.get("organic", data.get("organic_results"))
    if not isinstance(rows, list):
        return None, "invalid_serp_response"
    return rows, ""


def _organic(rows, prefix, limit, domain="", scan_limit=10):
    output, seen, discarded = [], set(), 0
    for order, row in enumerate(rows, 1):
        if order > scan_limit:
            discarded += 1
            continue
        if not isinstance(row, dict):
            discarded += 1
            continue
        url = _url(row.get("link", row.get("url")))
        url_key = _url_key(url)
        title = _text(row.get("title"), 301)
        host = urlsplit(url_key).hostname if url_key else ""
        if (
            not url
            or not title
            or url_key in seen
            or (domain and host != domain and not host.endswith("." + domain))
            or len(output) >= limit
        ):
            discarded += 1
            continue
        seen.add(url_key)
        position = next(
            (
                row[field]
                for field in ("position", "rank")
                if type(row.get(field)) is int and row[field] > 0
            ),
            None,
        )
        snippet = _text(row.get("description", row.get("snippet")), 1201)
        output.append(
            {
                "id": prefix + str(len(output) + 1),
                "title": title[:300],
                "title_truncated": len(title) > 300,
                "url": url,
                "snippet": snippet[:1200],
                "rank": position,
                "serp_order": order,
                "snippet_missing": not bool(snippet),
                "snippet_truncated": len(snippet) > 1200,
                "display_date": _text(row.get("date", row.get("display_date")), 100)
                or None,
            }
        )
    return output, discarded


def normalize_serp(result: dict, config: dict) -> dict:
    evidence = _empty_evidence(config)
    rows, error = _response_rows(result)
    if error:
        return {"can_generate": False, "evidence": evidence, "error_code": error}
    evidence["organic"], evidence["discarded_count"] = _organic(rows, "P", 10)
    evidence["organic_count"] = len(evidence["organic"])
    evidence["quality"] = _quality(evidence["organic_count"])
    if evidence["discarded_count"]:
        _warn(
            evidence["warnings"],
            "Invalid, duplicate, or excess organic results were discarded.",
        )
    data = result["data"]
    evidence["schema_variant"] = "organic" if "organic" in data else "organic_results"
    for key, aliases, target, limit in [
        ("people_also_ask", ("paa",), "paa", 10),
        ("related_searches", ("related",), "related", 15),
    ]:
        items = (
            data[key]
            if key in data
            else next((data[a] for a in aliases if a in data), [])
        )
        if not isinstance(items, list):
            _warn(
                evidence["warnings"],
                "Optional " + key + " data had an unsupported type and was ignored.",
            )
            continue
        seen = set()
        for item in items:
            if not isinstance(item, dict):
                continue
            text = _text(
                item.get("question")
                if target == "paa"
                else item.get("query", item.get("text")),
                500 if target == "paa" else 300,
            )
            if not text or text.casefold() in seen or len(evidence[target]) >= limit:
                continue
            seen.add(text.casefold())
            if target == "paa":
                evidence[target].append(
                    {
                        "id": "Q" + str(len(evidence[target]) + 1),
                        "question": text,
                        "answer_excerpt": _text(
                            item.get("answer", item.get("answer_excerpt")), 1200
                        )
                        or None,
                        "source_url": _url(
                            item.get("source_url", item.get("link", item.get("url")))
                        )
                        or None,
                    }
                )
            else:
                evidence[target].append(
                    {"id": "K" + str(len(evidence[target]) + 1), "query": text}
                )
        if not evidence[target]:
            _warn(
                evidence["warnings"],
                "No People Also Ask questions were returned."
                if target == "paa"
                else "No related searches were returned.",
            )
    if evidence["quality"] == "limited":
        _warn(
            evidence["warnings"],
            "Limited sample: fewer than three valid organic results; conclusions require additional research.",
        )
    return {
        "can_generate": bool(evidence["organic"]),
        "evidence": evidence,
        "error_code": "" if evidence["organic"] else "no_results",
    }


def normalize_site(result: dict, config: dict) -> dict:
    if not config.get("site_domain"):
        return {"site": {"status": "skipped", "candidates": [], "warnings": []}}
    rows, error = _response_rows(result)
    if error:
        return {
            "site": {
                "status": "failed",
                "candidates": [],
                "warnings": [
                    "Optional site search failed; internal-link suggestions are unavailable."
                ],
            }
        }
    candidates, discarded = _organic(rows, "I", 5, config["site_domain"])
    warnings = (
        ["Invalid, duplicate, off-domain, or excess site results were discarded."]
        if discarded
        else []
    )
    return {
        "site": {"status": "success", "candidates": candidates, "warnings": warnings}
    }


def assemble_evidence(evidence: dict, site: dict) -> dict:
    bounded = {
        key: copy.deepcopy(evidence[key]) for key in EVIDENCE_FIELDS if key in evidence
    }
    original_warnings = bounded.get("warnings", [])
    bounded["warnings"] = []
    for warning in original_warnings:
        _warn(bounded["warnings"], warning)
    bounded["internal_links"] = {
        "status": site.get("status", "skipped"),
        "candidates": copy.deepcopy(site.get("candidates", []))[:5],
        "warnings": [],
    }
    for warning in site.get("warnings", []):
        _warn(bounded["warnings"], warning)
        _warn(bounded["internal_links"]["warnings"], warning)
    row_fields = {
        "organic": (
            "id",
            "title",
            "title_truncated",
            "url",
            "snippet",
            "rank",
            "serp_order",
            "snippet_missing",
            "snippet_truncated",
            "display_date",
        ),
        "paa": ("id", "question", "answer_excerpt", "source_url"),
        "related": ("id", "query"),
    }
    for key, fields in row_fields.items():
        bounded[key] = [
            {field: row[field] for field in fields if field in row}
            for row in bounded.get(key, [])
        ]
    bounded["internal_links"]["candidates"] = [
        {
            field: row[field]
            for field in ("id", "title", "url", "snippet")
            if field in row
        }
        for row in bounded["internal_links"]["candidates"]
    ]
    for key in ("audience", "brand_context"):
        if bounded.get(key):
            bounded[key] = {
                field: bounded[key][field]
                for field in ("id", "text", "source")
                if field in bounded[key]
            }

    def refresh():
        bounded["organic_count"] = len(bounded["organic"])
        bounded["quality"] = _quality(bounded["organic_count"])
        return _json(bounded)

    serialized = refresh()
    if len(serialized) > EVIDENCE_BUDGET:
        _warn(
            bounded["warnings"],
            "Evidence was trimmed to fit the model input budget; use only the retained source IDs.",
        )
        bounded["related"] = bounded["related"][:10]
        for limit in (600, 300):
            for rows in (bounded["organic"], bounded["internal_links"]["candidates"]):
                for row in rows:
                    if (
                        "snippet_truncated" in row
                        and len(row.get("snippet", "")) > limit
                    ):
                        row["snippet_truncated"] = True
                    row["snippet"] = row.get("snippet", "")[:limit]
            for row in bounded["paa"]:
                if row.get("answer_excerpt"):
                    row["answer_excerpt"] = row["answer_excerpt"][:limit]
            serialized = refresh()
            if len(serialized) <= EVIDENCE_BUDGET:
                break
        while (
            len(serialized) > EVIDENCE_BUDGET
            and bounded["internal_links"]["candidates"]
        ):
            bounded["internal_links"]["candidates"].pop()
            serialized = refresh()
        while len(serialized) > EVIDENCE_BUDGET and len(bounded["organic"]) > 1:
            bounded["organic"].pop()
            bounded["discarded_count"] = bounded.get("discarded_count", 0) + 1
            serialized = refresh()
        for key in ("related", "paa"):
            while len(serialized) > EVIDENCE_BUDGET and bounded[key]:
                bounded[key].pop()
                serialized = refresh()
    if bounded["quality"] == "limited":
        _warn(
            bounded["warnings"],
            "Limited sample: fewer than three valid organic results; conclusions require additional research.",
        )
        serialized = refresh()
    ready = bool(bounded["organic"]) and len(serialized) <= EVIDENCE_BUDGET
    return {
        "ready": ready,
        "evidence": bounded,
        "evidence_json": serialized,
        "error_code": ""
        if ready
        else "no_results"
        if not bounded["organic"]
        else "evidence_too_large",
    }


def _catalog(evidence):
    result = {}
    for key in ("organic", "paa", "related"):
        for item in evidence.get(key, []):
            result[item["id"]] = item
    for item in evidence.get("internal_links", {}).get("candidates", []):
        result[item["id"]] = item
    for key in ("audience", "brand_context"):
        if evidence.get(key):
            result[evidence[key]["id"]] = evidence[key]
    return result


def _reject_constants(value):
    raise ValueError("Non-finite JSON values are not permitted")


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON keys are not permitted")
        result[key] = value
    return result


def validate_brief(text: str, evidence: dict, *, omit_unattributed=False) -> dict:
    try:
        if not isinstance(text, str) or len(text) > 60000:
            raise ValueError("Model output must be JSON text within 60000 characters")
        text = text.strip()
        fenced = re.fullmatch(r"```(?:json)?\s*\n([\s\S]*?)\n```", text, re.IGNORECASE)
        if fenced:
            text = fenced.group(1)
        brief = json.loads(
            text, parse_constant=_reject_constants, object_pairs_hook=_unique_object
        )
    except (ValueError, TypeError, RecursionError):
        return {
            "valid": False,
            "brief": {},
            "errors": "Model output is not a valid bounded JSON object.",
        }
    catalog = _catalog(evidence)
    normalization_warnings = []

    def fail(path, reason):
        raise ValueError(path + ": " + reason)

    def obj(value, keys, path):
        if not isinstance(value, dict):
            fail(path, "Expected a JSON object.")
        missing = [key for key in keys if key not in value]
        extra_count = len(set(value) - set(keys))
        if missing or extra_count:
            details = []
            if missing:
                details.append("Missing required fields: " + ", ".join(missing) + ".")
            if extra_count:
                details.append("Unexpected field count: " + str(extra_count) + ".")
            fail(path, " ".join(details))

    def string(value, path, limit=2000):
        if not isinstance(value, str) or not value.strip() or len(value) > limit:
            fail(path, "Expected nonempty text within the field length limit.")
        if re.search(
            r"https?\s*:|\[[^\]]*\]\s*\(|\[[^\]]+\]\s*\[|\[[^\]]+\]\s*:|<\s*a\b",
            value,
            re.IGNORECASE,
        ):
            fail(path, "Model-generated URLs and links are not allowed.")
        if re.search(
            r"[\u2e80-\u9fff\uac00-\ud7af\uf900-\ufaff\U00020000-\U0003134f]", value
        ):
            fail(path, "Model prose must be in English.")
        if re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", value):
            fail(path, "Control characters are not allowed.")

    def array(value, path, maximum=12, minimum=0):
        if not isinstance(value, list):
            fail(path, "Expected a JSON array; received " + type(value).__name__ + ".")
        if not minimum <= len(value) <= maximum:
            fail(path, f"Expected {minimum}-{maximum} items; received {len(value)}.")

    def ids(value, path, prefixes=None, maximum=20):
        array(value, path, maximum)
        seen = set()
        for identifier in value:
            if (
                not isinstance(identifier, str)
                or identifier not in catalog
                or identifier in seen
                or (prefixes and identifier[0] not in prefixes)
            ):
                fail(path, "Unknown, duplicate, or incompatible evidence ID.")
            seen.add(identifier)

    def assertion(value, path, editorial=False):
        obj(value, ("text", "basis", "evidence_ids"), path)
        string(value["text"], path + ".text")
        basis = value["basis"]
        if (
            not isinstance(basis, str)
            or basis not in BASES
            or (editorial and basis != "editorial_inference")
        ):
            fail(
                path,
                "Unsupported claim basis; this field may require editorial inference.",
            )
        ids(value["evidence_ids"], path + ".evidence_ids")
        if basis == "serp_observation" and (
            not value["evidence_ids"]
            or any(item[0] not in "PQKI" for item in value["evidence_ids"])
        ):
            fail(path, "SERP observations require direct observed evidence IDs.")
        if basis == "user_context" and (
            not value["evidence_ids"]
            or any(item not in {"U1", "B1"} for item in value["evidence_ids"])
        ):
            fail(path, "User context requires U1 or B1 evidence.")

    def assertions(value, path, editorial=False, maximum=12, minimum=0):
        array(value, path, maximum, minimum)
        for i, item in enumerate(value):
            assertion(item, path + "[" + str(i) + "]", editorial)

    try:
        obj(
            brief,
            (
                "executive_summary",
                "search_intent",
                "target_reader",
                "competitor_observations",
                "must_cover_topics",
                "differentiation_angles",
                "semantic_keywords",
                "article_plan",
                "eeat_recommendations",
                "meta",
                "faq",
                "cta",
                "internal_link_ids",
                "editor_checks",
            ),
            "brief",
        )
        for key in ("executive_summary", "target_reader", "cta"):
            assertion(brief[key], key)
        if (
            not evidence.get("audience")
            and brief["target_reader"]["basis"] != "editorial_inference"
        ):
            fail(
                "target_reader", "An unspecified audience requires editorial inference."
            )
        intent = brief["search_intent"]
        obj(intent, ("primary", "confidence", "rationale"), "search_intent")
        if intent["primary"] not in (
            "informational",
            "commercial",
            "transactional",
            "navigational",
            "mixed",
            "uncertain",
        ) or intent["confidence"] not in ("low", "medium", "high"):
            fail("search_intent", "Unsupported intent or confidence.")
        assertion(intent["rationale"], "search_intent.rationale")
        array(brief["competitor_observations"], "competitor_observations", 10)
        retained_competitors = []
        for item_index, item in enumerate(brief["competitor_observations"]):
            item_path = f"competitor_observations[{item_index}]"
            obj(item, ("source_id", "observations"), item_path)
            ids([item["source_id"]], item_path + ".source_id", "P")
            assertions(
                item["observations"],
                item_path + ".observations",
                maximum=5,
                minimum=1,
            )
            retained_observations = []
            for observation_index, observation in enumerate(item["observations"]):
                if item["source_id"] not in observation["evidence_ids"]:
                    if omit_unattributed:
                        continue
                    fail(
                        f"{item_path}.observations[{observation_index}].evidence_ids",
                        "The parent source is "
                        + item["source_id"]
                        + ". This observation does not cite that source. Recheck its title/snippet: rewrite and cite the parent only if supported, or remove the observation. Remove the competitor entry if no supported observations remain. Never add a citation without checking support.",
                    )
                retained_observations.append(observation)
            omitted = len(item["observations"]) - len(retained_observations)
            if omitted:
                normalization_warnings.append(
                    f"{omitted} competitor observation(s) for {item['source_id']} were omitted after repair because their citations did not include that page. Review the original sources before adding those claims."
                )
            if retained_observations:
                item["observations"] = retained_observations
                retained_competitors.append(item)
        brief["competitor_observations"] = retained_competitors
        assertions(
            brief["must_cover_topics"], "must_cover_topics", maximum=12, minimum=1
        )
        checks = brief["editor_checks"]
        if checks is None or checks == []:
            checks = [
                {
                    "text": instruction,
                    "basis": "editorial_inference",
                    "evidence_ids": [],
                }
                for instruction in (
                    "Review the original source pages before publishing; this brief uses search snippets only.",
                    "Verify factual, pricing, performance, and compliance claims independently before publishing.",
                    "Review the final draft for audience fit, accuracy, and useful source attribution.",
                )
            ]
            normalization_warnings.append(
                "Editor checks were empty; standard editorial review recommendations were added by the workflow."
            )
        elif isinstance(checks, (str, dict)):
            checks = [checks]
            normalization_warnings.append(
                "Editor checks were converted from a single entry to a list."
            )
        if not isinstance(checks, list):
            fail(
                "editor_checks",
                "Expected a JSON array, a review instruction, or a Claim object; received "
                + type(checks).__name__
                + ".",
            )
        normalized_checks = []
        for index, check in enumerate(checks):
            if isinstance(check, str):
                check = {
                    "text": check,
                    "basis": "editorial_inference",
                    "evidence_ids": [],
                }
                _warn(
                    normalization_warnings,
                    "Editor checks in plain text were labeled as editorial recommendations.",
                )
            assertion(check, f"editor_checks[{index}]")
            normalized_checks.append(check)
        if len(normalized_checks) > 10:
            normalization_warnings.append(
                f"Editor checks contained {len(normalized_checks)} valid items; the first 10 were retained."
            )
        brief["editor_checks"] = normalized_checks[:10]
        for key in ("differentiation_angles", "eeat_recommendations"):
            assertions(brief[key], key, True, maximum=8, minimum=1)
        array(brief["semantic_keywords"], "semantic_keywords", 15)
        for item in brief["semantic_keywords"]:
            obj(item, ("term", "basis", "evidence_ids"), "semantic_keywords.item")
            string(item["term"], "semantic_keywords.term")
            assertion(
                {
                    "text": item["term"],
                    "basis": item["basis"],
                    "evidence_ids": item["evidence_ids"],
                },
                "semantic_keywords.item",
            )
        plan = brief["article_plan"]
        obj(plan, ("format", "suggested_word_count", "outline"), "article_plan")
        string(plan["format"], "article_plan.format")
        count = plan["suggested_word_count"]
        obj(count, ("min", "max", "rationale"), "article_plan.suggested_word_count")
        if (
            type(count["min"]) is not int
            or type(count["max"]) is not int
            or not 1 <= count["min"] <= count["max"] <= 20000
        ):
            fail(
                "article_plan.suggested_word_count",
                "Expected positive integer word counts with min <= max <= 20000.",
            )
        assertion(
            count["rationale"], "article_plan.suggested_word_count.rationale", True
        )
        array(plan["outline"], "article_plan.outline", 8, 4)
        for item in plan["outline"]:
            obj(item, ("h2", "h3", "purpose"), "article_plan.outline.item")
            string(item["h2"], "article_plan.outline.h2")
            array(item["h3"], "article_plan.outline.h3", 3)
            for heading in item["h3"]:
                string(heading, "article_plan.outline.h3.item")
            assertion(item["purpose"], "article_plan.outline.purpose")
        obj(brief["meta"], ("title", "description", "slug"), "meta")
        for key, limit in (("title", 2000), ("description", 2000), ("slug", 2000)):
            string(brief["meta"][key], "meta." + key, limit)
        if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", brief["meta"]["slug"]):
            fail("meta.slug", "Use lowercase English words and hyphens only.")
        faq = brief["faq"]
        obj(faq, ("observed_paa", "suggested_questions"), "faq")
        array(faq["observed_paa"], "faq.observed_paa", 10)
        for item in faq["observed_paa"]:
            obj(item, ("question_id", "answer_direction"), "faq.observed_paa.item")
            ids([item["question_id"]], "faq.question_id", "Q")
            assertion(item["answer_direction"], "faq.answer_direction", True)
        array(faq["suggested_questions"], "faq.suggested_questions", 5)
        for item in faq["suggested_questions"]:
            obj(item, ("question", "rationale"), "faq.suggested_questions.item")
            string(item["question"], "faq.suggested_questions.question")
            assertion(item["rationale"], "faq.suggested_questions.rationale", True)
        ids(brief["internal_link_ids"], "internal_link_ids", "I", 5)
    except (ValueError, TypeError, KeyError, RecursionError) as error:
        message = (
            str(error)
            if isinstance(error, ValueError)
            else "Brief contains an invalid field type or structure."
        )
        return {"valid": False, "brief": {}, "errors": message[:1000]}
    return {
        "valid": True,
        "brief": brief,
        "errors": "",
        "warnings": normalization_warnings,
    }


def _markdown(value):
    value = str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    value = re.sub(r"([\\`*_{}\[\]()#+.!|~-])", r"\\\1", value)
    return " ".join(value.split())


def _source_link(identifier, catalog):
    item = catalog[identifier]
    url = _url(item.get("url", item.get("source_url")))
    return (
        "[" + identifier + "][source-" + identifier + "]"
        if url
        else "[" + identifier + "]"
    )


def render_brief(brief: dict, evidence: dict) -> dict:
    validated = validate_brief(_json(brief), evidence)
    if not validated["valid"]:
        return failure_result("generation_failed", "brief_validation_failed", evidence)
    brief = validated["brief"]
    catalog = _catalog(evidence)

    def claim(value):
        sources = " ".join(
            _source_link(identifier, catalog) for identifier in value["evidence_ids"]
        )
        labels = {
            "serp_observation": "SERP observation",
            "editorial_inference": "Editorial inference",
            "user_context": "User context",
        }
        return (
            _markdown(value["text"])
            + " *("
            + labels[value["basis"]]
            + ")*"
            + (" " + sources if sources else "")
        )

    def claim_list(values):
        return ["- " + claim(value) for value in values] or [
            "No recommendations available from the supplied evidence."
        ]

    lines = [
        "# SEO Content Brief",
        "",
        "> SERP-only evidence: titles, snippets, observed questions, and related queries. Original page text, competitor word counts, and search volume were not retrieved.",
        "",
        "Query: " + _markdown(evidence.get("query", "")),
        "Market: " + _markdown(evidence.get("market", "")),
        "Observed at: " + _markdown(evidence.get("observed_at", "")),
        "",
    ]
    if evidence.get("quality") == "limited":
        lines.extend(
            [
                "> Limited sample: fewer than three valid organic results. Treat conclusions as provisional.",
                "",
            ]
        )
    lines.extend(
        [
            "## 1. Executive summary",
            "",
            claim(brief["executive_summary"]),
            "",
            "## 2. Search intent and target reader",
            "",
            "Intent: "
            + brief["search_intent"]["primary"]
            + " | Confidence: "
            + brief["search_intent"]["confidence"],
            claim(brief["search_intent"]["rationale"]),
            "",
            "Target reader: " + claim(brief["target_reader"]),
            "",
            "## 3. Competitor observations",
            "",
        ]
    )
    if not brief["competitor_observations"]:
        lines.extend(
            [
                "No individually validated competitor observations are included. Use the source table for manual review.",
                "",
            ]
        )
    for item in brief["competitor_observations"]:
        lines.extend(
            [
                "### "
                + item["source_id"]
                + ": "
                + _markdown(catalog[item["source_id"]]["title"]),
                "",
            ]
            + claim_list(item["observations"])
            + [""]
        )
    for heading, key in (
        ("4. Must-cover topics", "must_cover_topics"),
        ("5. Differentiation angles", "differentiation_angles"),
    ):
        lines.extend(["## " + heading, ""] + claim_list(brief[key]) + [""])
    lines.extend(["## 6. Semantic keywords", ""])
    if not evidence.get("related"):
        lines.extend(
            [
                "No related searches were returned."
                if "No related searches were returned." in evidence.get("warnings", [])
                else "No related searches are available in the retained evidence.",
                "The keywords below use other cited evidence or editorial inference.",
                "",
            ]
        )
    for item in brief["semantic_keywords"]:
        lines.append(
            "- "
            + claim(
                {
                    "text": item["term"],
                    "basis": item["basis"],
                    "evidence_ids": item["evidence_ids"],
                }
            )
        )
    plan = brief["article_plan"]
    counts = plan["suggested_word_count"]
    lines.extend(
        [
            "",
            "## 7. Article plan",
            "",
            "Format: " + _markdown(plan["format"]),
            "Suggested word count (editorial estimate): "
            + str(counts["min"])
            + "-"
            + str(counts["max"]),
            claim(counts["rationale"]),
            "",
        ]
    )
    for item in plan["outline"]:
        lines.extend(
            ["### H2: " + _markdown(item["h2"]), "", claim(item["purpose"])]
            + ["- H3: " + _markdown(h) for h in item["h3"]]
            + [""]
        )
    lines.extend(
        ["## 8. E-E-A-T recommendations", ""]
        + claim_list(brief["eeat_recommendations"])
        + [
            "",
            "## 9. Metadata",
            "",
            "- Title: " + _markdown(brief["meta"]["title"]),
            "- Description: " + _markdown(brief["meta"]["description"]),
            "- Slug: " + _markdown(brief["meta"]["slug"]),
            "",
            "## 10. FAQ",
            "",
            "### Observed People Also Ask questions",
            "",
        ]
    )
    if not brief["faq"]["observed_paa"]:
        if evidence.get("paa"):
            lines.append("Observed PAA questions were returned but none were selected.")
        elif "No People Also Ask questions were returned." in evidence.get(
            "warnings", []
        ):
            lines.append("No People Also Ask questions were returned.")
        else:
            lines.append(
                "No observed PAA questions are available in the retained evidence."
            )
    for item in brief["faq"]["observed_paa"]:
        lines.extend(
            [
                "- "
                + _markdown(catalog[item["question_id"]]["question"])
                + " "
                + _source_link(item["question_id"], catalog),
                "  - Answer direction: " + claim(item["answer_direction"]),
            ]
        )
    lines.extend(["", "### Editorial question suggestions", ""])
    for item in brief["faq"]["suggested_questions"]:
        lines.extend(
            ["- " + _markdown(item["question"]), "  - " + claim(item["rationale"])]
        )
    lines.extend(
        ["", "## 11. Call to action and internal links", "", claim(brief["cta"]), ""]
    )
    if not brief["internal_link_ids"]:
        lines.append("No internal links selected from verified site-search candidates.")
    for identifier in brief["internal_link_ids"]:
        lines.append(
            "- "
            + _markdown(catalog[identifier]["title"])
            + " "
            + _source_link(identifier, catalog)
        )
    lines.extend(
        ["", "## 12. Editor checks", ""]
        + claim_list(brief["editor_checks"])
        + [
            "",
            "## Sources",
            "",
            "Source titles and questions below are quoted as observed and may use another language.",
            "",
            "| ID | Observed source or context | Position | Link |",
            "| --- | --- | --- | --- |",
        ]
    )
    for identifier, item in catalog.items():
        label = item.get(
            "title", item.get("question", item.get("query", item.get("text", "")))
        )
        position = str(item["rank"]) if item.get("rank") is not None else "Not provided"
        lines.append(
            "| "
            + identifier
            + " | "
            + _markdown(label)
            + " | "
            + position
            + " | "
            + _source_link(identifier, catalog)
            + " |"
        )
    lines.append("")
    for identifier, item in catalog.items():
        url = _url(item.get("url", item.get("source_url")))
        if url:
            lines.append(
                "[source-"
                + identifier
                + "]: <"
                + quote(url, safe=":/?&=%;@+$,!~*'[]-#")
                + ">"
            )
    markdown = "\n".join(lines)
    if len(markdown) >= 400000:
        return failure_result("generation_failed", "brief_validation_failed", evidence)
    return {
        "status": "limited_evidence"
        if evidence.get("quality") == "limited"
        else "completed",
        "brief_markdown": markdown,
        "brief_json": brief,
        "evidence_json": _json(evidence),
        "warnings": (
            list(evidence.get("warnings", [])) + validated.get("warnings", [])
        )[-30:],
        "error_code": "",
    }


def failure_result(status: str, error_code: str, evidence: dict | None = None) -> dict:
    allowed_status = {
        "invalid_input",
        "search_failed",
        "no_results",
        "generation_failed",
    }
    allowed_codes = {
        "invalid_input",
        "serp_tool_error",
        "invalid_serp_response",
        "no_results",
        "evidence_too_large",
        "llm_call_failed",
        "brief_validation_failed",
    }
    return {
        "status": status if status in allowed_status else "generation_failed",
        "brief_markdown": "",
        "brief_json": {},
        "evidence_json": _json(evidence) if isinstance(evidence, dict) else "{}",
        "warnings": list(evidence.get("warnings", []))
        if isinstance(evidence, dict)
        else [],
        "error_code": error_code
        if error_code in allowed_codes
        else "brief_validation_failed",
    }


def prepare_run(keyword, market, audience="", brand_context="", site_domain="") -> dict:
    validated = validate_input(
        keyword,
        market,
        "" if audience is None else audience,
        "" if brand_context is None else brand_context,
        "" if site_domain is None else site_domain,
    )
    if not validated["valid"]:
        return {
            "valid": False,
            "config": {},
            "search_options": "{}",
            "result_json": _json(failure_result("invalid_input", "invalid_input")),
        }
    config = validated["config"]
    config["gl"] = "gb" if config["market"] == "gb_en" else "us"
    return {
        "valid": True,
        "config": config,
        "search_options": _json({"gl": config["gl"], "hl": "en"}),
        "result_json": "",
    }


def prepare_search(result, config) -> dict:
    normalized = normalize_serp(result, config)
    if not normalized["can_generate"]:
        code = normalized["error_code"]
        final = failure_result(
            "no_results" if code == "no_results" else "search_failed",
            code,
            normalized["evidence"],
        )
        return {
            "action": "finish",
            "evidence_json": _json(normalized["evidence"]),
            "result_json": _json(final),
        }
    assembled = assemble_evidence(
        normalized["evidence"], {"status": "skipped", "candidates": [], "warnings": []}
    )
    if not assembled["ready"]:
        final = failure_result(
            "no_results"
            if assembled["error_code"] == "no_results"
            else "search_failed",
            assembled["error_code"],
            assembled["evidence"],
        )
        return {
            "action": "finish",
            "evidence_json": assembled["evidence_json"],
            "result_json": _json(final),
        }
    return {
        "action": "site" if config.get("site_domain") else "generate",
        "evidence_json": assembled["evidence_json"],
        "result_json": "",
    }


def finish_site(result, config, evidence_json) -> dict:
    try:
        evidence = json.loads(
            evidence_json,
            parse_constant=_reject_constants,
            object_pairs_hook=_unique_object,
        )
        if not isinstance(evidence, dict) or not isinstance(
            evidence.get("organic"), list
        ):
            raise TypeError("Invalid evidence transport")
        site = normalize_site(result, config)["site"]
        assembled = assemble_evidence(evidence, site)
    except (ValueError, TypeError, KeyError, RecursionError):
        return {
            "ready": False,
            "evidence_json": "{}",
            "result_json": _json(
                failure_result("generation_failed", "brief_validation_failed")
            ),
        }
    final = ""
    if not assembled["ready"]:
        final = _json(
            failure_result(
                "no_results"
                if assembled["error_code"] == "no_results"
                else "search_failed",
                assembled["error_code"],
                assembled["evidence"],
            )
        )
    return {
        "ready": assembled["ready"],
        "evidence_json": assembled["evidence_json"],
        "result_json": final,
    }


def finish_generation(text, evidence_json, allow_repair=True) -> dict:
    def final_output(final, errors=""):
        if errors:
            final["warnings"] = list(final.get("warnings", []))[:29] + [
                "Brief validation failed: " + errors[:1000]
            ]
        serialized = _json(final)
        if len(serialized) >= 400000:
            final = failure_result("generation_failed", "brief_validation_failed")
            final["warnings"] = ["Brief output exceeds the workflow size limit."]
            serialized = _json(final)
        return {"needs_repair": False, "errors": errors, "result_json": serialized}

    try:
        evidence = json.loads(
            evidence_json,
            parse_constant=_reject_constants,
            object_pairs_hook=_unique_object,
        )
        if not isinstance(evidence, dict) or not isinstance(
            evidence.get("organic"), list
        ):
            raise TypeError("Invalid evidence transport")
        if not isinstance(text, str) or text == "__DIFY_MODEL_CALL_FAILED__":
            return final_output(
                failure_result("generation_failed", "llm_call_failed", evidence)
            )
        validated = validate_brief(text, evidence, omit_unattributed=not allow_repair)
        if not validated["valid"]:
            if not allow_repair:
                return final_output(
                    failure_result(
                        "generation_failed", "brief_validation_failed", evidence
                    ),
                    validated["errors"],
                )
            return {
                "needs_repair": True,
                "errors": validated["errors"],
                "result_json": "",
            }
        final = render_brief(validated["brief"], evidence)
        omissions = [
            warning
            for warning in validated.get("warnings", [])
            if "were omitted after repair" in warning
        ]
        if omissions and final["brief_markdown"]:
            final["brief_markdown"] = (
                "> Editorial review required: "
                + " ".join(omissions)
                + "\n\n"
                + final["brief_markdown"]
            )
        for warning in validated.get("warnings", []):
            if warning not in final["warnings"]:
                final["warnings"] = final["warnings"][-29:] + [warning]
        return final_output(final)
    except (ValueError, TypeError, KeyError, RecursionError):
        return {
            "needs_repair": False,
            "errors": "Invalid evidence transport.",
            "result_json": _json(
                failure_result("generation_failed", "brief_validation_failed")
            ),
        }


def unpack_result(result_json) -> dict:
    fallback = failure_result("generation_failed", "brief_validation_failed")
    try:
        result = json.loads(
            result_json,
            parse_constant=_reject_constants,
            object_pairs_hook=_unique_object,
        )
        if not isinstance(result, dict) or set(result) != set(fallback):
            return fallback
        if any(
            not isinstance(result[key], str)
            for key in ("status", "brief_markdown", "evidence_json", "error_code")
        ):
            return fallback
        if not isinstance(result["brief_json"], dict) or not isinstance(
            result["warnings"], list
        ):
            return fallback
        if any(not isinstance(warning, str) for warning in result["warnings"]):
            return fallback
        return result
    except (ValueError, TypeError, RecursionError):
        return fallback
