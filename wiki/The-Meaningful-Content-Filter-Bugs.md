# The Meaningful-Content-Filter Bugs

[Query Decomposition](Query-Decomposition)'s meaningful-content filter exists to decide which fragments of a split query are real intents worth keeping, and which are stray leftovers — stop words, filler, a stranded comma — worth discarding. It's a different mechanism from the [proper-noun-pair guard](The-Proper-Noun-Pair-Saga), which protects specific *pairs* from being split apart in the first place; this filter runs *after* a split has already happened, deciding what survives. Two real, separate bugs lived in the filter's current, stop-word-based design — both found via the same source: tracing real, live [Adversarial Self-Testing](Adversarial-Self-Testing) production data on an actual deployment, not synthetic test cases written in advance. A third, earlier bug, in the design this one replaced, is recorded here too for the same reason.

## Before this: a fixed allowlist that had zero coverage for technical vocabulary

The version of this filter that predates the current stop-word approach used a fixed list of recognized "intent words" — hand-maintained, domain by domain. Real usage found a genuine, significant gap: a query like *"I've been getting a python pigpio no permission to update GPIO error"* silently lost its actual content, because none of `"python"`, `"pigpio"`, `"GPIO"`, or any other real technical term in the sentence happened to be on the list. The fragment looked, to the filter, like it had nothing meaningful in it at all — not because it was wrong, but because the allowlist could only ever recognize words someone had thought to add in advance.

This is the structural reason the filter moved to its current design: rather than maintaining a list of recognized intent words that has to be hand-extended every time a new domain comes up, the filter instead asks "is there at least one real content word left once ordinary filler is stripped out" — a question that doesn't need to know about GPIO, Python, or any other specific vocabulary in advance, since any real noun or topic word survives stop-word stripping on its own.

## The filter, as it exists today

A candidate fragment survives if any of three checks pass, run in this specific order:

1. **Colloquial-phrase check** — does the fragment contain a recognized filler phrase (`"what's the deal with"`, `"that thing"`, etc.)? If so, keep it regardless of what's left after stripping.
2. **Real keyword check** — does the fragment contain a literal, real `INTENT_MAP` keyword phrase, the same flattened list every source's routing already uses? If so, keep it — regardless of what stop-word stripping would otherwise conclude.
3. **Length and stop-word check** — is the fragment longer than 3 characters, and does it have at least one real content word once stop words are stripped?

The order of checks 2 and 3 is the actual subject of this page. It wasn't always this order, and the bug from getting it wrong twice — once for what stop-word stripping considers "real," once for what the length gate considers "long enough" — is worth understanding in detail, since both bugs share the same root shape: a generic, blunt filter discarding something that a more specific, real-world check already knew was meaningful.

## Bug 1 — two real keywords are made entirely of stop words

`uptime`'s `INTENT_MAP` entry includes `"is it up"` and `"are they up"` — perfectly natural, real phrases a person would actually type. Both are made *entirely* of common English stop words: `"is"`, `"it"`, `"up"`, `"are"`, `"they"`. Confirmed directly against all 113 real keyword phrases across every source — these are the *only* two with this property.

When either phrase ended up as its own clause in a longer compound query — `"feeds plus is it up in addition later today also door locked as well as google"` — the stop-word check stripped every single word from it, leaving zero content words. The filter correctly concluded "nothing meaningful here" by its own stated logic, and silently discarded the entire clause. Not folded into a neighboring fragment, not logged — just gone. The query that should have decomposed into 5 parts (`feeds`, `is it up`, `later today`, `door locked`, `google`) came back with 4, missing `uptime` entirely.

**Fixed** by adding the real-keyword check (step 2 above) *before* the generic stop-word check — a real keyword phrase now always counts as meaningful, even when every individual word in it happens to be a stop word. This closes the general case, not just these two phrases by name: any future `INTENT_MAP` addition with the same all-stop-words property is automatically protected too, with no special-casing required.

## Bug 2 — the length gate ran before the keyword check that was supposed to protect against exactly this

Bug 1's fix wasn't actually sufficient on its own, and the gap took a second real production query to surface: `"everyone keeps talking about black holes, and rss"` should decompose into `["...black holes,", "rss"]`, but didn't — it stayed as one unsplit string. Tracing why landed on a different, second filter entirely: `"rss"` — confirmed the *only* real `INTENT_MAP` keyword that is itself 3 characters or shorter — was being discarded by the filter's length gate (`if len(p) <= 3: continue`), which ran **before** the keyword check that bug 1 had just added, not after.

```text
   Fragment: "rss" (length 3)
                    │
                    ▼
        Length gate: len(p) <= 3?  ──── TRUE
                    │
                    ▼
        Discarded immediately.
        The keyword check added for
        bug 1 never even runs — it's
        positioned AFTER this gate,
        not before it.
```

The fix for bug 1 added a real, working check — but adding it to the *end* of the filter chain meant it could only ever protect a fragment that survived every earlier check first. A short keyword that the length gate would discard outright never got that far.

This had a real, visible downstream consequence beyond just the missing decomposition. Once `"rss"` failed to split off as its own clause, it rode along as part of the larger discourse-framed clause sent to `kiwix` — polluting both the actual Kiwix search query and its relevance scoring with a word that had nothing to do with the real topic. Tracing that consequence is its own, separate story, told in [The Adversarial Testing Production Bugs](The-Adversarial-Testing-Production-Bugs#discourse-framing-escalation-never-ran-on-the-keyword-match-path) and [The Fusion Merge Bugs](The-Fusion-Merge-Bugs#the-same-source-merge-chain-three-bugs-found-in-sequence).

**Fixed** by reordering the filter: colloquial-phrase and keyword checks now both run *before* the length gate, not after. `"rss"` survives as its own clause regardless of its length, because it's checked against the real keyword list before anything ever asks how long it is.

## The actual lesson

Both bugs have the identical shape: a blunt, general-purpose filter (stop-word stripping; a short-fragment length cutoff) discarding something that a more specific check — "is this a real, documented keyword Mnemolis already knows about?" — would have correctly protected, if only that check ran first. Neither bug was a flaw in the *idea* of either filter; stop-word stripping and a length floor are both reasonable, generally-correct heuristics. The bug, both times, was assuming a general heuristic and a specific exception could coexist in any order, when the only correct order is specific-before-general — the same lesson [the proper-noun-pair saga](The-Proper-Noun-Pair-Saga#the-actual-lesson) arrived at independently, in a structurally different part of the same decomposition mechanism.
