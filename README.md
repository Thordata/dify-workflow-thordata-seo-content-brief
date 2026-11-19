# SEO Content Brief Generator — Thordata

Give your editorial team a content brief grounded in what actually ranks: this template pulls live Google results through the [Thordata SERP API](https://www.thordata.com/products/serp-api?op=cianna&from=blog) inside Dify — no crawler, no SERP scraper to maintain, one API key. New to wiring Thordata into Dify? Start from the [Thordata for Dify integration](https://www.thordata.com/products/serp-api/dify-integration?op=cianna&from=dify).

Generate an English SEO content brief from live Google results using the published Thordata Dify plugin. The workflow turns actual ranking titles, snippets, People Also Ask questions, and related searches into a brief for a human editor, with source references and optional internal-link discovery.

**Import [thordata-seo-content-brief.yml](./thordata-seo-content-brief.yml) into Dify.** This file is standalone: the Python code and prompts are embedded, so no repository files need to be mounted in Dify.

The compact edition has **18 nodes instead of 43**. It preserves the same inputs, output fields, source validation, optional internal links, and single repair attempt. Search cards show only three configuration fields, and the canvas uses two reading rows with optional side branches.

## What it produces

- Search intent and a suggested target reader.
- Competitor observations grounded in titles and search snippets.
- Topics, potential differentiation angles, and semantic keywords.
- A recommended H2/H3 outline and an editorial word-count estimate.
- E-E-A-T recommendations, metadata drafts, FAQ directions, and a CTA.
- Actual source links, optional internal-link candidates, and editor checks.

The evidence is **SERP only**. The workflow does not fetch article bodies, measure competitor word counts, or retrieve search volume, keyword difficulty, CPC, or backlinks. Potential gaps and suggested headings are editorial recommendations, not claims about the complete contents of competitor pages. Actual PAA questions and suggested questions are presented separately.

## Get your Thordata API key

1. [Create a Thordata account](https://www.thordata.com/?ls=community&lk=proxyip) and open the dashboard.
2. Copy a SERP API key with search permission and available balance.
3. In Dify, paste that key into the Thordata plugin's credential settings — the same key powers the **Search Google** and **Find internal links** nodes.

## Install and configure

1. Use a Dify installation with Python Code nodes, plugin support, If/Else nodes, Variable Aggregators, and default-value error handling. The DSL targets Dify 1.17.1 / DSL 0.7.0.
2. In Studio, import the YAML file using **Import DSL file**.
3. Install or resolve these Marketplace dependencies when prompted:
   - [Thordata SERP](https://marketplace.dify.ai/plugin/thordata/thordata_serp), pinned to the verified public 0.1.1 identifier.
   - [OpenAI](https://marketplace.dify.ai/plugin/langgenius/openai), pinned to the verified public 1.1.0 identifier.
4. Authorize Thordata in the plugin credential settings with your SERP API token. Bind the authorized credential to **Search Google** and **Find internal links** if Dify asks. Both nodes receive the selected market automatically.
5. Configure an OpenAI credential with access to `gpt-4o`, used in **Generate SEO brief** and **Repair once**. To use another model, change both nodes and verify that it follows the supplied JSON contract.
6. Ensure the Dify sandbox and plugin execution services are running. Allow enough tool execution time for the Thordata plugin, whose current service timeout is 120 seconds.
7. Run a preview with the example below. Review the returned status and sources before using the draft.

No API token, model key, or environment secret is included in the DSL. Saving a Thordata credential can make a separate validation search; that is outside the workflow's search count. A newer installed Thordata package may satisfy the same tool contract, but dependency resolution and credentials must be checked in the target workspace.

### Model not found after import

If Dify reports `Model gpt-4o not exist.` or marks a model as incompatible, the imported provider/model pair is unavailable in your workspace. The template's model selection is a configuration default, not an entitlement to that model.

Open **Generate SEO brief**, select a model from your configured provider's available models, and do the same for **Repair once**. Changing one node does not update the other. If the repair node already has a valid selection, use the same provider and model in the generation node. Select the full provider/model pair in Dify rather than only editing the model-name string in YAML. Review model-specific parameters, wait for autosave, then run the preview again.

This configuration error can be raised while Dify resolves the model, before node-level error handling runs. Default-value handling does not make an unavailable model runnable. These changes can be made directly in the existing imported application; re-importing is unnecessary.

### Brief validation failed

`generation_failed` with `brief_validation_failed` means no validated brief could be returned. Successful search evidence can still be present. Missing PAA or related searches are informational warnings and do not cause validation failure.

For a failed model response, check the `errors` output of **Validate and format** and **Finalize repaired brief** in Dify's trace. The updated workflow also includes the final safe validation reason in `warnings`, prefixed with `Brief validation failed:`. It reports field paths and missing required fields without exposing the model response. Output-size failures have their own warning.

Typical checks cover valid JSON, required fields, evidence IDs, English prose, and the required object/array shapes. The workflow tries one repair after a malformed response; it does not return unvalidated text as a successful brief. A chosen model must follow the JSON contract. Changing the model is optional; inspect the actual validation reason first. If JSON is incomplete, also inspect the model node for output truncation.

Editor review recommendations have limited format tolerance: plain text entries or a single Claim become a list, an empty/null list receives explicitly disclosed standard review recommendations, and more than ten valid entries are reduced to the first ten. All supplied entries are checked before trimming, including their source IDs and text. `warnings` records these adjustments. Other fields retain their schema and citation requirements. Array errors now distinguish a wrong type from an out-of-range count and report the expected and actual counts.

For competitor observations, each claim must cite the page named by its parent `source_id`. The first validation requests a targeted repair with the exact entry index and parent ID. After the one repair attempt, an otherwise valid observation that still cites only other known sources is omitted; it is never automatically assigned a new citation. Empty competitor entries are removed. The brief displays an editorial-review notice and `warnings` lists the affected page IDs. Unknown IDs, invalid text, malformed fields, and unsupported claim bases still fail validation. This checks citation attribution, not whether a model's interpretation is factually correct.

To update an existing compact 18-node application without resetting its models or credentials, replace the entire Python code in **Validate and format** with [validate-and-format.py](./patches/validate-and-format.py), and replace the entire Python code in **Finalize repaired brief** with [finalize-repaired-brief.py](./patches/finalize-repaired-brief.py). Keep each node's existing `text` and `evidence_json` inputs, outputs, and connections. These generated patches match the current YAML code exactly. This updates validation and repair-result handling; importing the complete YAML also updates the model prompts. The patch files are generated artifacts, so edit `code/runtime.py` and rebuild rather than editing them directly.

## Inputs

| Input | Required | Description |
| --- | --- | --- |
| `keyword` | Yes | One target query, 1–200 characters after trimming. |
| `market` | Yes | `us_en` for United States/English or `gb_en` for United Kingdom/English. Default: `us_en`. |
| `audience` | No | Intended readers, up to 500 characters. An omitted audience is proposed as an editorial inference. |
| `brand_context` | No | Product positioning and writing goals, up to 3000 characters. This is user-supplied context, not independently verified evidence. |
| `site_domain` | No | A plain domain such as `thordata.com`, up to 253 characters. No protocol, path, port, or search operator. Enables one additional site search. |

All interface text, prompts, and generated editorial prose are English. Original source titles and questions are preserved as observed; an externally supplied non-English query can therefore produce non-English source quotations.

Example:

```json
{
  "keyword": "best serp api for seo",
  "market": "us_en",
  "audience": "SEO managers and developers evaluating search data providers",
  "brand_context": "Create an educational comparison brief. Explain integration considerations and require the writer to verify any pricing or performance claims.",
  "site_domain": "thordata.com"
}
```

Both searches use the native `google_web` tool. **Prepare search** produces a controlled JSON object with `gl=us` or `gl=gb` and `hl=en`, which is passed through the tool's supported **Extra Parameters JSON** field. Users do not need to edit JSON or duplicate nodes to switch markets. Both tools set `num=10` and `start=0`; desktop and `google.com` use the plugin defaults. Location and UULE overrides are unset.

To update an earlier imported copy, import this YAML as a new application and rebind credentials. Updating a local YAML file does not modify an application already saved in Dify.

## Outputs

| Output | Type | Meaning |
| --- | --- | --- |
| `status` | string | `completed`, `limited_evidence`, `invalid_input`, `search_failed`, `no_results`, or `generation_failed`. |
| `brief_markdown` | string | Human-readable brief with citations and source table; empty on failure. |
| `brief_json` | object | Validated structured brief; `{}` on failure. |
| `evidence_json` | string | JSON-encoded normalized evidence for inspection or downstream storage. Parse it once when an object is needed. |
| `warnings` | array of strings | Missing data, filtered results, truncation, limited samples, or optional site-search problems. |
| `error_code` | string | Empty on success, otherwise a safe machine-readable failure code. |

One or two usable natural results produce `limited_evidence`. Zero usable results produce `no_results` without invoking the model. A request for ten results can return fewer than ten; the workflow preserves the observed count and original ranks.

Model-generated source references are validated against the current evidence. The model never supplies source URLs. URLs, source titles, and actual PAA questions are inserted by deterministic rendering from the retrieved data. This validates the reference structure; editorial correctness still requires human review.

## Request limits and failure handling

- Without `site_domain`: one SERP search. With it: at most two searches.
- One model call normally; at most one additional call to repair invalid JSON or references.
- No automatic retries, keyword expansion, pagination, or full-page crawling.
- Main search errors stop generation and produce a safe `search_failed` result.
- Optional site-search errors preserve the main brief, add a warning, and supply no fabricated internal links.
- Model API errors stop with `generation_failed`. A successful model response that fails validation gets one repair attempt; a second invalid response fails cleanly.
- Evidence sent to the model is bounded to 24,000 characters. Truncation is recorded, URLs are not shortened, and references remain consistent.

PAA and related searches are optional SERP features. Missing PAA is not replaced with invented collected questions. When PAA contains only question text, any answer direction is explicitly an editorial suggestion.

## Development and verification

All deliverables live in this repository. See [DEVELOPMENT.md](./DEVELOPMENT.md) for architecture and verification details.

Run from the repository root with Python 3.12+ and the packages in `requirements-dev.txt`:

```powershell
python -m venv .venv
.venv\Scripts\pip install -r requirements-dev.txt
.venv\Scripts\python build_workflow.py
.venv\Scripts\python build_workflow.py --check
.venv\Scripts\python -m pytest tests -q
```

These checks use bundled SERP fixtures and do not make paid requests. The recorded Google response used by the runtime and execution tests is in `tests/fixtures/serp-observations.json`; the UK sample is in `tests/fixtures/google-web-gb.json`.

## More from Thordata

- [Thordata SERP API](https://www.thordata.com/products/serp-api?op=cianna&from=blog) — the live ranking titles, snippets, People Also Ask, and related searches this brief is built from.
- [Thordata for Dify](https://www.thordata.com/products/serp-api/dify-integration?op=cianna&from=dify) — plugin setup and the full tool list.
- [LangChain](https://www.thordata.com/products/serp-api/langchain-integration?op=cianna&from=rename) and [n8n](https://www.thordata.com/products/serp-api/n8n-integration?op=cianna&from=n8n) integrations, plus the [Thordata MCP server](https://www.thordata.com/products/serp-api/mcp-server?op=cianna&from=mcp) for the same data in other agents.
- [SERP monitoring](https://www.thordata.com/solutions/serp-monitoring?op=cianna&from=community) — keep watching the keywords and competitors behind each brief over time.

## References

- [Thordata SERP plugin](https://marketplace.dify.ai/plugin/thordata/thordata_serp) — [source repository](https://github.com/Thordata/dify-plugin-thordata-serp).
- [Reference SEO template](https://marketplace.dify.ai/template/south_0120/7862792c-04ae-41a2-a1f6-d37c1321f09d)
- [Bundled test fixtures](./tests/fixtures/) — recorded and derived SERP responses used by the offline checks.

