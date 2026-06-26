# Kiwix Disambiguation

A single ambiguous word — *"what is mercury"*, *"tell me about galaxy"* — is genuinely hard for a small local LLM to resolve correctly. The model has no visibility into what's actually in your Kiwix index; it can only guess at a search phrase based on its own training, and that guess can be wrong in unpredictable ways: too broad and it gets drowned out by unrelated category pages, too narrow and it collides with a completely different topic that happens to share vocabulary.

Mnemolis's answer to this is structural, not just a better prompt: generate several candidate search phrases, actually run all of them against the real index, and let genuine search results plus scoring decide which one wins — rather than trusting a single blind guess about content the LLM can't see.

## When this actually triggers

Multi-candidate disambiguation only fires when **all four** of these are true:

1. An LLM backend is configured at all
2. The query is recognizably definitional — *"what is X"*, *"tell me about X"*, or one of the recognized colloquial forms (*"what's the deal with X"*, *"what's up with X"*)
3. The book selected was Wikipedia specifically — encyclopedic ambiguity is the problem being solved here, not general Q&A ambiguity
4. The actual search term, after stop-word stripping, is exactly **one word**

That fourth condition is checked against the *final, cleaned* search term — not the original query, and not the single longest word picked out of it. This matters: a query with real multi-word context (*"raspberry pi gpio permission errors in python"*) never enters this path at all, since its full cleaned term set is more than one word, even though one individual word in it (`"permission"`) might look ambiguous in isolation.

```text
            Is an LLM configured?
                      │
              ┌───────┴───────┐
              ▼ no             ▼ yes
        Skip entirely    Is the query definitional?
                                │
                        ┌───────┴───────┐
                        ▼ no             ▼ yes
                  Skip entirely    Was Wikipedia the
                                   selected book?
                                          │
                                  ┌───────┴───────┐
                                  ▼ no             ▼ yes
                            Skip entirely    Is the FULL cleaned
                                             search term exactly
                                             one word?
                                                    │
                                            ┌───────┴───────┐
                                            ▼ no             ▼ yes
                                      Skip entirely    Generate 2-3
                                                        disambiguation
                                                        candidates
```

## What happens once it triggers

The LLM is asked for 2–3 candidate search phrases that might find the article actually meant — not a single best guess, several real options. Every candidate is then genuinely searched against Kiwix (not just trusted), and every result from every candidate gets pooled together and deduplicated by URL before scoring picks the actual winner. See [Kiwix Scoring](Kiwix-Scoring) for exactly how that final decision is made.

Candidate generation is itself cached in the routing cache (`disambig_candidates:{search_terms}`), so a repeated ambiguous query doesn't pay the LLM cost twice — the expensive part (asking the LLM what the candidates even are) happens once per unique ambiguous term, ever (within the cache's TTL), while the actual disambiguation search-and-score still runs fresh.

**Only a true LLM call failure skips the cache.** If the LLM call itself genuinely fails (a timeout, a malformed response), the bare fallback — just the original word, no real disambiguation at all — is never cached, so the next retry of that exact query gets a fresh attempt rather than repeating the same unhelpful fallback for the rest of the cache's TTL. One real nuance: if the LLM genuinely *responded* with candidates that simply didn't survive the sanity check below (rather than failing to respond at all), that outcome still gets cached, since the same prompt would likely produce the same unusable answer again.

The sanity check itself — does a candidate actually contain the original ambiguous word — requires the word to appear as its own whole word, not just anywhere inside the candidate as a substring. This matters most for single-letter or single-digit ambiguous terms (the word "C" the programming language, say): a bare substring check would let almost any candidate through, since most English phrases happen to contain the letter "c" somewhere.

## Why this is structurally different from "ask the LLM to pick better"

The tempting, simpler fix for ambiguous-word queries is to just improve the prompt asking the LLM which article it means. That approach has a hard ceiling: the LLM is still guessing blind, no matter how the prompt is worded, because it genuinely cannot see what's actually indexed in your specific Kiwix instance. Generating multiple candidates and testing them for real against the actual index sidesteps the problem entirely rather than trying to prompt-engineer around it — closer to a search-and-verify approach than a single confident guess.

This isn't a theoretical preference — three single-guess prompting strategies were tried and discarded against the same real failures (*"what are galaxies"* landing on a Samsung Galaxy phone; *"how do batteries work"* landing on a military fortification article) before this architecture was settled on:

1. **A broader category hint** ("galaxy astronomy") — the disambiguation word itself still dominated the search, surfacing dozens of unrelated astronomy-portal pages instead of the actual target article.
2. **A rarer, more specific qualifier** ("galaxy celestial") — collided with an entirely unrelated topic that happened to share thematic vocabulary with the intended one.
3. **Abandoning word-injection entirely, fixing scoring alone instead** — rejected as insufficiently general, since the underlying problem (the LLM guessing at search text for an index it can't see) isn't something scoring downstream can fully compensate for.

Each single-guess attempt failed for a different, specific reason, not the same reason twice — which is itself the real evidence that no amount of prompt refinement alone was going to close this gap. Searching multiple genuinely different candidates and verifying against real results is what finally worked.

## A genuine, accepted limitation

Even with disambiguation working correctly, a single bare word can still land on an imprecise match if the index genuinely contains multiple, comparably-relevant senses of the same word — *"galaxy"* landing on a Hitchhiker's Guide reference rather than astronomy content is a real example, not a bug in the disambiguation logic itself. This is a search-relevance ceiling, not something multi-candidate generation can fully solve on its own — see [Kiwix Scoring](Kiwix-Scoring) for how close scoring can get, and where it still falls short.

---

## Development Notes

- **The eligibility check used to be checked against `primary_term` (always exactly one word by construction) rather than the full cleaned search term set**, making it trivially true regardless of how unambiguous the actual query was. A real, multi-word, genuinely unambiguous query (`"raspberry pi gpio permission errors in python"`) was triggering disambiguation on a single word in isolation and landing on an unrelated article. Fixed by checking eligibility against the full term set.
- **A bad LLM response used to get permanently stuck for the rest of the cache's TTL** — a true call failure (timeout, malformed response) was being cached the same way a real success would, so every retry of an exact query kept getting the same unhelpful fallback for up to an hour. Fixed; only a genuine call failure now skips the cache, while a response that simply failed the sanity check still caches (since it would likely fail the same way again).
- **The candidate sanity check used to use a bare substring match**, which provided meaningfully less protection for short search terms than long ones — almost any English phrase coincidentally contains a single letter like "c" somewhere, so a one-character ambiguous word got far less real filtering than a multi-word one. Surfaced after fixing a separate bug that let single-character search terms reach this filter for the first time. Fixed with the same word-boundary discipline already applied elsewhere in the codebase.
