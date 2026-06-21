# Query Decomposition

Decomposition is what lets Mnemolis answer a single, casually-phrased compound question — *"what's the weather and are the lights on"* — by splitting it into independent sub-queries, routing each one separately, and merging the results back into one response. It runs once, on the full original query, before any per-sub-query [Routing](Routing) happens.

This page covers the splitting logic in detail, including four real bugs found and fixed in the part of it that protects proper-noun pairs from being split apart — the single most-revised piece of logic in this project's history. The full narrative, in the order the bugs were actually found, is in [The Proper-Noun-Pair Saga](The-Proper-Noun-Pair-Saga); this page covers the mechanism as it stands today.

## The splitting mechanism

Decomposition looks for five conjunction phrases: `" and "`, `" also "`, `" plus "`, `" as well as "`, `" in addition "`. It tries two different strategies and keeps whichever produces more genuinely separate, meaningful parts:

1. **Single-conjunction-type split** — try each conjunction type in isolation, see how many parts it produces
2. **Combined split** — find every occurrence of *any* conjunction type across the whole query at once, and split on all of them simultaneously

The combined approach exists because real compound sentences don't politely use only one kind of conjunction — *"check the wifi, and also can you check if the front door is locked and the lights are off"* has two separate `" and "` occurrences that need to be treated as two separate split points, not one.

```text
   "check the weather and also are the lights on"
                    │
                    ▼
        Try " and " alone: 2 parts
        Try " also " alone: 2 parts
        Try combined (both at once): 2 parts
                    │
                    ▼
        Pick whichever produced the MOST
        meaningful parts (ties keep the
        first-found result)
                    │
                    ▼
   ["check the weather", "are the lights on"]
```

## What stops something from being split when it shouldn't be

Not every `" and "` is a conjunction joining two intents. Three separate guards exist, each catching a different real failure mode:

**`_NOSPLIT_PATTERNS`** — a short list of words (`compare`, `difference between`, `vs`, `versus`, `both`, `either`, `neither`, `between`) that, if present anywhere in the query, cancel splitting entirely. *"compare Python and Rust"* needs both halves to stay together to mean anything.

**Meaningful-content filtering** — after a split, each resulting part is checked for actual content. A part that's just stop words and filler (`"and"`, `"the"`, `"is"`) gets discarded rather than kept as a bogus third "intent." This check is stop-word-based, not an allowlist of expected topics — an earlier version used a fixed list of "intent words" that had zero coverage for technical vocabulary, silently dropping real content like *"I've been getting a python pigpio no permission to update GPIO error"* because none of those words happened to be on the list.

**The proper-noun-pair guard** — the hardest of the three, and the one with real history. *"what's happening with Iran and Israel"* should never split into `["what's happening with Iran", "Israel"]`. The guard looks for a specific structural pattern: a capitalized word right before the conjunction, a capitalized word right after it, and a short, name-shaped phrase following — not a place-name list, since that would never generalize, but a structural detector that works for any properly-capitalized pair.

```text
   Decomposition Defense Layers
   ┌─────────────────────────────────────┐
   │  _NOSPLIT_PATTERNS                 │  "compare X and Y" → never split at all
   │  (whole-query veto)                │
   ├─────────────────────────────────────┤
   │  Proper-noun-pair guard            │  "Iran and Israel" → this ONE
   │  (per-occurrence, not global)      │  occurrence isn't a split point,
   │                                     │  but OTHER real conjunctions
   │                                     │  in the same query still split
   ├─────────────────────────────────────┤
   │  Meaningful-content filtering      │  stray fragments with no real
   │  (post-split cleanup)              │  content get discarded, not kept
   │                                     │  as a bogus extra intent
   └─────────────────────────────────────┘
```

That middle layer — "per-occurrence, not global" — is doing a lot of work, and it's exactly where three of the four real bugs lived. An earlier version's guard was a global veto: finding *any* proper-noun pair anywhere in the query canceled splitting entirely, which meant *"check the weather, and Iran and Israel news, and is the door locked"* lost its other two genuine intents just because one harmless pair happened to be present somewhere in the sentence. The current guard checks each conjunction occurrence independently, protecting only the specific pair it found while leaving every other real split point alone.

## Colloquial phrasing

Decomposition (and the search-term cleanup that follows it) needs to handle the way people actually talk, not just formal phrasing. A set of stop-word-style filler terms — `deal`, `thing`, `things`, `stuff`, `keep`, `hearing`, `hear`, `heard`, `up`, `going` — get stripped the same way ordinary stop words do, so *"that mercury thing I keep hearing about"* reduces to just `mercury` instead of carrying along filler that pollutes the actual search.

A small set of recognized colloquial question patterns (`"what's the deal with"`, `"what's up with"`, `"what's this about"`, `"what's the story with"`) are explicitly recognized as legitimate definitional questions, not nonsense — handled with a substring check rather than `.startswith()`, since an earlier version missed these phrases entirely when they appeared mid-clause rather than at the very start of the query.

## What decomposition doesn't do

It splits independent intents — it deliberately does *not* try to handle a leading conditional structure (*"if the back door is unlocked, let me know"*), since that's not a flat list of separate questions, it's a single statement with a condition and a consequence. That's [Conditional Query Detection](Conditional-Query-Detection)'s job, and it runs *before* decomposition at the top level — but a decomposed sub-query can still turn out to be conditional in its own right, which is why conditional detection gets re-applied to every sub-query decomposition produces, not just the original full query.
