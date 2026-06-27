# First-Time Setup

There are two real install paths, and picking the right one up front matters more than it might seem — going down the wrong path doesn't break anything permanently, but it does mean extra steps untangling things later.

## Requirements

- Docker + Docker Compose
- A shared Docker network for container communication (`mnemo-net` by convention — see the note on this below)
- At least one of the supported backends already running and reachable on that network

## Path 1 — Full stack (recommended if you're starting from nothing)

The repo includes an example compose file and SearXNG config that bring up several services together — Mnemolis itself, plus the backends it talks to that don't typically already exist in a homelab (Kiwix, FreshRSS, SearXNG).

```bash
git clone https://github.com/immortalbob/Mnemolis
cd Mnemolis

docker network create mnemo-net

cp docker-compose.example.yml docker-compose.yml
# Fill in credentials, your coordinates, and secret_key in searxng/settings.yml

docker compose up -d
```

**What this deliberately doesn't bring up:** Home Assistant, your LLM backend, and Uptime Kuma. These are excluded on purpose — they're the kind of long-running services most homelabs already have set up with their own existing configuration, and the example compose isn't trying to replace or duplicate that. If you're running any of these in Docker already and want Mnemolis to reach them, connect them to the same network explicitly:

```bash
docker network connect mnemo-net ollama
docker network connect mnemo-net homeassistant
```

## Path 2 — Mnemolis only (if your backends already exist)

```bash
git clone https://github.com/immortalbob/Mnemolis
cd Mnemolis
# Edit docker-compose.yml with your settings
docker compose up -d
```

## Confirm it's actually running

```bash
curl http://your-host:8888/health
```

This isn't just "did the container start" — it's a real, live check against every configured backend. See [Health & Observability](Health-and-Observability) for exactly what it checks and why that matters; a fresh install with an unreachable backend will show up here clearly, not as a vague "Mnemolis isn't working" symptom discovered later. Full interactive API docs are at `http://your-host:8888/docs`.

## A real gotcha worth knowing about before you hit it

Docker Compose automatically prefixes named volumes and, in some setups, networks with your **project name** — which defaults to whatever folder your `docker-compose.yml` lives in. This rarely matters for a first install, but it absolutely matters the moment you're connecting a *separately-managed* service (one with its own compose file, in its own folder) to the same `mnemo-net` network: that other service's compose file needs to reference the exact same network name, and if its own folder structure or naming history doesn't match, you can end up with two differently-prefixed networks that look similar but aren't the same one. [Backup & Restore](Backup-and-Restore#a-real-gotcha-worth-knowing-before-you-need-it-volume-naming) covers the volume-naming version of this same issue in more depth; the underlying lesson is the same either way — check what Docker actually created (`docker network ls`, `docker inspect <container> --format '{{json .NetworkSettings.Networks}}'`) rather than assuming the name in a YAML file is the name Docker is actually using.

## What to set up next, roughly in order of how much it unlocks

1. **At least one Kiwix ZIM file** — without this, `kiwix` (the most architecturally complete source) has nothing to search. See the README's Kiwix ZIM files section for where to get them.
2. **An LLM backend** (Ollama or an OpenAI-compatible endpoint) — without this, [Routing](Routing) falls back to keyword-only matching and Kiwix falls back to a fixed "search Wikipedia first" behavior, losing [disambiguation](Kiwix-Disambiguation), [query expansion](Query-Expansion), and the LLM-assisted parts of routing entirely. Mnemolis still works without one, just with meaningfully less of its actual intelligence available.
3. **Your home coordinates** for `forecast` — a one-time config value, no ongoing service required. Worth setting deliberately rather than skipping: leaving it blank now correctly reports `forecast` as not configured, the same graceful degradation every other optional source gets.
4. **Whatever subset of FreshRSS, SearXNG, Uptime Kuma, and Home Assistant you actually use** — each is independently optional; Mnemolis degrades gracefully and simply reports that source as unreachable in `/health` if it's not configured or not running, rather than failing entirely.

## Where to go from here

[Configuration Reference](Configuration-Reference) for every environment variable and what it actually controls. [Home Assistant Integration](Home-Assistant-Integration) specifically if HA is part of your setup, since it has its own token-generation step and a distinct category of queries it answers. [Troubleshooting](Troubleshooting) if something isn't behaving the way this page or the README led you to expect.

---

## Development Notes

If `forecast` ever returns weather for the wrong location after setup, see [Troubleshooting](Troubleshooting#weather-forecast-looks-completely-wrong-for-your-location) — this is a known, now-fixed historical issue, not something currently expected from a correct configuration.
