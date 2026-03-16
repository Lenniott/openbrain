# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

OpenBrain is a personal AI memory system — a "second brain" built on a unified inbox model. Everything captured (thoughts, documents, queries, chat summaries) lands in one Postgres table (`inbox`), with semantic vectors stored in Qdrant and files in Minio. The project is currently in the **design/prototyping phase**: the `ideas/` directory contains a SQL schema draft and two React prototype UIs, but no implemented backend yet.

## Architecture

### The Stack
| Layer | Technology |
|---|---|
| Interface | `/ob` skill — Claude, iOS Shortcuts (command hub, makes HTTP calls to n8n) |
| API | n8n webhooks — one workflow per endpoint; owns all Postgres + Qdrant writes |
| Vector | Qdrant — one collection `openbrain`, hybrid search (dense + sparse BM25) |
| Embedding | Ollama `nomic-embed-text` — 768d, local |
| Memory | Postgres — 7 tables, `inbox` is the universal store |
| Files | Minio — S3-compatible, referenced by `file_url` |

### Core Principle: Inbox Is the Database
Every item (capture, note, query, chat_summary, document, record) is a row in `ob_inbox`. `type` is the only filter. There is no separate table per content type. Vectors live in Qdrant with `postgres_id` in the payload linking back to Postgres.

### Qdrant Design: One Collection
All inbox items and document chunks share a single collection (`openbrain`). The `table` field in the payload routes fetches back to the correct Postgres table (`inbox` or `documents`). This enables cross-type neighbourhood — a captured thought can neighbour a PDF chunk. Payload indexes: `postgres_id`, `inbox_id`, `table`, `type`, `source`, `chunk_index`, `path`, `captured_at`.

### Document Chunking
Documents always chunk. Any inbox item over 600 chars also chunks. Chunks are 600 chars with 100-char overlap. Each chunk gets its own Qdrant point and a `documents` table row with `inbox_id` linking to the parent entry. `chunk_text` is stored in both Postgres and the Qdrant payload for zero round-trip retrieval.

### Folders Are Intentional
Folders are anchored by inbox items chosen by the user — never auto-generated from semantic distance. `is_anchor = true` on the seeds that define the folder's identity. `is_excluded = true` permanently removes an item from suggestions for that folder. `folder_items` always references `inbox.id`, not document chunks directly.

### Visits as Signal
Three layers: `retrieval_count` (total surfaces), `last_surfaced` (when), and the `surfacing_log` event table (every retrieval with command, query, session, rank, distance). `get_unvisited()` surfaces items never returned. `get_session_cooccurrence()` reveals natural folder seeds.

## API (n8n Webhooks)

n8n webhooks are the API. The skill calls them over HTTP. n8n handles all Postgres writes (via SQL functions) and all Qdrant writes (embed via Ollama → store point).

**Confidence threshold: 0.8.** Items below threshold are written to `ob_inbox` only (`status: 'needs_review'`), not vectorized. Items ≥ 0.8 get a Qdrant point. Human-sent captures always set `confidence: 1.0` in the request — no server-side scoring for capture.

```
POST /ob/capture          — { raw_text, source, confidence, template_type?, fields? }
                            n8n writes to ob_inbox; vectorizes if confidence ≥ 0.8

GET  /ob/templates        — list all templates
GET  /ob/templates/:type  — fetch one (skill calls this before template-based capture)

GET  /ob/inbox            — list items; filter by status/type/source
GET  /ob/inbox/:id        — single item + neighbours + folder memberships
PATCH /ob/inbox/:id       — edit, verify, re-score; triggers vectorization if now ≥ 0.8
DELETE /ob/inbox/:id      — remove (cascades to ob_documents, ob_folder_items)

POST /ob/search           — { query, filters? } → embed → Qdrant → surface_entity() per result
POST /ob/doc              — file upload → ob_inbox row + ob_documents chunks + Qdrant points

GET  /ob/folders          — list folders
GET  /ob/folders/:id      — folder + members + anchors
POST /ob/folders          — create with anchor inbox_ids
POST /ob/folders/:id/items          — add item (set is_anchor)
DELETE /ob/folders/:id/items/:inbox_id — exclude item permanently

GET  /ob/report           — clustered chat_summaries + unvisited items
```

## The Skill (Command Hub)

The `/ob` Claude skill routes commands to the above webhooks. It does not contain business logic — that lives in n8n. For template-based captures the skill fetches the template first, formats content, shows the user a preview, then POSTs to `/ob/capture` with the filled fields and a confidence score.

## Key SQL Functions

Eight Postgres functions called by n8n after Qdrant results come back:

- `surface_entity(p_table, p_id, p_command, p_query_text, p_session_id, p_rank, p_distance)` — routes by table, writes `surfacing_log`, updates `retrieval_count`/`last_surfaced`
- `upsert_neighbour(entity_type, entity_id, neighbour_type, neighbour_id, distance)` — writes Qdrant results to `ob_neighbours` permanently
- `get_neighbours_with_context(entity_id, limit)` — expands retrieval context via neighbour graph
- `add_to_folder(folder_id, inbox_id, is_anchor)` — membership with anchor flag
- `exclude_from_folder(folder_id, inbox_id)` — permanent exclusion from folder suggestions
- `get_unvisited(type, limit)` — items where `last_surfaced IS NULL`
- `get_session_cooccurrence(session_id)` — co-occurrence signal for folder seed discovery

## Automated Jobs (n8n)

- **End of session**: `type:chat_summary` row created, embedded, neighbours calculated
- **Nightly batch**: chunk neighbours queued and written to `ob_neighbours`
- **Weekly**: surfacing analysis, co-occurrence patterns, singleton flagging

## Current Files

- `ideas/table_v1.sql` — Postgres schema; all tables and references use `ob_` prefix
- `ideas/idea_v1.jsx` — interactive architecture reference UI (React, no build system, designed for direct use in a sandbox like v0 or Bolt)
- `ideas/idea_navigation_interface_v0_6.jsx` — graph/list navigation UI prototype (React, iOS-style, shows cluster→file→chunk drill-down with animated transitions)
