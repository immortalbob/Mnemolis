# The General-News-Query Detection Bugs

`freshrss.py`'s `_is_general_query()` decides whether a `news` query should skip relevance scoring entirely and return the unfiltered feed, or be treated as a specific-topic request and scored like a `web` result. It's a small function with an outsized real-bug history — three separate, sequential gaps, found across three different releases, all in the same general shape: the check correctly recognized the *grammatically clean* version of a request, but not the messier, more natural way people actually ask out loud.

## v2.7.0: word-level matching missed the full-string case

The earliest version of this check matched word-by-word against a list of recognized general-news terms. `"what's happening"` — a genuinely common, natural way to ask for the news — failed, because nothing in the function ever checked the *full query string* against known general-query phrases before falling through to word-level matching. Fixed by checking the full string first.

## v3.29.0: nine of nine natural phrasings failed at once

A deliberate complexity-investigation pass read `app/sources/freshrss.py` end to end for the first time in a long while. Most of the file held up — a genuinely convoluted-looking canonical-URL extraction expression turned out to correctly handle every real edge case when traced through precisely. `_is_general_query()` didn't hold up.

The check required every word in the query, after stop-word removal, to be a recognized general-news term — but `_STOP_WORDS` only ever covered formal grammatical filler ("the", "is", "about"), never the common request verbs people actually use when asking out loud. A direct test against nine realistic phrasings — *"tell me the news"*, *"give me the headlines"*, *"show me my feeds"*, *"any news today"*, and others — found **all nine failing**, each one incorrectly scored against literal words like "tell" or "give" instead of cleanly returning the general feed. Fixed by expanding `_STOP_WORDS` to include the common request verbs and modifiers actually missing (tell, give, show, read, check, catch, any, today, update, and others).

**A second, distinct gap was found while fixing the first.** *"whats new"* (no apostrophe) still failed even after the verb additions — the bare word "whats" was never itself a recognized stop word. `_GENERAL_QUERIES` already handled both apostrophe forms of the full *"what's happening"* / *"whats happening"* phrase (the v2.7.0 fix above), but not the standalone contracted word on its own.

**A real interaction bug was caught before it shipped, not after.** The obvious fix — add "whats" to `_STOP_WORDS` — was checked against a second realistic case first: *"catch me up on whats happening"*. Naively stripping "whats" as a stop word before the multi-word phrase check ever ran would have broken the match against the existing "whats happening" entry, since stop-word stripping happens first in the pipeline. Fixed by checking multi-word `_GENERAL_QUERIES` phrases against the *original* query text directly, deliberately independent of stop-word stripping — then verified this doesn't introduce a new false-positive risk in the other direction either: *"what's happening with bitcoin"* and *"what's the latest news about bitcoin"* both correctly still classify as specific-topic queries, since the unmatched remainder ("bitcoin") gets checked and rejected, not just blindly substring-matched against the whole query.

All three fixes were verified together against a 23-case sweep — every previously-fixed false-positive regression test, every newly-found phrasing gap, and the interaction-bug check — before shipping, rather than testing each fix in isolation and hoping they composed correctly.

## The lesson

The same shape of bug, three times: a check that's *technically* correct for the clean, grammatically formal version of a request, but blind to the casual, verb-first way a real person actually phrases it out loud. None of these were found by inspection — each one came from deliberately constructing realistic test phrasings and running them, not from reading the code and reasoning about what it should do.
