# Kiwix Disambiguation

A single ambiguous word — *"what is mercury"*, *"tell me about galaxy"* — is genuinely hard for a small local LLM to resolve correctly. The model has no visibility into what's actually in your Kiwix index; it can only guess at a search phrase based on its own training, and that guess can be wrong in unpredictable ways: too broad and it gets drowned out by unrelated category pages, too narrow and it collides with a completely different topic that happens to share vocabulary.

Mnemolis's answer to this is structural, not just a better prompt: generate several candidate search phrases, actually run all of them against the real index, and let genuine search results plus scoring decide which one wins — rather than trusting a single blind guess about content the LLM can't see.

## When this actually triggers

Multi-candidate disambiguation only fires when **all four** of these are true:

1. An LLM backend is configured at all
2. The query is recognizably definitional — *"what is X"*, *"tell me about X"*, or one of the recognized colloquial forms (*"what's the deal with X"*, *"what's up with X"*)
3. The book selected was Wikipedia specifically — encyclopedic ambiguity is the problem being solved here, not general Q&A ambiguity
4. The actual search term, after stop-word stripping, is exactly **one word**

That fourth condition is the one with real history behind it. It's checked against the *final, cleaned* search term — not the original query, and not the single longest word picked out of it. An earlier version mistakenly checked word count against `primary_term`, which is *always* exactly one word by construction (it's deliberately the longest word extracted from the search terms), making the eligibility check trivially true for every query regardless of how unambiguous it actually was. *"raspberry pi gpio permission errors in python"* — five genuinely unambiguous content words — was triggering disambiguation on `"permission"` alone, discarding everything else, and landing on an unrelated macOS disk-permissions article. Checking against the full cleaned term set instead fixed this: a query with real multi-word context never enters this path at all.

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

**A bad LLM response used to get permanently stuck for the rest of the cache's TTL.** If the LLM call itself genuinely failed (a timeout, a malformed response), the bare fallback — just the original word, no real disambiguation at all — used to get cached the same way a real success would, meaning every retry of that exact query kept getting the same unhelpful fallback for up to an hour. Fixed now, but with one real nuance: if the LLM genuinely *responded* with candidates that simply didn't survive the sanity check below (rather than failing to respond at all), that outcome still gets cached, since the same prompt would likely produce the same unusable answer again — only a true call failure skips the cache.

The sanity check itself — does a candidate actually contain the original ambiguous word — now requires the word to appear as its own whole word, not just anywhere inside the candidate as a substring. This matters most for single-letter or single-digit ambiguous terms (the word "C" the programming language, say): a bare substring check would have let almost any candidate through, since most English phrases happen to contain the letter "c" somewhere.

## Why this is structurally different from "ask the LLM to pick better"

The tempting, simpler fix for ambiguous-word queries is to just improve the prompt asking the LLM which article it means. That approach has a hard ceiling: the LLM is still guessing blind, no matter how the prompt is worded, because it genuinely cannot see what's actually indexed in your specific Kiwix instance. Generating multiple candidates and testing them for real against the actual index sidesteps the problem entirely rather than trying to prompt-engineer around it — closer to a search-and-verify approach than a single confident guess.

## A genuine, accepted limitation

Even with disambiguation working correctly, a single bare word can still land on an imprecise match if the index genuinely contains multiple, comparably-relevant senses of the same word — *"galaxy"* landing on a Hitchhiker's Guide reference rather than astronomy content is a real example, not a bug in the disambiguation logic itself. This is a search-relevance ceiling, not something multi-candidate generation can fully solve on its own — see [Kiwix Scoring](Kiwix-Scoring) for how close scoring can get, and where it still falls short.
