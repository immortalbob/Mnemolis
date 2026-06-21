# The SearXNG Timeout Lesson

`"Error reaching SearXNG: connection failed"` kept appearing intermittently in real testing, and the first reasonable assumption was rate limiting — SearXNG's own bot-detection limiter, or one of the upstream search engines it queries blocking repeated rapid requests. That assumption turned out to be half right, and chasing down the other half surfaced a genuinely different, more interesting lesson about verifying configuration rather than trusting it.

## Ruling out the limiter — correctly, this time

SearXNG's own rate limiter (`limiter: true/false` in `settings.yml`) protects *it* from too many incoming requests. Checking the actual running config showed `limiter: false` — already disabled, not the cause. Worth noting explicitly: this is a different layer from what was actually wrong, and confirming it wasn't the cause (rather than assuming it must be, since rate limiting was the first guess) was the right move before chasing anything further.

## Finding the real cause in SearXNG's own logs

Checking SearXNG's container logs directly — not Mnemolis's — surfaced two distinct, real issues:

1. **Genuine upstream rate limiting** — Brave specifically was returning `SearxEngineTooManyRequestsException: Too many request (suspended_time=180)`, a real, expected consequence of a night's worth of rapid testing against one engine.
2. **A timeout set too aggressively for real-world latency** — `request_timeout: 3.0` (SearXNG's own default), while several engines (Google, Wikipedia, Startpage, DuckDuckGo) were genuinely taking 20+ seconds to respond under completely normal conditions, not just when rate-limited. The logs showed this explicitly: `HTTP requests timeout (search duration: 20.5s, timeout: 3.0s)`. The timeout wasn't catching hung requests — it was killing requests that would have succeeded if given a realistic amount of time.

The fix: raise `request_timeout` to 10 seconds and uncomment/set `max_request_timeout` to 20 seconds, matching the real observed latency rather than SearXNG's conservative-by-default value.

## The fix that looked complete, but wasn't actually live

The config file was edited correctly. `request_timeout: 10.0` and `max_request_timeout: 20.0` were genuinely present in `settings.yml`, confirmed by directly reading the file. And yet, sometime later, `/health` reported the exact same error again — `read timeout=3` — the literal old value, as if the edit had never happened at all.

This is the actual lesson, and it's a general one, not specific to SearXNG: **a config file being correct on disk doesn't mean the running process is using it.** The file was right. The *container* had not been restarted since the edit was made — it was still running with whatever it had loaded at its last actual startup, which predated the fix. Checking `docker ps`'s uptime against the actual timeline of when the edit happened confirmed this directly, and `docker exec searxng grep ... /etc/searxng/settings.yml` — reading the file from *inside* the live container, not trusting the host-side copy — was the step that actually proved the live process's view of its own config, rather than inferring it from the file on disk.

```text
   Config file on disk: request_timeout: 10.0  ✓ correct
                    │
                    ▼
   Is the RUNNING PROCESS actually using this value?
   (Don't assume — check from inside the container)
                    │
          ┌─────────┴─────────┐
          ▼ not restarted       ▼ restarted
   Still running on the     Now genuinely using
   OLD value from its       the new value —
   last actual startup —    verify with a real
   the file being right     health check, not
   on disk is irrelevant    just re-reading the
   to what's actually       file again
   loaded in memory
```

## Why `/health` is what actually caught this, twice

The first time, this was found through manual, deliberate debugging — reading SearXNG's logs by hand, checking `docker ps`, comparing timestamps. The second time it happened — genuinely, independently, after the fix had supposedly already been applied — [Health & Observability](Health-and-Observability)'s live source connectivity check caught it immediately, on the very next `/health` call, with the exact error message attached, no manual log-diving required. That's the real payoff of building real, live checks rather than config-presence checks: the same class of problem, caught the second time almost instantly, specifically because something was actively watching for it rather than waiting to be debugged by hand again.

## The general lesson, stated plainly

When a fix doesn't seem to be taking effect, check whether the *process* actually picked it up before assuming the fix itself is wrong. A correctly-edited file, a correctly-applied database migration, a correctly-set environment variable — none of these matter if the thing reading them hasn't been restarted, reloaded, or re-deployed since. Verify from inside the running thing itself, not from the artifact you edited.
