# Smriti local fallback — Mac mini M4 / 16 GB

## Decision

Build a **serial specialist harness**, not a single VLM and not a resident multi-model MoE.

Why: 16 GB is LM Studio's recommended Apple-Silicon tier, but unified memory also holds macOS, runtime state, image tensors, and KV cache.[1] Keeping a vision reader, OCR model, and math model loaded together is weak design: it trades scarce memory for zero benefit because one request only needs one specialist at a time.

Use one loaded model at a time, at 4K–8K context initially. Keep the source library, index, validators, and deterministic renderer permanently resident; evict the specialist after an idle TTL if memory pressure appears.

## Recommended model roles

| Role | Model / format | Load when | Output contract | Why |
|---|---|---|---|---|
| **Planner / controller** | `Qwen/Qwen3-4B-GGUF`, begin Q5_K_M (2.89 GB weights) | Every non-direct request | Closed `SmritiPlan` JSON only | Qwen3 supports thinking/non-thinking modes and tool-oriented use; use **non-thinking** for quick plan emission.[4][5] |
| **Math and LaTeX worker** | `Qwen/Qwen2.5-Math-7B-Instruct`; community GGUF Q4_K_M (4.68 GB weights) | derivation, symbolic reasoning, complex equation explanation | `MathResult` JSON: assumptions, steps, equations, checks, uncertainties | Official card positions it for mathematical solving/tool-integrated reasoning; do not use it as the action/controller model.[6][7] |
| **Fast visual reader** | `Qwen/Qwen2.5-VL-3B-Instruct`; `ggml-org` Q4_K_M (1.93 GB) plus projector | handwritten rM2 crop, formula crop, diagram question | `OCRResult` JSON: lines, boxes, alternatives, confidence; LaTeX only for detected equations | General VLM suitable for visual Q&A/OCR; it is a reader, not proof authority.[8][9] |
| **Page-layout OCR cross-check** | `DeepSeek-OCR`; `ggml-org` Q8_0 (3.13 GB) + projector | full textbook/scan page, failed layout extraction | page Markdown plus line/region evidence | Its upstream prompts cover free OCR and document-to-Markdown conversion.[10][11] |
| **Visual escalation** | `Qwen/Qwen2.5-VL-7B-Instruct`; `ggml-org` Q4_K_M (4.68 GB) + projector | low confidence, VLM disagreement, complex handwritten equation/diagram | same `OCRResult`, with alternatives mandatory | Use only after a confidence failure. Conservative context, one crop/page.[12][13] |

**Do not download every model first.** Start with controller + 3B visual reader. Add the Math 7B only after benchmark. Add DeepSeek-OCR only if textbook layout extraction fails. This preserves simple failure diagnosis.

### Deliberate exclusions

- **PaddleOCR-VL:** attractive formula/document specialist, but keep it out of the LM Studio core until its supported GGUF/MLX deployment is verified. A separate native service is acceptable later.
- **Community math fine-tunes:** not trusted as primary. Their quant files are convenient, but model-specific claims and reproducibility are weaker than upstream cards.
- **Automatic tool authority:** no local model gets shell, filesystem, networking, or tablet control. Model emits an intent; app validates it; deterministic code executes it.

## Textbook-first source policy

Prescribed material is first-class evidence, not optional RAG garnish.

```text
handwritten selection / typed question / page crop
             |
             v
  deterministic router ────────────> direct plot / TeX / circuit action
             |
      OCR specialist (when image)
             |
             v
  course-library retrieval, page-bound chunks
    1. selected textbook page/crop
    2. prescribed book passages
    3. explicitly approved references
    4. parametric knowledge, only marked fallback
             |
             v
  evidence gate ─> local planner (strict JSON Schema)
             |
             v
  citation validator ─> deterministic renderer / plot engine
```

Every indexed chunk carries immutable provenance:

```json
{
  "chunk_id": "oppenheim-3e:p245:c03",
  "book_id": "oppenheim-3e",
  "edition": "3rd",
  "pdf_page": 263,
  "printed_page": "245",
  "bbox": [72, 414, 1470, 582],
  "text": "...",
  "source_sha256": "..."
}
```

Rules enforced by code:

1. An explanatory claim needs `source_refs`, unless it is a labeled `derived_from_user_work` or `parametric_fallback`.
2. Citation IDs must come from the exact retrieval result and resolve to the stored book, edition, page, and region. Model cannot invent them.
3. If retrieval is weak, reply `need_source_or_clearer_crop`; do not manufacture textbook content.
4. OCR uncertainty is explicit: preserve candidate glyphs for `z^-1` vs `z-1`, `l` vs `1`, signs, roots, superscripts, and circuit labels.
5. Derivations from user-provided values are labelled calculations, not claims attributed to a book.

This follows NIST's provenance/grounding direction: record data origin/content lineage and retrieval sources for GenAI systems.[14]

## Contracts between specialists

The harness owns contracts. Models never talk in free prose to each other.

```json
{
  "crop_id": "sha256:...",
  "kind": "handwriting|printed_page|mixed|diagram",
  "lines": [{"text": "H(z)=...", "confidence": 0.91, "bbox": [12,48,312,86]}],
  "uncertain_spans": [{"alternatives": ["z^-1", "z-1"], "bbox": [160,48,204,86]}],
  "source_page": {"book_id": "oppenheim-3e", "printed_page": "245"}
}
```

```json
{
  "intent": "explain_and_plot",
  "evidence_mode": "textbook|reference|derived_from_user_work|parametric_fallback",
  "claims": [{"text": "...", "source_refs": ["oppenheim-3e:p245:c03"]}],
  "actions": [{"type": "graph", "spec": {}}],
  "uncertainties": []
}
```

The existing Smriti schema validator must reject unknown actions, malformed graph specs, absent required source refs, source/page mismatches, and raw tool commands. The plot, diagram, equation layout, and ink renderer stay deterministic.

## Low-complexity pilot

Do this before model swaps, vector databases, agent loops, or fine-tuning:

1. Ingest **one** course textbook PDF, text-native pages first.
2. Use SQLite FTS5 with page-bound chunks and selected-page boosting. No vector database yet.
3. Run LM Studio loopback-only. Its OpenAI-compatible endpoint can apply JSON-schema constrained responses, matching Smriti's current typed-plan path.[2]
4. Implement two flows only:
   - typed request -> textbook lookup -> cited explanation / deterministic graph;
   - printed page crop -> OCR -> same-page cited explanation.
5. Benchmark actual rM2 crops and source pages on: symbol error rate, equation-LaTeX validity, page citation accuracy, first-token latency, total response latency, and peak unified memory.
6. Add Math 7B and DeepSeek only if those measurements identify a gap.

## Security and availability

- Bind LM Studio and the harness to `127.0.0.1` first. Network serving creates an exposure boundary.
- Enable LM Studio API-token authentication even on loopback. Its server settings provide access-control and network options.[3]
- Disable MCP, browser CORS, automatic model download, remote URLs in PDFs, and network/file tools from the model.
- Store textbooks read-only; store OCR/index artefacts separately. Hash each input at ingestion; retain images under an explicit expiry policy.
- Model output and retrieved text are untrusted data. They never become instructions or executable input.
- If rM2 later connects to the Mac mini, put a narrow Smriti service behind the private overlay. Do **not** expose LM Studio itself to the LAN or public Internet.

## Acceptance gates

- Selected textbook crop returns correct book, edition, printed page, and region citation.
- Invalid or invented citation ID is rejected before rendering.
- Ambiguous handwriting prompts for a clearer crop/candidate choice; it never silently selects a symbol.
- Same graph specification renders identically on repeat runs.
- Planner cannot invoke an undeclared tool or bypass plan validation.
- Measured Mac memory and latency satisfy an agreed target before any live rM2 fallback deployment.

## Sources

[1] https://lmstudio.ai/docs/app/system-requirements
[2] https://lmstudio.ai/docs/developer/openai-compat/structured-output
[3] https://lmstudio.ai/docs/developer/core/server/settings
[4] https://huggingface.co/Qwen/Qwen3-4B
[5] https://huggingface.co/Qwen/Qwen3-4B-GGUF
[6] https://huggingface.co/Qwen/Qwen2.5-Math-7B-Instruct
[7] https://huggingface.co/bartowski/Qwen2.5-Math-7B-Instruct-GGUF
[8] https://huggingface.co/Qwen/Qwen2.5-VL-3B-Instruct
[9] https://huggingface.co/ggml-org/Qwen2.5-VL-3B-Instruct-GGUF
[10] https://huggingface.co/deepseek-ai/DeepSeek-OCR
[11] https://huggingface.co/ggml-org/DeepSeek-OCR-GGUF
[12] https://huggingface.co/Qwen/Qwen2.5-VL-7B-Instruct
[13] https://huggingface.co/ggml-org/Qwen2.5-VL-7B-Instruct-GGUF
[14] https://nvlpubs.nist.gov/nistpubs/ai/NIST.AI.600-1.pdf
