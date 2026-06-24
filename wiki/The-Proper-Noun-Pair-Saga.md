# The Proper-Noun-Pair Saga

[Query Decomposition](Query-Decomposition) needs to split *"check the weather and is the door locked"* into two intents, while leaving *"what's happening with Iran and Israel"* completely intact — the word `"and"` looks identical in both sentences, but only one of them should ever be split. The guard that tells these apart is the single most-revised piece of logic in this project's history: five distinct bugs, found two different ways — four by deliberately escalating to harder real queries once a simpler one passed, and a fifth found by a careful, complexity-driven re-read of the function rather than any new test.

This page tells the story in the order the bugs were actually found, because the order matters — each fix narrowed the problem just enough to expose the next one.

## Bug 1 — unbounded scope

The first version of the guard, on finding a conjunction, looked at whatever text followed it to decide if a proper-noun pair was present. The bug: it measured "after" using `query.index(conj)`, which only ever finds the *first* occurrence of a conjunction in the whole string — and the "after" text it then examined ran to the *end of the entire query*, not just the next clause.

For a short test sentence this never showed up. For a real, longer sentence — *"Iran and Israel right now, and also did the front door do anything weird"* — the "after" span being measured was dozens of words long, so the length check meant to recognize "this looks like a bare name" never matched anything, because it was being compared against a sentence's worth of unrelated text instead of just the next word or two.

**Fix:** bound the "after" text to stop at the first comma or the start of any other conjunction, so the comparison is against the immediate next clause, not whatever happens to follow it for the rest of the sentence.

## Bug 2 — trailing filler broke the length check

With scope correctly bounded, the next real query exposed a second problem: *"what's happening with Iran and Israel right now"* — the bounded "after" segment is `"Israel right now"`, three words, not the one or two words the length check expected for "this looks like a bare name."

The check was conflating two different things: how long the *proper noun itself* is, versus how long the *entire bounded segment* is. `"Israel right now"` absolutely starts with a bare proper noun, even though trailing filler follows within the same comma-bounded segment.

**Fix:** only the word immediately after the conjunction needs to look like the start of a name. A one-or-two-word proper noun followed by lowercase filler within the same segment still counts — `after_words[1][:1].islower()` is the actual check that lets `"Israel right now"` pass while still correctly rejecting a segment where the second word is *also* capitalized (which would suggest a longer, unrelated capitalized phrase rather than a short name).

## Bug 3 — a global veto discarded unrelated real intents

This is the bug with the most consequential blast radius. The guard, once it found *any* proper-noun pair anywhere in the query, canceled splitting **entirely** — for the whole sentence, not just at that one occurrence.

*"what's happening with Iran and Israel right now, and also did the front door do anything weird while I was out, and is it gonna be hot enough this week, also what's the deal with raspberry pi gpio permission errors"* has a real, harmless proper-noun pair and three genuinely separate, real questions. The global veto discarded all three of those real intents — door status, weather, and GPIO troubleshooting — just because *Iran and Israel* happened to be present somewhere in the same sentence.

**Fix:** redesigned as a per-occurrence check, not a global gate. `_is_proper_noun_pair_at(query, idx, conj_len)` evaluates one specific conjunction occurrence and returns whether *that one* should be protected — it makes no judgment about the rest of the query. A query can now contain a protected pair and three separate real intents in the same sentence, with all four correctly handled: the pair stays intact, the three intents get split out normally.

## Bug 4 — the fix for bug 3 quietly discarded real content

The per-occurrence redesign fixed the global-veto problem, but introduced a new, subtler one of its own — found only when a megaquery test happened to place a real intent's content directly adjacent to a protected pair, something none of the simpler tests up to that point had constructed.

The decomposition loop's skip logic, on finding a protected occurrence, advanced its scan position to just past the skipped conjunction — and that same advance was also resetting where the *next kept part* would begin. *"also whats happening with Iran and Israel, plus I keep getting a weird numpy import error..."* — skipping the protected *"Iran and"* occurrence correctly avoided splitting there, but it also silently discarded `"also whats happening with"` entirely, since the next real part's start position had been reset to right after the protected pair, not to where the previous real content had actually begun.

**Fix:** track two positions separately — `segment_start` (where the *current accumulating part* began) and `search_from` (where to resume *looking* for the next conjunction occurrence). Skipping a protected pair now advances `search_from` only, leaving `segment_start` untouched, so real content preceding a protected pair accumulates into the next real part instead of vanishing.

## Bug 5 — the pronoun "I" mistaken for a proper noun

Found differently from the first four — not via a megaquery test or real production usage, but via a deliberate, thorough re-read of the entire function during a complexity-reduction investigation, prompted by `_decompose()` carrying the highest cyclomatic complexity score left in the codebase after several other functions had already been simplified. Given this function's bug history, the investigation was scoped explicitly to read every line fresh rather than scan for an obvious extraction — and it found something more significant than a refactor opportunity.

The guard's capitalization check (`before_tail[:1].isupper() and after_head[:1].isupper()`) has no concept of the pronoun *"I"* — which is always capitalized in English regardless of sentence position, making it look exactly like a proper noun to a naive check. *"what's happening in Texas, plus I need help with my router"* was being incorrectly protected as a bare proper-noun pair (*"Texas"* + *"I"*, as if it were a real pair like *"Texas and Arizona"*), causing the **entire query to not split at all** — even though *"X, plus I need..."* is an extremely common, completely natural way to phrase a second, unrelated request, not a contrived edge case.

**Fix:** exclude the pronoun *"I"* explicitly from counting as the proper-noun half of a pair. No broader pronoun list was needed — no other common English pronoun (he/she/they/we) is unconditionally capitalized regardless of context the way *"I"* uniquely is, so no other word produces this exact false-positive shape.

A genuine, additional improvement surfaced while updating the existing megaquery test for this fix: that test's own expected part-count of 3 had unknowingly baked in the limitation of this very bug — the numpy/GPIO clause in that test query was permanently stuck merged into part 1 alongside *"Iran and Israel,"* because *"Israel, plus I keep..."* kept getting misidentified as a protected pair. With the fix in place, the same query now correctly produces 4 distinct parts. Every original content-integrity assertion (Iran and Israel staying together, no real content lost) still holds true exactly as before — only the count was wrong, for a reason nobody had found yet when that test was originally written.

## What held up under the hardest test thrown at it

After the first four fixes, a single query combining a leading conditional, a protected proper-noun pair sitting directly adjacent to unrelated technical content, a second independent conditional, and a colloquial phrase — five distinct mechanisms in one sentence — decomposed correctly, with the protected pair intact, the adjacent real content preserved, and every other genuine intent split out properly. See [Conditional Query Detection](Conditional-Query-Detection) and [Query Decomposition](Query-Decomposition) for how those other mechanisms interact with this one.

## The actual lesson

Every one of the first four bugs was invisible until tested against a query specifically constructed to be harder than whatever had been tested before it. The first three were each found by deliberately escalating to messier, more realistic phrasing once the simpler case passed — the fourth was found only by combining several already-fixed mechanisms into one sentence and checking the result against real production data. The fifth was found a different way entirely: not by constructing a harder test, but by deliberately, slowly re-reading code that already had a passing test suite and a real production track record, specifically because a complexity score flagged it as worth a second, careful look. None of these five would have been caught by a single comprehensive-looking test written up front, or by complexity tooling alone without someone actually reading what the number was pointing at — they required either genuinely adversarial, incrementally harder real usage, or the discipline to read thoroughly even when nothing obviously looked broken.
