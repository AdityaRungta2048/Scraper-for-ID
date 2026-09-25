# Design — Cross-Platform Streamer Identity Matcher

This document records the verified platform capabilities, the architecture, and
the exact decision / Excel behaviour. It was written before the implementation
and is kept in sync with it.

Research date: 2026-09-25.

## A. Verified Twitch capabilities (Helix)

Sources: Twitch API reference (dev.twitch.tv/docs/api/reference), the official
`twitchdev/twitch-cli` mock API (which mirrors Helix request/response shapes),
and Twitch developer-forum statements on rate limits.

| Need | Endpoint | Auth | Notes |
|---|---|---|---|
| App token | `POST https://id.twitch.tv/oauth2/token` `grant_type=client_credentials` | client id + secret | returns `access_token`, `expires_in` |
| User lookup by login / id | `GET https://api.twitch.tv/helix/users?login=a&login=b&id=…` | app token | up to 100 logins+ids per request. Fields: `id, login, display_name, type, broadcaster_type, description, profile_image_url, offline_image_url, created_at`. Unknown logins are **silently omitted** (200 with fewer items) → that is how "not found" is detected. |
| Channel info | `GET /helix/channels?broadcaster_id=…` | app token | up to 100 ids. Fields: `broadcaster_login, broadcaster_name, broadcaster_language, game_name, title, tags, content_classification_labels` |
| Search channels | `GET /helix/search/channels?query=…&first=…&live_only=false` | app token | `first` ≤ 100, cursor pagination (`pagination.cursor` → `after`). Fields: `broadcaster_login, display_name, id, game_name, title, tags, broadcaster_language, is_live, thumbnail_url`. Search is fuzzy and recency-weighted: **absence from search does not mean the account does not exist** — existence is only ever decided by `Get Users`. |
| Streams | `GET /helix/streams?user_login=…` | app token | ≤100 per call; only returns live streams (offline ≠ nonexistent). |
| Rate limits | token bucket, ~800 points/min per app token | | headers `Ratelimit-Limit`, `Ratelimit-Remaining`, `Ratelimit-Reset` (epoch s). 429 on exhaustion. |
| Errors | 400 bad request, 401 invalid/expired token (refresh once, then config error), 429, 5xx | | |

**Not available in Helix:** social links / About-panels. Social identities for
Twitch are therefore extracted from the public `description` text only.

Login format: `[A-Za-z0-9_]`, ≤ 25 chars. A source value that cannot be a valid
login (e.g. contains `-`) is "not found" without an API call.

## B. Verified Kick capabilities (Public API)

Source: the official Kick developer docs repository `KickEngineering/KickDevDocs`
(published at docs.kick.com; changelog read up to 11/08/2026), and the OpenAPI
spec referenced there (`https://api.kick.com/swagger/doc.yaml`) cross-checked
against a maintained SDK that mirrors it.

| Need | Endpoint | Auth | Notes |
|---|---|---|---|
| App token | `POST https://id.kick.com/oauth/token` form: `grant_type=client_credentials, client_id, client_secret` | | App Access Token: "can access publicly available data". |
| Channel by slug | `GET https://api.kick.com/public/v1/channels?slug=a&slug=b` | app token | up to 50 slugs (each ≤ 25 chars); **cannot be mixed** with `broadcaster_user_id`. Fields: `broadcaster_user_id, slug, channel_description, banner_picture, stream_title, category{id,name,thumbnail}, stream{is_live, language, custom_tags, viewer_count, …}`. Unknown slugs are omitted from `data`. |
| Channel by id | `GET /public/v1/channels?broadcaster_user_id=…` | app token | up to 50 |
| User (name, avatar) | `GET /public/v1/users?id=…` | app token | `user_id, name, profile_picture` (email only with user token + scope; we never request it) |
| Livestreams | `GET /public/v2/livestreams` (paginated; v1 **deprecated** 23/06/2026), `GET /public/v1/users/livestreams` | app token | only live streams — not used for existence |
| Categories | `GET /public/v2/categories` (v1 deprecated 15/01/2026) | | not needed for matching |
| Token introspect | moved to `POST https://id.kick.com/oauth/token/introspect` (old `/public/v1/token/introspect` deprecated) | | not needed |

**Not available in the official Kick API:** any user/channel **search**, and
social links. Consequences:

* Twitch→Kick candidate discovery is done by deterministic **slug-variant
  probing** (batched 50/request — cheap) plus the optional search-engine
  fallback.
* Kick social links come from `channel_description`. An **opt-in**
  (`KICK_PUBLIC_PROFILE_ENRICHMENT=false` by default) enrichment reads the
  public channel JSON that kick.com itself serves to anonymous visitors. It is
  undocumented, so it is disabled by default, never retries around bot
  protection (a challenge/403 simply means "enrichment unavailable"), and the
  pipeline works without it.

Kick rate limits are not documented; the client uses conservative configurable
concurrency, honours `Retry-After`, and backs off exponentially on 429/5xx.

## C. Architecture

```
Next.js UI ──► FastAPI ──► jobs/rows tables ──► queue (RQ+Redis, or inline thread)
                                                  │
                                           JobProcessor (asyncio, bounded concurrency)
                                                  │  per unique source id
                         ┌────────────────────────┼───────────────────────────┐
                 PlatformAdapter            CandidateGenerator          IdentityVerifier
              (TwitchAdapter/KickAdapter)   (exact → variants →        (matchers → ConfidenceScorer
               HttpClient: token mgmt,       platform search →           → DecisionEngine)
               rate limit, retry, cache      search engine)
                                                  │
                                   RowStateMachine → processing_rows (original_row kept)
                                                  │
                        ExcelExporter (openpyxl, edits a copy of the ORIGINAL workbook)
                                                  │
                        OutputVerifier (reopens both files, cell-by-cell diff) → download
```

* Adapters implement one `PlatformAdapter` interface
  (`find_exact_accounts`, `search_accounts`, `get_profiles`, `download_image`).
* `CandidateGenerator` only discovers; it never decides.
* `IdentityVerifier` runs matchers cheap-first; image hashing only for candidates
  that survive the cheap pre-filter or already carry strong link evidence.
* Every decision is stored with evidence JSON and `MATCHING_ENGINE_VERSION`.

## D. Candidate generation strategy

Per unique source id (duplicates resolved once per job and cached across jobs):

1. **Exact** lookup of the source account (existence check).
2. **Explicit links**: target-platform URLs/handles found in the source profile
   (e.g. `twitch.tv/foo` inside a Kick description) become priority candidates.
3. **Deterministic variants** of the source username / display name: case,
   `_`↔`-`, separators removed, suffix/prefix toggles (`tv, ttv, live, official,
   yt, gaming, gg, real, the`), trailing-digit trimming. Twitch: batched
   `Get Users` (100/call); Kick: batched `channels?slug=` (50/call).
4. **Platform search** (Twitch `search/channels` for username and display name).
5. **Search engine fallback** (optional Brave Search API) with
   `site:twitch.tv "name"` / `site:kick.com "name"`; results only yield handles.
6. Dedup, drop rejected pairs from manual review, cheap pre-rank by name
   similarity, cap at `MAX_CANDIDATES` (default 15) — link-derived candidates
   are always kept.

## E. Identity matching strategy

Signals (each reports `available`, `score 0..1`, `points`, `explanation`):

| Signal | Family | Strength | Notes |
|---|---|---|---|
| Explicit cross-link | link | strong | candidate ↔ source URL on the other platform. A link from the source to a *different* account on the target platform is a hard conflict. |
| Shared unique social identity | social | strong | normalized (instagram/youtube/x/tiktok/website/linktree/beacons …). Discord invites & generic hosts are weak. Each distinct shared identity adds points (diminishing). |
| Profile image | image | strong when ≥ threshold **and** not generic | pHash + dHash + aHash on normalised images (RGB, alpha on white, square; untrimmed, border-trimmed and centre-crop variants, best-aligned pair wins). On the test corpus the same picture after platform processing scores ≥ 0.91 and different pictures ≤ 0.68. Default avatars, low-complexity images, and images whose hash is seen on ≥ N unrelated accounts are "generic" → weight × 0.15. |
| Username | name | supporting | normalised (NFKC, casefold, accent fold, separators), Levenshtein/Jaro-Winkler/token/n-gram max; affix-stripped equality is only a feature; scaled by name distinctiveness (short/common names count less). |
| Display name | name | supporting | same machinery; shares the "name" family with username (they are not independent). |
| Bio | text | supporting | URLs/boilerplate/generic gaming words removed; char n-gram TF-IDF cosine (+ optional multilingual sentence-transformer); too-short/generic bios are "unavailable". |
| Content | content | weak–medium | category/game, tags, language; generic categories (Just Chatting…) down-weighted. |
| Country | country | weak | Excel country vs candidate broadcast language; +2 max, never negative beyond −1. |

## F. Confidence & decision strategy

* Points are summed (negative evidence subtracts) and mapped to
  `confidence = 100·(1 − e^(−points/CONFIDENCE_SCALE))` (default scale 16, tuned
  on the test suite; the initial 19.5 left "same distinctive name + same picture"
  at 85). The name family (username + display name) is capped at 20 points.
  Examples: exact distinctive username (15) + redundant display name (3) +
  country (2) → 71 (REVIEW, and name-only evidence is capped below MATCH anyway);
  add a strong non-generic image match (20) → 92 (MATCH). A 4–5 letter name
  earns only 35–50 % of the username weight, so "short name + same picture"
  stays in REVIEW.
* **Gates** (conditions, not only a number) for MATCH:
  * `confidence ≥ MATCH_THRESHOLD` (90), **and**
  * (≥ 1 strong signal **and** ≥ 1 supporting signal from a different family)
    **or** ≥ 2 strong signals from different families, **and**
  * no hard conflict, **and**
  * no other candidate also passing the MATCH gate (ambiguity → REVIEW).
* Name-only evidence (no strong signal) is capped below MATCH_THRESHOLD → at most
  REVIEW.
* `REVIEW_THRESHOLD ≤ confidence` (70) or conflicting signals → REVIEW.
* Otherwise NO_MATCH.
* Manual review decisions (confirm / reject) are stored and applied as explicit
  overrides on re-export; they never retune weights.

## G. Excel input/output behaviour (row state machine)

Header detection: first row (in the first 20) that contains both `id_kick` and
`id_twitch` (case/space/hyphen-insensitive). Source = the id column that comes
**first** (left-most); this must agree with fill-rate (source column mostly
filled, destination mostly empty *or* at least as filled). If they disagree, or
headers are missing, the upload is flagged ambiguous and the user must choose.

Internal row status: `PENDING → SOURCE_EXISTS | SOURCE_NOT_FOUND → MATCH |
NO_MATCH | REVIEW`, or `API_ERROR | RATE_LIMITED | TEMPORARY_ERROR |
PROCESSING_ERROR` (retryable, never written as a remark), `SKIPPED_EMPTY`,
`PRESERVED`.

Destination ID: written only when the (final, after manual review) decision is MATCH —
case A (source exists) or C (source missing, target verified by a back-link or a human).

Remarks mirror the channel-link columns (a later product decision replacing the original
per-case remark table), for both source platforms:

| Kick link present | Twitch link present | remarks |
|---|---|---|
| yes | yes | *(empty)* |
| yes | no | `no twitch id found` |
| no | yes | `no kick id` |
| no | no | `no Id on both platforms` |

A platform "has a link" when, for the source platform, the source account exists; for the
other platform, there is a match, a review candidate or any candidate not rejected in
review. Errors never touch the workbook.

Other rules:
* Empty source cell → row untouched (`SKIPPED_EMPTY`).
* Destination cell already filled in the upload → **preserved** by default
  (`EXISTING_DESTINATION_POLICY=preserve`); the row is still verified and any
  disagreement is reported in the review report. `overwrite` is available.
* A pre-existing remark that is not one of the two app phrases is never erased.
* Two informational columns, `twitch_id_link` and `kick_id_link`, are appended after the
  last used column (or reused if present), one URL each: the source channel, and the best
  other-platform account (confirmed match > review candidate > closest candidate). They
  never influence the ID or remarks columns.
* Nothing else in the workbook is modified; output is `<stem>_processed.xlsx`.

## H. How row order is guaranteed

* On import every data row becomes a `processing_rows` record with its
  immutable `original_row` (the Excel row number) and source value.
* Workers resolve **unique source ids**, never rows; results are written back
  to every row that references that id, keyed by `original_row`.
* The exporter opens a copy of the **original** workbook and writes only
  `cell(row=original_row, column=dest_col|remarks_col)`. It never appends,
  inserts, deletes or sorts rows.
* The `OutputVerifier` reopens the original and the output and asserts,
  cell by cell, across every sheet, that the only differences are the
  expected (row, column) writes with the expected values; plus sheet names,
  dimensions, merged ranges, freeze panes, filters, column widths. Any
  deviation → job `FAILED`, file not offered.
* Test `test_async_completion_order_does_not_change_output` finishes rows in
  order B, D, A, C and asserts the output is A, B, C, D.

## I. How false positives are minimised

* Discovery and verification are separate modules; a candidate is never written
  unless the DecisionEngine returns MATCH for it.
* MATCH needs independent strong evidence (link, shared unique social account,
  or non-generic image) **plus** corroboration; username/display name/country/
  category can never produce a MATCH on their own (hard cap).
* Generic avatars, generic Discord/aggregator links, generic bios and generic
  categories are explicitly down-weighted.
* Conflicts (source links to another target account, different non-generic
  avatars with no link evidence) push to NO_MATCH/REVIEW.
* Two candidates passing the gate → REVIEW (never "pick the best").
* API failures are never converted into "not found" or "no match".
