# The Discourse-Framing Investigation

*"What's the deal with that whole mercury retrograde thing everyone keeps talking about"* is a genuinely encyclopedic question, phrased the way an actual person would ask it out loud — and for a long stretch of this project's life, it reliably routed past [Kiwix](Kiwix-Disambiguation) entirely, landing on generic news and web results instead. This was the longest-standing known limitation in the whole project, and fixing it properly took two separate, sequential discoveries — the second one found only after the first fix had already shipped and seemed, on its own, complete.

## Finding the actual root cause

The instinct going in was that this was probably a routing judgment call gone slightly wrong — maybe Kiwix's description just needed better wording. Reading the actual prompt the LLM sees made the real mechanism obvious instead: `news` and `web`'s own descriptions say things like *"current events"* and *"recent information,"* and *"everyone keeps talking about"* matches that phrasing almost word-for-word. Kiwix's description — *"factual, encyclopedic, or technical questions"* — gives the model no reason to think an evergreen topic can *also* be currently trending in conversation. From the LLM's own perspective, given only those descriptions, picking news/web for this phrasing isn't a mistake at all — it's a reasonable reading of the words it was given.

Two ways to fix this were on the table: reword Kiwix's description to nudge the LLM's judgment, or detect the pattern explicitly and bias the routing decision directly. The first is an indirect, genuinely unverifiable lever — there's no way to know in advance whether new wording would actually shift behavior, or shift it too far in the other direction and start over-triggering Kiwix for queries that really are just current events. The second is explicit, testable in isolation, and doesn't depend on guessing how an 8B model will weigh adjectives in a prompt. The explicit approach won.

## The first fix — routing bias

`_has_discourse_framing()` checks for a small set of literal phrases (`"everyone keeps talking about"`, `"everyone's obsessed with"`, and close variants). If one is present and Kiwix wasn't already part of the LLM's chosen source(s), Kiwix gets added and the result escalates to [fusion](Fusion) — without discarding whatever the LLM already picked, since web/news content was often genuinely relevant too.

Applied across all four real code paths where a routing decision can be made: fresh single-source, fresh multi-source, and — importantly — the *cached* version of each, since a routing cache entry written before this fix existed would otherwise silently bypass it for the remainder of its TTL.

This shipped, was verified against several real queries (mercury, bitcoin, galaxy), and looked complete: Kiwix was now reliably included in the fusion decision for this phrasing.

## The second fix — the first one wasn't actually enough

Continued real-production testing after the routing fix shipped kept surfacing nonsensical Kiwix results even with Kiwix correctly included in the fusion list. *"Bitcoin"* landed on a Big Bang Theory character. *"Black holes"* landed on a Thai horror film. Both with very low relevance scores — not close misses, genuinely unrelated content winning anyway.

The actual cause: routing was fixed, but [Kiwix's search terms](Kiwix-Scoring) were never cleaned up. The literal words *"everyone,"* *"obsessed,"* *"talking,"* *"keep"* all survived stop-word stripping untouched, because they're real English words, not filler in the traditional sense — just pure noise in *this specific* discourse-framing context. The actual string sent to Kiwix for the bitcoin query was `"what whole bitcoin everyone obsessed"` — and Kiwix was scoring matches against "everyone" and "obsessed" exactly as seriously as it was scoring matches against "bitcoin," which explains the nonsensical winners.

`_strip_discourse_framing()` removes the whole matched phrase as a single unit before search terms get built — not by adding "everyone," "obsessed," etc. individually to the general stop-word list, which would risk treating those words as meaningless filler in some *other*, unrelated query where they might carry real meaning. Stripping the exact matched phrase only ever affects queries that actually contain this specific pattern.

```text
   Before fix 1 (routing):
   "what's the deal with bitcoin everyone is obsessed with"
                         │
                         ▼
              Routes to news/web only.
              Kiwix never even searched.

   After fix 1, before fix 2 (search terms):
                         │
                         ▼
              Kiwix correctly included —
              but searched with "what whole
              bitcoin everyone obsessed"
                         │
                         ▼
              Scores "everyone"/"obsessed" as
              real signal. Wins: unrelated
              sitcom character, score 2.

   After both fixes:
                         │
                         ▼
              Kiwix included AND searched
              with "what whole bitcoin"
                         │
                         ▼
              Wins: the actual Bitcoin
              article, score 32.
```

## A real, deliberate single source of truth

The discourse-framing pattern list now lives in `kiwix.py` — not `router.py` — even though `router.py` needs it too, for the routing bias. `router.py` imports it from there rather than each module keeping its own copy. The direction matters: `router.py` already imports the `kiwix` module elsewhere, so importing the pattern list the same direction is safe; the reverse (`kiwix.py` importing from `router.py`) would create a circular import, since `router.py` is what calls into `kiwix.py` in the first place. One list, two genuinely different uses (bias a routing decision; strip noise from a search term), zero risk of the two copies drifting apart from each other over time — because there's only ever one copy.

That guarantee only holds because it was built this way from the start. A separate, similar-shaped mechanism elsewhere in this same codebase (`router.py` and `fusion.py`'s shared "does this result look empty" phrase list) was built the opposite way — two independently-maintained copies, each presumably written with the same care — and genuinely did drift apart over time, missing different real failure phrases in each direction, found and unified the same way this one already was. Worth remembering as the actual lesson: a single shared definition isn't just tidier, it's the only version of this pattern that can't silently drift, no matter how carefully each copy is written.

## What the fix actually proved, end to end

Verified against real production data after both fixes shipped: *"bitcoin"* went from a nonsensical match (score 2) to the correct article (score 32). *"Black holes"* went from a horror film (score 7) to a real historical topic genuinely named "Black Hole" (Black Hole of Calcutta, score 22) — not the astrophysics article one might have expected for this specific bare word, but a legitimate, defensible match rather than a nonsensical one. *"Galaxy"* improved but didn't fully resolve, since the word itself remains genuinely ambiguous between astronomy and pop-culture senses in the index — a separate, smaller, accepted limitation, not evidence either fix fell short.
