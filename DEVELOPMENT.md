# Development notes

## Scope

This implementation follows the SERP-only design approved for this repository. The later English-only instruction takes precedence over the earlier design's Chinese output option: the workflow has no output-language selector and supports `us_en` and `gb_en` market presets. No plugin, SDK, backend, or worktree changes are required.

## Source layout

| File | Responsibility |
| --- | --- |
| `thordata-seo-content-brief.yml` | Standalone Dify import artifact. |
| `build_ifpkg.py` | Builds the Creator Center `.ifpkg` app package from the DSL. |
| `build_workflow.py` | Graph, node configuration, JSON contract, and deterministic generation. |
| `code/runtime.py` | Input validation, response normalization, evidence limits, brief validation, rendering, and safe failures. Standard library only. |
| `code/brief-schema.json` | Generated model-output contract for review and tooling. |
| `prompts/brief-system.txt` | English editorial and evidence rules. |
| `tests/test_runtime.py` | Data and rendering contracts using saved real responses and edge cases. |
| `tests/test_compact.py` | Composed processing stages, transport size limits, and error contract preservation. |
| `tests/test_workflow.py` | Graph reachability, request bounds, language checks, embedded Python, and generation drift. |
| `tests/test_execution.py` | Runs the actual exported Code blocks across success, failure, and repair paths with recorded network boundaries. |
| `tests/verify_dify.py` | Optional checks using Dify's actual graphon node schemas, Code output validator, and Variable Aggregator. |
| `tests/fixtures/serp-observations.json` | Bundled SERP evidence for the runtime and execution tests. |
| `tests/fixtures/google-web-gb.json` | Additional real English UK response used to validate the new market preset. |

The builder embeds the transitive dependencies of each runtime entry point into its Code node. The YAML has no external Python imports from this repository, no network access from Code nodes, and no credential literals. Change the source files, regenerate the YAML, and run the checks; do not edit the generated Python blocks by hand.

## Execution structure

Inputs → prepare search → native Google search → prepare evidence → optional internal-link search → generate brief → validate and format → optional repair → unified output.

The compact graph has 18 nodes, 25 fewer than the original 43. Separate US/UK tool branches are replaced by a controlled `extra_parameters_json` template. Normalization, optional-feature diagnostics, evidence bounding, and failure packaging share processing nodes. Validation and rendering also share a node. All terminal outcomes join a single End node.

The canvas places the 13 main-path nodes on one horizontal baseline. The three internal-link nodes and two repair nodes sit 400 units below it, in reserved columns before their merge points. All edges advance to the right. Short English node descriptions explain purpose and branching behavior; these layout fields do not change execution.

Tool failures use Dify's `default-value` strategy with `result={}`. LLM failures use the reserved `__DIFY_MODEL_CALL_FAILED__` text marker. These fallbacks are recognized by the processing nodes and preserve the existing safe failure statuses; raw tool errors are never forwarded to the model. Optional site errors preserve the main evidence with a warning. A model API failure ends immediately; a successful but empty or malformed model response still receives one repair. There are no automatic retries.

Evidence and final outcomes travel between composed stages as JSON strings. The output node unpacks the final outcome into the original six fields; `brief_json` remains an object and `warnings` remains an array. Evidence is bounded before generation, and the complete serialized final outcome is capped below Dify's 400,000-character string limit.

Suggested word counts must be positive ordered integers no greater than 20,000. The model schema and runtime enforce the same editorial bound, preventing unbounded model integers from reaching Dify's numeric output limits.

Markdown uses reference-style citations so each source URL is stored only once, even when many claims cite it. The renderer also checks the final 400,000-character Dify limit and returns a structured generation failure if it is exceeded.

Aggregator selector order is significant. Research context prefers the completed site-enriched evidence over the earlier primary evidence. Final result prefers the repair outcome, then the first generation outcome, then site/evidence/input failures. The node is reached only after a terminal decision; later executed stages must precede earlier stages whose empty interim values still exist in Dify's variable pool. Offline tests exercise original success, repair success/failure, both tool failures, absent site input, invalid input, empty search, and limited evidence.

## Compatibility evidence

- Graph and node definitions are checked against the locally available Dify 1.17.1 `graphon` source and Pydantic models.
- Dify Code output validation defaults are depth 5, string length 400,000, object-array length 30, and string-array length 30. Nested brief objects are checked against these actual validators; lists of primitive strings do not increase object depth in the same way as nested objects.
- Thordata dependency `0.1.1@97c8705a29df75009d5caf4daa1f3b609fb1dbac62a1214a74a1f18bf710732f` was read from its public Marketplace page.
- OpenAI dependency `1.1.0@1ded02acee56efd5788c86fd447d792e4923e959554a4942f440c7859d164340` was read from its public Marketplace page, which declares minimum Dify 1.0.0.
- Tool identity and output selection follow the existing repository's real Dify-exported `thordata-serp-workflow.yml` example. The actual credential binding is resolved in the importing Dify workspace.

## Verification boundaries

The original research validated six successful production SERP requests through the local plugin service. The additional UK request on 2026-11-17 returned HTTP 200 with ten natural results and the expected `engine=google_web`, `gl=gb`, `hl=en` echo. Its observed elapsed time was 4.375 seconds; this single measurement is not a performance guarantee.

Automated checks validate data contracts, graph structure, generated code, and Dify node/output types. They do not prove model quality or successful execution in a configured Dify deployment. A live Dify deployment and OpenAI credential are not bundled with this repository, so a model-backed end-to-end preview requires the importing workspace's configured model.

To repeat compatibility checks, run `tests/verify_dify.py` with the Python interpreter from a Dify 1.17.1 environment. It requires `graphon` and PyYAML but no running service or credentials. Point `--plugin-path` at a Thordata Dify plugin checkout if the `google_web` tool descriptor is not in a sibling `thordata-serp-plugin` directory:

```powershell
python -B tests/verify_dify.py --dify-api C:\path\to\dify\api --plugin-path C:\path\to\dify-plugin-thordata-serp
```

The verifier reports the count of validated nodes, executed Code outputs, aggregator scenarios, and the exact YAML SHA-256. It uses saved search results and deterministic synthetic model output; it does not call an LLM or claim to evaluate the model's reasoning.

## Review before release

1. Import the YAML into the target Dify workspace and resolve both plugin dependencies.
2. Bind the Thordata credential to both tool nodes and the model credential to both model nodes.
3. Preview both markets, with and without a site domain, and inspect the request echoes.
4. Exercise an empty search result and an unavailable optional site search; verify no fabricated brief or link appears.
5. Inspect the full English brief, citation IDs, URLs, PAA provenance, and editorial word-count language.
6. Export and re-import into a separate application to confirm deployment-specific dependency and credential compatibility.

Credentials stay in Dify provider settings. Never put tokens in the graph, prompts, screenshots, fixtures, command arguments, or committed files.
