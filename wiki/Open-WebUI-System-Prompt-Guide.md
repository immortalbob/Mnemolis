# Open WebUI System Prompt Guide

This is a real, verified-working system prompt for any Open WebUI deployment (or similar tool-calling setup) where Mnemolis is the only tool available. It exists because of a genuine, repeatedly-observed failure mode: a tool-calling LLM, left to its own judgment, will often silently narrow a compound question down to a single tool call — answering only part of a multi-part question, or pre-splitting it itself instead of trusting Mnemolis's own [decomposition](Query-Decomposition) to handle the full, original sentence.

## The prompt

```text
You are a voice and chat assistant for a private homelab, backed by a tool called Mnemolis Knowledge Aggregator. Mnemolis is your only source of real information — you have no other way to know anything current, local, or specific to this household.

ALWAYS call the Mnemolis Knowledge Aggregator tool for every user message that asks a question, requests information, or could be answered with a search — with no exceptions for questions that seem simple, common-sense, or already familiar to you. Never answer from your own training data when a tool call could answer instead. If you're unsure whether to call it, call it.

Call the tool exactly once per user message, passing the user's FULL message as the query, with source set to "auto". Do not pre-split the question into pieces yourself, do not paraphrase it, and do not only forward part of a multi-part question — Mnemolis already knows how to break apart compound questions ("the weather and is the door locked", "if X then Y", multiple topics in one sentence) far more reliably than you can by guessing. Sending the whole message as-is is correct even if it contains several unrelated questions, conditional phrasing, or casual/run-on language.

Only skip the tool call entirely for messages that are pure greetings, thanks, or social chat with no informational content (e.g. "hi", "thanks", "good morning").

After receiving the tool's result, answer using only what it returned. If the result says information wasn't found or doesn't apply, say so plainly rather than guessing or filling the gap with your own assumptions. Do not invent citations or facts beyond what the tool gave you.

Keep your final answer natural and conversational — the tool's raw output may include source labels like [FORECAST] or [HA] for your own reference; don't repeat those labels verbatim to the user unless they ask where information came from.
```

## Why each part is there

**"Call the tool exactly once... passing the FULL message"** is the single highest-leverage line in the whole prompt. The real, observed failure mode wasn't the model picking the wrong tool — Mnemolis only exposes one — it was the model deciding *how much* of a compound question to forward, sometimes answering only one clause of a multi-part question from its own training data and never calling the tool for the rest. Telling it explicitly not to pre-split removes a judgment call that kept failing, and hands that judgment back to [Query Decomposition](Query-Decomposition), which is specifically built to handle exactly this.

**"Call it exactly once"** guards against the opposite failure — a model deciding to make several narrow tool calls instead of trusting `auto` to handle decomposition, fusion, and routing itself. Multiple narrow calls would also mean the model is doing the splitting *itself*, with all the same unreliability "don't pre-split" was meant to prevent, just spread across several calls instead of one truncated answer.

**The greeting carve-out** exists so the assistant doesn't feel robotic on "hi" or "thanks" while staying strict everywhere else — a blanket "always call the tool, no exceptions" reads oddly in a chat interface for purely social messages with no actual informational content.

## Verified, not just plausible-sounding

This was tested directly against a real compound query: *"hey, is it gonna rain later this week, and also can you check if my services are all running okay, plus whats the deal with that whole black hole thing everyone's talking about"* — three real intents, casual phrasing, including the [discourse-framing](The-Discourse-Framing-Investigation) pattern deliberately included to see whether it would survive being forwarded as a single, full tool call.

Checking Mnemolis's own logs after the response confirmed it directly: **exactly one** `POST /search` request, with the entire original sentence as the query, and a `Decomposed query into 3 parts` log line right after it — rain, services, black holes, all three correctly split out by Mnemolis itself, none of them dropped or pre-answered by the model before the tool call happened. That's the direct, log-level evidence this prompt actually closes the gap it was written for, not just a plausible-sounding theory about why it should.

## A real caveat — this is a starting point, not a universal constant

This was written and verified against one specific local model's actual behavior (Qwen3:8b). A different model — especially a more capable one, or one with genuinely reliable native multi-tool-call reasoning — might not need such explicit, repeated instructions about not pre-splitting, or might respond to different phrasing better. Treat this as a tested baseline to adapt for your own model, not a drop-in guarantee that any model will behave identically.

## Where this connects to the rest of the architecture

This prompt's entire reason for existing is the same one behind [MCP Server](MCP-Server)'s design — Mnemolis deliberately exposes one tool, not several per-source tools, because Mnemolis itself is better positioned to decide which source(s) actually apply to a question than a general-purpose calling model is. The system prompt above is the other half of that same decision: making sure the calling model actually *gives* Mnemolis the full question to make that decision on, instead of making the decision itself first and only handing over a fragment.
