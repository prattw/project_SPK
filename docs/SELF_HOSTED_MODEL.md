# Running Project SPK against a self-hosted model

Project SPK talks to the model over the OpenAI chat-completions API. That is an
interface, not a vendor: anything that speaks it works, including models running on
hardware you control with no route to the internet.

Two settings decide where every prompt goes — document questions, email analysis,
reply drafts, all of it:

```bash
OPENAI_BASE_URL=https://llm.internal.example.mil/v1
OPENAI_MODEL=the-model-the-server-serves
OPENAI_API_KEY=whatever-that-server-accepts
```

Leave `OPENAI_BASE_URL` empty and prompts go to `api.openai.com`. Set it and they
go where you point it. **There is no code change involved**, and nothing about the
email feature is special-cased: redirecting the model redirects email processing
with it.

`OPENAI_API_KEY` must be non-empty even for a server that ignores it, because the
client library requires a value. Most self-hosted servers accept any string.

## Why this matters for email

Email is the reason this document exists. Government correspondence routinely
carries CUI, PII, procurement-sensitive, and attorney-client material, and the
autonomous sweep processes 72 hours of it at a time rather than one thread a user
deliberately chose. Where that content is processed is the whole question.

So the app reports it rather than leaving it implicit. `GET /email/status` returns:

```json
{
  "model": {
    "model": "llama-3.3-70b-instruct",
    "endpoint_host": "llm.internal.example.mil",
    "public_openai": false,
    "self_hosted": true,
    "local": false,
    "configured": true
  }
}
```

The Email Assistant tab turns that into a banner: amber and naming the commercial
host when prompts leave for `api.openai.com`, green when they do not.

One honest caveat about `self_hosted`: it means "not a commercial multi-tenant API",
which is inferred from the hostname. Whether the endpoint is genuinely isolated is a
network and accreditation question that application code cannot answer. Treat the
banner as a configuration check, not an accreditation.

## Servers that work

All of these expose an OpenAI-compatible `/v1` endpoint.

| Server | Typical base URL | Notes |
| --- | --- | --- |
| [vLLM](https://docs.vllm.ai) | `http://host:8000/v1` | The usual choice for GPU serving with real throughput |
| [Ollama](https://ollama.com) | `http://host:11434/v1` | Easiest to stand up; good for a pilot or a single workstation |
| [llama.cpp](https://github.com/ggml-org/llama.cpp) `llama-server` | `http://host:8080/v1` | Runs on CPU; viable where there is no GPU |
| [LM Studio](https://lmstudio.ai) | `http://host:1234/v1` | Desktop, useful for evaluating models before committing |
| [TGI](https://huggingface.co/docs/text-generation-inference) | `http://host:8080/v1` | Hugging Face's server |
| Azure OpenAI | `https://<resource>.openai.azure.com/openai/v1` | Not self-hosted, but available in GCC High / DoD at accredited impact levels |

Azure OpenAI in a government cloud is worth calling out separately: it is a
commercial service, but one that may already hold the accreditation a self-hosted
model would have to earn. If the goal is "approved" rather than "on our hardware",
it is often the shorter path.

## Choosing a model

The email agents ask the model for **strict JSON** — the analysis, note, and meeting
proposal come back in one structured object. That is the capability to test first,
because a model that writes lovely prose but drifts out of JSON will fail the sweep
while looking fine on document questions.

What the workload needs:

- **Reliable JSON adherence.** Non-negotiable. The parser tolerates code fences and
  surrounding prose, but it cannot rescue malformed JSON.
- **Instruction following over long-ish inputs.** A full email thread with quoted
  history runs to a few thousand tokens.
- **Roughly 8k usable context.** Threads are truncated at 24,000 characters, and
  library-grounded replies add retrieved excerpts on top.
- **Date arithmetic.** Turning "Thursday at 10" into a timestamp is where weaker
  models fail most visibly. Failures here are caught rather than acted on — an
  unparseable or past time produces no calendar file — so the symptom is missing
  invites, not wrong ones.

Instruction-tuned models in the 8B–70B range generally handle this. Test with real
district email before committing, and use the two tone checks below.

## Verifying a new endpoint

```bash
# 1. The server answers and serves the model you think it does.
curl -s "$OPENAI_BASE_URL/models" | head -40

# 2. Project SPK agrees about where prompts are going.
curl -s "$SPK_URL/email/status" -H "Authorization: Bearer $SPK_TOKEN" \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["model"])'

# 3. A real analysis round-trips, including the JSON contract.
curl -s -X POST "$SPK_URL/email/analyze" \
  -H "Authorization: Bearer $SPK_TOKEN" -H "Content-Type: application/json" \
  -d '{"text":"From: pm@contractor.com\nSent: Monday, January 5, 2026 9:14 AM\nTo: you@usace.army.mil\nSubject: Submittal 03 30 00\n\nWhat is the review period? We need an answer by Friday."}'
```

If step 3 returns `The assistant did not return a readable analysis`, the model is
not holding the JSON contract. Try a larger instruction-tuned model, or one with
constrained/grammar-based decoding enabled — vLLM and llama.cpp both support forcing
valid JSON, which removes the failure mode entirely.

Then run the whole email feature against it:

```bash
python scripts/test_email_sweep_api.py   # stubbed model: proves the plumbing
# then a real sweep against the live endpoint:
curl -s -X POST "$SPK_URL/email/sweep" -H "Authorization: Bearer $SPK_TOKEN" \
  -H "Content-Type: application/json" -d '{"hours":72}'
```

## Embeddings are a separate decision

`OPENAI_BASE_URL` also redirects embeddings, which the document library needs for
retrieval. Two consequences worth knowing before switching:

- The self-hosted server must serve an embeddings endpoint too, or document search
  breaks even though chat works.
- **Changing the embedding model invalidates the existing index.** Vectors from
  different models are not comparable. Switching means re-ingesting the library —
  see `DEPLOY.md`.

The email assistant only needs embeddings for library-grounded replies
(`use_library: true`). Sweeps, analysis, notes, and invites need chat alone, so
email works against a chat-only endpoint.

## Air-gapped notes

- `WARM_INDEX_ON_STARTUP=true` (the default) loads the vector index at boot so the
  first request is not slow. It needs no network.
- Publication sync (`POST /sync/publications`) reaches out to
  `publications.usace.army.mil` and will fail without a route. Nothing else depends
  on it.
- `OUTLOOK_CONNECTOR=local_folder` is the mail source designed for this posture: it
  reads files off disk and makes no network calls. See
  [`OUTLOOK_INTEGRATION.md`](OUTLOOK_INTEGRATION.md).
- Pin `OPENAI_BASE_URL` to an internal hostname, not an IP, so certificate
  validation still means something.
