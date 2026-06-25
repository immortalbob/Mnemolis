# Troubleshooting

Indexed by symptom — start here if something's behaving unexpectedly, before assuming it's a Mnemolis bug.

## "Error reaching SearXNG: connection failed" or "request timed out"

As of a later fix, Mnemolis distinguishes a genuine timeout from other connection failures in its own response — a timeout-specific message means exactly that, not a guess. Either way, check [Health & Observability](Health-and-Observability) first — `/health`'s `sources.web` field will show the real underlying error, which is almost always more specific and more useful than Mnemolis's own response. Two distinct real causes have actually been found and documented here:

- **Timeout set too aggressively for real engine latency.** SearXNG's default `request_timeout` (3.0s) is genuinely too short — several real search engines routinely take 20+ seconds to respond under completely normal conditions. See [The SearXNG Timeout Lesson](The-SearXNG-Timeout-Lesson) for the exact fix and, more importantly, for the second part of that story: **a correctly-edited config file doesn't help if the container was never restarted to pick it up.** If you've already made this fix and the error persists, check whether SearXNG has actually been restarted since you edited `settings.yml` — `docker exec searxng grep -A2 "^outgoing:" /etc/searxng/settings.yml` reads the config the *live process* sees, which is the only way to be sure.
- **Genuine upstream rate limiting.** A specific engine (Brave has been observed doing this) returning `SearxEngineTooManyRequestsException` after heavy query volume. This self-resolves after the engine's own suspension window; it's not something to "fix" so much as expect under sustained testing.

## A routing decision looks wrong — Kiwix is being skipped for something encyclopedic

If the query is phrased like current discourse ("what's the deal with X everyone's talking about/obsessed with"), this is a known, specifically-handled pattern — see [The Discourse-Framing Investigation](The-Discourse-Framing-Investigation) and [Adversarial Self-Testing](Adversarial-Self-Testing#real-bug-discourse-framing-escalation-never-ran-on-the-keyword-match-path) for the full history, including a real gap found in the keyword-matching path specifically, not just the LLM-assisted one. If you're still seeing Kiwix skipped for phrasing that doesn't match that pattern, check [Routing](Routing) for the actual keyword-match and LLM-selection logic; the LLM genuinely can make a different call than expected for a query that's just inherently ambiguous about which source applies.

## LLM-assisted routing or disambiguation seems to silently stop working — no errors, just generic keyword-only behavior

Check `/health`'s `sources.llm` field first to confirm the backend is actually reachable at all. If it reports healthy but routing decisions still look like plain keyword matching even for queries that should need the LLM, and you're running a "thinking" model (Qwen3 and similar reasoning-tuned families) via an OpenAI-compatible endpoint (`LLM_API_TYPE=openai` — `llama-server`, LM Studio, etc.), this was a real, serious bug: thinking models on this specific path used to return completely empty responses for every single completion, since their actual answer sits in a separate `reasoning_content` field that nothing read. Fixed now — see [LLM Client](LLM-Client#a-real-serious-bug-thinking-models-silently-returned-nothing-at-all) for the full mechanism and why it only affected the OpenAI-compatible path, not Ollama's native API.

## Kiwix found the wrong article for a single ambiguous word

Check [Kiwix Scoring](Kiwix-Scoring#where-scoring-still-has-a-real-ceiling) first — if the word genuinely has multiple, comparably-represented senses in your index (the "galaxy" astronomy-vs-pop-culture case is the documented example), this is an accepted, real limitation of keyword-and-structure scoring, not necessarily a bug. If the query was multi-word and still landed on something nonsensical, that's more likely worth investigating — [disambiguation](Kiwix-Disambiguation) is only supposed to trigger for genuinely single-word, definitional, Wikipedia-targeted queries.

## A compound question only got partially answered

If you're using Open WebUI or a similar tool-calling setup, check whether the calling model is actually forwarding your *entire* message to Mnemolis in one call, or silently splitting/truncating it itself before Mnemolis's own [decomposition](Query-Decomposition) ever sees the full question. This is a real, repeatedly-observed failure mode at the model layer, not a Mnemolis routing bug — see the [Open WebUI System Prompt Guide](Open-WebUI-System-Prompt-Guide) for the actual fix and the log-level evidence confirming it works.

If you're calling the API directly and a genuinely compound query still isn't splitting correctly, check [Query Decomposition](Query-Decomposition) for what conjunctions are actually recognized — a conjunction Mnemolis doesn't currently watch for (anything outside `"and"`, `"also"`, `"plus"`, `"as well as"`, `"in addition"`) won't trigger a split.

## A proper noun pair got split apart when it shouldn't have (or vice versa)

This exact category of bug has a long, real history — see [The Proper-Noun-Pair Saga](The-Proper-Noun-Pair-Saga) for the full account of five sequential bugs found in this specific logic, including one found via a deliberate code-reading investigation rather than a failing test. If you're seeing a sixth, it's worth checking whether your query combines a protected pair with adjacent real content in a way the existing fixes don't account for, or whether a different always-capitalized word (beyond the pronoun "I," already excluded) is producing a similar false positive.

## A conditional question ("if X, Y") didn't get the expected framing

Check [Conditional Query Detection](Conditional-Query-Detection#why-the-pattern-is-this-narrow) — detection is deliberately narrow. Missing the leading comma ("if the front door is unlocked tell me," no comma) is a known, accepted limitation, not a bug. Mid-sentence or trailing "if" is out of scope for the same reason. If the structure genuinely matches the leading-comma pattern and still isn't framed correctly, check whether the source the condition resolved to is actually one of the three [interpretable sources](Conditional-Query-Detection#honest-abstention-the-actual-point-of-this-feature) (`ha`, `uptime`, `forecast`) — every other source intentionally never gets a yes/no verdict, only an honestly-presented raw result.

## A backup/restore command silently did nothing

Almost always a Docker volume-naming mismatch, not a real failure — see [Backup & Restore](Backup-and-Restore#a-real-gotcha-worth-knowing-before-you-need-it-volume-naming). Check the *actual* volume name Docker created (`docker volume ls`, or `docker inspect <container>`) before assuming the restore command itself is broken.

## A background snapshot job seems to have stopped

Check `/health`'s `snapshot_jobs` field — see [Health & Observability](Health-and-Observability#background-job-health) for what each status (`ok`, `stale`, `never_ran`, `unknown`) actually means and how the staleness threshold is calculated.

## "No matching entities found" for a Home Assistant question that should clearly have an answer

See [Home Assistant Integration](Home-Assistant-Integration#if-a-specific-entity-question-ever-came-back-empty) — a real, now-fixed bug used to cause exactly this for specific entity questions like "is the front door locked." If you're still seeing it, check that the entity actually exists and is named the way you'd expect — `GET /areas` lists what Mnemolis can currently see.

## A source you forgot to configure returned its own raw error message instead of falling back to web search

Check [Routing](Routing#fallback-when-a-source-comes-back-empty) — `kiwix` and `news` are both supposed to fall back to `web` automatically when they come back empty, including when they're simply unconfigured. This used to fail silently for certain "not configured"/"could not connect" messages specifically; fixed now, but worth confirming the source you expected to fall back from is actually the intended one, since not every source has a configured fallback target.

## Weather forecast looks completely wrong for your location

If `forecast` is returning real-looking weather data that's nowhere near where you actually live, check that `FORECAST_LATITUDE` and `FORECAST_LONGITUDE` are genuinely set — leaving them blank used to silently default to `(0, 0)`, a real, valid ocean coordinate, so the forecast would "work" without ever telling you it was answering for the wrong place on Earth. This is fixed now: an unconfigured forecast correctly returns a "not configured" message instead.

## A query about a single-letter or single-digit topic (like "the C language" or "vitamin D") returns oddly generic, off-topic results

A real bug used to drop single-character search terms entirely while scoring and building search queries, since they were filtered out the same way stray punctuation is — meaning a query about "R programming" could lose the one word that actually distinguished it from any other programming language. Fixed now; single letters and digits are kept.

## A fusion query (`source="fusion"`) returns an error instead of results

If `FUSION_MAX_SOURCES` is set to `0`, this used to crash with a raw error; it now correctly reports "no valid sources specified" instead. If you're trying to limit fusion to fewer sources, set it to a positive number — `0` was never a valid way to disable fusion, just a configuration mistake that's now handled gracefully instead of crashing.

## General debugging principle, if none of the above applies

Several of the real bugs documented in [Design History](Home#design-history-real-bugs-real-fixes) were only correctly diagnosed by adding genuine debug tracing and reading the actual output, rather than guessing at a plausible-sounding cause. If something's behaving unexpectedly and none of the documented cases above match, check the application logs directly (`docker logs mnemolis`) for the actual routing/decomposition/conditional-detection decision being made, rather than inferring it from the final response alone — the log lines at each of those stages are usually specific enough to show exactly where a query's handling diverged from what was expected.
