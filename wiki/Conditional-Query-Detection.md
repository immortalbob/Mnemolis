# Conditional Query Detection

Conditional detection recognizes a leading **"if X, Y"** structure — *"if the back door is unlocked, let me know"* — and answers it honestly: it searches the condition, and either gives a real yes/no verdict (when the underlying source genuinely supports one) or presents the raw result and says so plainly when it doesn't. Mnemolis has no reminder or trigger capability, so it can never actually act on the consequence — what it can do is make sure the response is framed around the condition's real, current answer instead of just restating the question back.

## Why the pattern is this narrow

The detector only matches a leading `"if "` / `"should "` / `"in case "` followed eventually by a comma:

```regex
^(if|should|in case)\s+(.+?),\s*(.+)$
```

This is deliberately restrictive, and the restriction is the actual design decision worth understanding. **"If" is genuinely ambiguous in English** — it has a conditional sense (*"if it's raining, bring an umbrella"*) and a "whether" sense (*"check if the lights are on"* means *"check whether the lights are on,"* not a real condition at all). The whether sense never shows up at the very start of a sentence followed by a comma; it's always embedded after a verb like "check," "see," or "tell me." Restricting to the leading-comma form sidesteps the ambiguity entirely, rather than trying to guess at it from surrounding verbs.

A few phrasings are deliberately, permanently out of scope as a result:

- **Mid-sentence or trailing "if"** — *"remind me to bring an umbrella if it's raining"* doesn't match. Out of scope for the same reason as above.
- **"Let me know if X"** — genuinely ambiguous even to a human reader. Could mean "tell me the current status" or "notify me if it changes." Not safely interpretable either way, so it's left alone.
- **No comma at all** — *"if the front door is unlocked tell me"* (missing the comma) doesn't match. This is a real, accepted limitation, not an oversight — distinguishing this reliably from "whether" usage would require actual grammatical parsing, not pattern matching, and that's a different kind of project.

## Honest abstention — the actual point of this feature

Once a condition is extracted, the real question is whether Mnemolis can say anything meaningful about whether it's *true*. This is restricted to exactly three sources with a genuinely structured, binary signal:

```text
_YES_NO_INTERPRETABLE_SOURCES = {"ha", "uptime", "forecast"}
```

- **`ha`** — checks for "locked"/"unlocked" keywords in the condition, then in the result
- **`uptime`** — checks "down"/"up"/"running"/"working" in the condition against "all...up" / "down" in the result
- **`forecast`** — checks rain/storm-related language in the condition against rain/storm/clear language in the result

Every other source — Kiwix, web, news — is **never** interpreted, on purpose. There's no structured signal to check against in free text, and guessing wrong would actively mislead rather than just be unhelpful. Even within the three interpretable sources, a genuinely subjective condition like *"if it's hot enough this week"* correctly returns no verdict, because there's no universal threshold for "hot enough" to check against — `_interpret_yes_no` returns `None` here, not a guess.

```text
                 Condition extracted, searched
                              │
                              ▼
                  Was the answer source one of
                  ha / uptime / forecast?
                              │
                  ┌───────────┴───────────┐
                  ▼ no                     ▼ yes
        Present the real result      Does the condition's language
        honestly, note it's          map to a recognizable keyword
        conditional, let the         pattern for THIS source?
        person judge for                     │
        themselves                  ┌────────┴────────┐
                                     ▼ no               ▼ yes
                          Same honest        State an explicit verdict:
                          presentation        "It IS / IS NOT the case
                          as the "no"         that {condition} — so the
                          branch              suggested action may or
                                              may not apply"
```

Wrong is worse than uncertain. That's the entire design principle behind this feature, and it's the reason the interpretable-sources set is a short, explicit allowlist rather than something that tries to generalize.

## Recursion, and the bug that taught us how to do it right

A query can be conditional *without starting with "if"* — *"what is the weather and if the back door is unlocked, let me know"* doesn't match the leading pattern at all, but [Query Decomposition](Query-Decomposition) will still split it into two sub-queries, and the second one (*"if the back door is unlocked, let me know"*) absolutely is conditional. Conditional detection is re-applied to every decomposed sub-query for exactly this reason.

The first version of this re-check recursed on the *original* `"if X, Y"` string, with a manual depth counter meant to prevent runaway recursion. That counter introduced a real bug: it incremented *before* the conditional was actually consumed, which meant the recursive call's own necessary re-detection of the very same conditional got blocked by the counter that was supposed to be protecting against infinite recursion that was never actually possible in the first place. The fix — and the much simpler design that replaced the depth counter entirely — is told in full in [The Recursion Design Bug](The-Recursion-Design-Bug).

## When a real second question follows the conditional

*"if any services are down, let me know, and also what's the weather"* has a genuine second intent hiding after the conditional's consequence. An early version of the consequence-extraction regex was greedy and captured everything to the end of the string — *"let me know, and also what's the weather"* — silently swallowing the weather question into plain descriptive text that never got searched at all.

This is fixed by checking the extracted consequence for a trailing conjunction and, if found, splitting off the remainder and searching it independently, merging it back into the final response with its own source attribution. The same fix surfaced a second, smaller bug — the exact `[FUSION — FUSION]` double-header issue described in [Fusion](Fusion) — at a new call site that hadn't existed when that bug was first found and fixed elsewhere.

## A real, structural latency cost of handling the condition and remainder separately

The condition and the remainder are each routed with their own full, independent call — search the condition, get a verdict, *then* search the remainder, merge the two. This is sequential, not concurrent: if either half hits a slow LLM call or a slow fusion fan-out, the total wait is additive, not the longer of the two. A condition that takes 2 seconds to resolve and a remainder that takes 6 adds up to roughly 8 seconds total, not 6.

This was found via real, live latency data, not a synthetic benchmark — several real `conditional_with_remainder`-shaped queries logged by [Adversarial Self-Testing](Adversarial-Self-Testing#a-real-structural-latency-characteristic-not-a-bug-documented-rather-than-fixed) showed latency meaningfully above what either half would cost alone. It's deliberately **not** being changed to run concurrently: that would mean touching the same conditional-handling code that's already had two separate, carefully-reasoned bug fixes (the recursion depth-counter bug above, and the greedy-consequence-regex bug just above this section) — a real, working area of the codebase with a documented history of subtle bugs whenever it's modified. The risk of a new concurrency bug (shared cache writes from two threads resolving at once, for one) outweighs shaving a few seconds off a query shape that, in practice, isn't the common case. Recorded here as a known, accepted, structural cost of the current design — not a defect waiting to be fixed.

## What it looks like end to end

```text
Query:  "if the back door is unlocked, let me know"
                          │
                          ▼
        condition = "the back door is unlocked"
        consequence = "let me know"
                          │
                          ▼
        Search the condition → routes to `ha`
        Real result: "Back Door: locked"
                          │
                          ▼
        ha is interpretable. Condition mentions
        "unlocked". Result says "locked" — the
        opposite. Verdict: NOT the case.
                          │
                          ▼
"This was a conditional question: 'if the back door
is unlocked, let me know.'

It is NOT the case that the back door is unlocked —
so the suggested action (let me know) may not apply.

Back Door: locked"
```

The real underlying result is always preserved in the response, regardless of the verdict — framing adds context, it never replaces or hides the actual data.
