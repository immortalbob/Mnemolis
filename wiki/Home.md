# Mnemolis Wiki

Mnemolis is a self-hosted knowledge broker for homelabs — it routes natural-language queries to the right backend (offline knowledge, weather, news, live web, service monitoring, smart home state) and merges results when a question genuinely needs more than one source.

This wiki holds the deep-dive material that doesn't belong in the [README](https://github.com/immortalbob/Mnemolis/blob/main/README.md) — mechanism-level detail, design rationale, and the real bugs found and fixed along the way. The README stays lean: what it is, quick start, core config, API reference. Start there if you just want to get it running. Come here when you want to know *why* it works the way it does, or *what* it would actually take to break it.

---

## Getting Started

- **[About Mnemolis](About)** — what it is, what it isn't, and the philosophy behind it (local-first, privacy-preserving, subscription-free)
- **[First-Time Setup](First-Time-Setup)** — full stack vs. Mnemolis-only install paths, the actual order of operations, and the things that bite people on a first install
- **[Configuration Reference](Configuration-Reference)** — every environment variable, grouped by what it actually controls, with the reasoning behind each default, including optional API key authentication
- **[Home Assistant Integration](Home-Assistant-Integration)** — token setup, the analytical queries the `ha` source handles beyond HA's own built-in intents, and how it participates in fusion
- **[Troubleshooting](Troubleshooting)** — the real problems found and fixed this project's life, indexed by symptom (start here if something's broken)

## Core Concepts

- **[Sources](Sources)** — what each backend (`kiwix`, `forecast`, `news`, `web`, `uptime`, `ha`, `changes`) actually does, and where its data comes from
- **[Routing](Routing)** — how a query gets from "what you typed" to "which source(s) answer it" — keyword matching, LLM-assisted selection, and the discourse-framing bias
- **[Query Decomposition](Query-Decomposition)** — how compound questions get split into independent sub-intents, including the mixed-conjunction and proper-noun-pair handling
- **[Conditional Query Detection](Conditional-Query-Detection)** — the `"if X, Y"` feature: what it detects, what it deliberately doesn't, and how it avoids guessing when it shouldn't
- **[Fusion](Fusion)** — how multiple sources get queried concurrently and merged into one coherent response
- **[Snapshot Engine & Changes](Snapshot-Engine-and-Changes)** — the background scheduler that captures source state over time, diffs it, and answers "what changed since X"
- **[Caching](Caching)** — the result cache and routing cache: what's cached, for how long, and how size is bounded
- **[MCP Server](MCP-Server)** — the Model Context Protocol interface, how it differs from the REST API, and connecting Claude Desktop or other MCP clients

## Kiwix Deep Dive

- **[Kiwix Disambiguation](Kiwix-Disambiguation)** — the multi-candidate search-and-score approach that replaced trusting a single LLM guess, and why
- **[Kiwix Scoring](Kiwix-Scoring)** — the exact point values behind article selection, spelled out in full
- **[Multi-Book Fusion](Multi-Book-Fusion)** — when and why Mnemolis merges results from more than one Kiwix book instead of picking just one

## Web & News Deep Dive

- **[Confidence-Aware Fusion](Confidence-Aware-Fusion)** — why web and news results get scored at all, and the exact weights behind that scoring
- **[Query Expansion](Query-Expansion)** — the alternate-phrasing mechanism for web search, and why it doesn't apply to news

## Operations

- **[Health & Observability](Health-and-Observability)** — what `/health` and `/logs/stats` actually check, including fallback visibility and background job health
- **[Backup & Restore](Backup-and-Restore)** — the four data files, the Docker volume naming gotcha, and how to actually restore from a backup
- **[Benchmarks](Benchmarks)** — real performance data across every major release, cold cache vs. warm cache
- **[Adding a New Source](Adding-a-New-Source)** — the four files a contributor touches, and the one optional fifth (fallback chains)

## Design History — Real Bugs, Real Fixes

These pages exist because the lessons are genuinely worth keeping, not just because something broke once. Each one is a real investigation — root cause traced, fix verified against production data, sometimes with a second bug found in the first fix.

- **[The Proper-Noun-Pair Saga](The-Proper-Noun-Pair-Saga)** — four distinct, sequential bugs in one piece of logic protecting "Iran and Israel" style phrases from incorrect splitting
- **[The Discourse-Framing Investigation](The-Discourse-Framing-Investigation)** — why "everyone's obsessed with X" queries routed past Kiwix, and why the fix needed two separate parts
- **[The SearXNG Timeout Lesson](The-SearXNG-Timeout-Lesson)** — a correctly-edited config file that silently didn't take effect, and how `/health` caught it
- **[The Recursion Design Bug](The-Recursion-Design-Bug)** — how an over-cautious depth counter in conditional detection blocked its own necessary logic, and the simpler design that replaced it

## Reference

- **[Roadmap](Roadmap)** — what's done, what's tracked, what's speculative, and what's deliberately tabled
- **[Open WebUI System Prompt Guide](Open-WebUI-System-Prompt-Guide)** — the verified-working prompt that stops an LLM from silently narrowing a compound question to one tool call, with real before/after evidence
- **[Contributing](Contributing)** — proposed source modules looking for contributors, and what a good PR looks like

---

*This wiki is a living document. If something here goes stale, that's worth fixing the same way a stale README section was — the project has a real track record of catching exactly that kind of drift.*
