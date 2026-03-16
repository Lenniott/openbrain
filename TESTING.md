# OpenBrain — Testing & Iteration Guide

This is the working reference for testing the API, diagnosing issues, and iterating safely. Paste pipeline log output back into a conversation and we can diagnose exactly what went wrong.

---

## Stack at a glance

| Service | Host port | What it does |
|---------|-----------|--------------|
| API (FastAPI) | `7788` | `/embed` `/doc` `/search/text` `/search/vector` `/transcribe` `/health` |
| Postgres | `55432` | `ob_inbox`, `ob_vectors`, `ob_neighbours`, `ob_pipeline_log` |
| Qdrant | `26333` | Vector store — dashboard at `http://localhost:26333/dashboard` |
| Minio | `29000` (API) `29001` (console) | File storage — console at `http://localhost:29001` |

Embedding: Ollama `nomic-embed-text` (768d, configured via `EMBEDDING_BASE_URL`)
Transcription: Faster-Whisper (configured via `WHISPER_BASE_URL`)

---

## Start / rebuild

```bash
# First time or after schema changes
docker compose down -v   # wipes volumes — schema re-runs on next up
docker compose up --build

# Normal restart (keeps data)
docker compose up --build

# Tail logs
docker compose logs -f api
```

---

## Health check

```bash
curl http://localhost:7788/health
# {"status":"ok"}
```

---

## Endpoints

### POST /embed — notes, captures, any text

```bash
curl -X POST http://localhost:7788/embed \
  -H "Content-Type: application/json" \
  -d '{
    "raw_text": "OpenBrain uses Qdrant for vector search and Postgres for structured storage.",
    "type": "note",
    "source": "claude",
    "session_id": "test-session-1"
  }'
```

Fields:
- `raw_text` (required)
- `type` (required) — `note | query | document | chat_summary`
- `source` (optional)
- `session_id` (optional)
- `confidence` (optional, default `1.0` — items below `0.8` go to `needs_review`)

Response: `{ inbox_id, chunk_count, embedded: true|false }`

---

### POST /doc — file ingest (PDF, TXT, MD)

```bash
curl -X POST http://localhost:7788/doc \
  -H "x-ob-filename: my-doc.pdf" \
  -H "x-ob-source: claude" \
  -H "x-ob-session-id: test-session-1" \
  -F "file=@/path/to/my-doc.pdf"
```

Headers:
- `x-ob-filename` — filename key used in S3 and for dedup (required if multipart filename not set)
- `x-ob-filetype` — optional hint (`pdf`, `txt`, `md`)
- `x-ob-source` — optional
- `x-ob-session-id` — optional
- `x-ob-description` — optional human summary

Response: `{ status, inbox_id, filename, is_new, chunk_count }`

`status` values:
- `ok` — embedded successfully
- `no_change` — file re-uploaded but content within 5% Jaccard distance, skipped
- `embed_error` — extraction or Qdrant failure (check pipeline log)

---

### GET /doc/{inbox_id}/file — download the original file

```bash
curl http://localhost:7788/doc/<inbox_id>/file -o downloaded.pdf
```

Returns the raw file from S3 with the correct `Content-Type` and `Content-Disposition` headers. Useful for verifying what actually got stored after a `/doc` upload.

---

### POST /search/text — semantic search by query

```bash
curl -X POST http://localhost:7788/search/text \
  -H "Content-Type: application/json" \
  -d '{"query_text": "how does chunking work", "limit": 10}'
```

Response: ranked hits with `score`, `inbox_id`, `chunk_text`, `type`, `source`, `filename`

---

### POST /search/vector — search from an existing vector id

```bash
curl -X POST http://localhost:7788/search/vector \
  -H "Content-Type: application/json" \
  -d '{"vector_id": "<ob_vectors.id>", "limit": 10}'
```

---

### POST /transcribe — audio to text only

```bash
curl -X POST http://localhost:7788/transcribe \
  -F "file=@/path/to/audio.m4a" \
  -F "language=en"
```

Response: `{ text: "..." }` — use this when you just want the transcript back.

---

### POST /transcribe/embed — audio to text to vectors (full pipeline)

Transcribes the audio, stores the audio file in S3, creates a `document` in `ob_inbox` with the transcript as `raw_text`, chunks and embeds, writes neighbours. The original audio is retrievable via `GET /doc/{inbox_id}/file`. One shot.

```bash
curl -X POST http://localhost:7788/transcribe/embed \
  -F "file=@/path/to/audio.m4a" \
  -F "language=en" \
  -F "source=shortcut" \
  -F "session_id=test-session-1"
```

Form fields:
- `file` (required) — mp3, m4a, wav
- `language` (optional) — hint for Whisper
- `source` (optional) — defaults to `whisper`
- `session_id` (optional)

Response: `{ text, inbox_id, chunk_count, embedded: true|false }`

---

## Pipeline log — primary debug tool

Every significant step writes a row to `ob_pipeline_log`. Connect to Postgres on port `55432`:

```
psql -h localhost -p 55432 -U openbrain -d openbrain
```

### See what happened to one item

```sql
SELECT step, status, detail, created_at
FROM ob_pipeline_log
WHERE inbox_id = '<uuid>'
ORDER BY created_at;
```

A healthy embed run looks like:

```
inbox_created  | ok      | type=note source=claude
chunk          | ok      | idx=0 vec=<uuid>
chunk          | ok      | idx=1 vec=<uuid>
qdrant_upsert  | ok      | points=2
neighbour      | ok      | searched from 2 chunk(s)
vectorised     | ok      | chunks=2
```

A healthy doc run looks like:

```
inbox_created  | ok      | new doc filename=report.pdf
chunk          | ok      | idx=0 vec=<uuid>
...
qdrant_upsert  | ok      | points=N
neighbour      | ok      | searched from N chunk(s)
vectorised     | ok      | chunks=N filename=report.pdf
```

### See recent errors

```sql
SELECT inbox_id, step, detail, created_at
FROM ob_pipeline_log
WHERE status = 'error'
ORDER BY created_at DESC
LIMIT 20;
```

### See everything in the last 10 minutes

```sql
SELECT inbox_id, step, status, detail, created_at
FROM ob_pipeline_log
WHERE created_at > NOW() - INTERVAL '10 minutes'
ORDER BY created_at;
```

### Count steps by type (pipeline health overview)

```sql
SELECT step, status, count(*)
FROM ob_pipeline_log
WHERE created_at > NOW() - INTERVAL '1 hour'
GROUP BY step, status
ORDER BY step, status;
```

---

## Verify data after a run

### Check an inbox item

```sql
SELECT id, type, source, status, vectorised, vectorised_at, filename,
       length(raw_text) AS text_len
FROM ob_inbox
WHERE id = '<uuid>';
```

### Check chunks written

```sql
SELECT id, chunk_index, length(chunk_text) AS len
FROM ob_vectors
WHERE inbox_id = '<uuid>'
ORDER BY chunk_index;
```

### Check neighbours written

```sql
SELECT neighbour_id, distance
FROM ob_neighbours
WHERE entity_id = '<uuid>'
ORDER BY distance;
```

### Check Qdrant points exist

```bash
# Count points for an inbox_id
curl -X POST http://localhost:26333/collections/openbrain/points/count \
  -H "Content-Type: application/json" \
  -d '{"filter": {"must": [{"key": "inbox_id", "match": {"value": "<uuid>"}}]}}'
```

---

## Test scenarios

### 1. Short note — single chunk

```bash
curl -X POST http://localhost:7788/embed \
  -H "Content-Type: application/json" \
  -d '{
    "raw_text": "OpenBrain uses Qdrant for vector search and Postgres for structured storage.",
    "type": "note",
    "source": "test"
  }'
```

Expected: `chunk_count=1, embedded=true`

Verify:
```sql
SELECT step, status FROM ob_pipeline_log WHERE inbox_id = '<id>' ORDER BY created_at;
-- should show: inbox_created, chunk (x1), qdrant_upsert, neighbour, vectorised
SELECT count(*) FROM ob_vectors WHERE inbox_id = '<id>';
-- 1
```

---

### 2. Long note — multiple chunks

```bash
curl -X POST http://localhost:7788/embed \
  -H "Content-Type: application/json" \
  -d '{
    "raw_text": "OpenBrain is a personal AI memory system built on a unified inbox model. Everything captured lands in one Postgres table called ob_inbox, with semantic vectors stored in Qdrant and files in Minio. The project uses Ollama nomic-embed-text for local 768-dimensional embeddings. Documents always chunk. Any inbox item over 600 characters also chunks. Chunks are 600 characters with 100-character overlap. Each chunk gets its own Qdrant point and a row in ob_vectors linking back to the parent inbox entry. The chunk_text is stored in both Postgres and the Qdrant payload for zero round-trip retrieval during search.",
    "type": "note",
    "source": "test"
  }'
```

Expected: `chunk_count=2, embedded=true`

Verify:
```sql
SELECT count(*) FROM ob_vectors WHERE inbox_id = '<id>';
-- 2
```

---

### 3. Low confidence — no embed

```bash
curl -X POST http://localhost:7788/embed \
  -H "Content-Type: application/json" \
  -d '{
    "raw_text": "Uncertain capture from some source.",
    "type": "note",
    "source": "test",
    "confidence": 0.5
  }'
```

Expected: `embedded=false`

Verify:
```sql
SELECT step, status, detail FROM ob_pipeline_log WHERE inbox_id = '<id>' ORDER BY created_at;
-- should show: inbox_created, low_confidence (skipped)
SELECT status FROM ob_inbox WHERE id = '<id>';
-- needs_review
```

---

### 4. Document — new upload

```bash
curl -X POST http://localhost:7788/doc \
  -H "x-ob-filename: test-note.txt" \
  -H "x-ob-source: test" \
  -F "file=@/tmp/test-note.txt"
```

Expected: `is_new=true, chunk_count>=1`

Verify:
```sql
SELECT step, status, detail FROM ob_pipeline_log WHERE inbox_id = '<id>' ORDER BY created_at;
-- inbox_created, chunk(s), qdrant_upsert, neighbour, vectorised
```

---

### 5. Document — re-upload unchanged

Upload the same file again. Expected: `status=no_change, chunk_count=0`

Verify:
```sql
SELECT step, status, detail FROM ob_pipeline_log WHERE inbox_id = '<id>' ORDER BY created_at;
-- shows diff_skip (skipped) with change_ratio near 0
```

---

### 6. Document — re-upload changed

Modify the file content meaningfully, upload again with the same filename. Expected: `status=ok, is_new=false`

Verify:
```sql
SELECT step, status, detail FROM ob_pipeline_log WHERE inbox_id = '<id>' ORDER BY created_at;
-- shows purge, then chunk(s), qdrant_upsert, neighbour, vectorised
-- old ob_vectors rows gone, new ones written
SELECT count(*) FROM ob_vectors WHERE inbox_id = '<id>';
```

---

### 7. Neighbours written

After embedding two thematically related notes, check each has neighbours pointing at the other:

```sql
SELECT n.entity_id, n.neighbour_id, n.distance,
       i.raw_text AS neighbour_text
FROM ob_neighbours n
JOIN ob_inbox i ON i.id = n.neighbour_id
WHERE n.entity_id = '<id>'
ORDER BY n.distance;
```

Expected: at least one row with `distance < 0.5` if the notes are semantically close.

---

### 8. Search returns results

```bash
curl -X POST http://localhost:7788/search/text \
  -H "Content-Type: application/json" \
  -d '{"query_text": "vector search memory", "limit": 5}'
```

Expected: hits with non-zero scores, chunk_text populated, inbox_id valid.

---

### 9. Audio — transcribe and embed

```bash
curl -X POST http://localhost:7788/transcribe/embed \
  -F "file=@/path/to/voice-memo.m4a" \
  -F "language=en" \
  -F "source=shortcut" \
  -F "session_id=test-audio-1"
```

Expected: `{ text: "...", inbox_id: "...", chunk_count: N, embedded: true }`

Verify the full pipeline ran:
```sql
SELECT step, status, detail FROM ob_pipeline_log WHERE inbox_id = '<id>' ORDER BY created_at;
-- inbox_created (audio doc), chunk(s), qdrant_upsert, neighbour, vectorised
```

Verify stored as a document with transcript:
```sql
SELECT type, filetype, filename, length(raw_text) AS transcript_len,
       fields->>'transcript' AS is_transcript, vectorised
FROM ob_inbox WHERE id = '<id>';
-- type=document, filetype=m4a, transcript=true, vectorised=true
```

Verify audio file is in S3:
```bash
curl http://localhost:7788/doc/<id>/file -o recovered.m4a
# should download the original audio
```

---

### 10. File download — recover a stored document

After any `/doc` upload or `/transcribe/embed`:

```bash
# Download as file
curl http://localhost:7788/doc/<inbox_id>/file -o output.pdf

# Or in browser — just open:
# http://localhost:7788/doc/<inbox_id>/file
```

Expected: file downloads with correct content type (`application/pdf`, `text/plain`, `audio/mp4`, etc.)

Returns `404` if the inbox_id doesn't exist or isn't a document type.

---

## Common failure patterns

| Symptom | Where to look |
|---------|---------------|
| Item stuck on `status=pending` | Pipeline log — look for missing `vectorised` step |
| `embed_error` in pipeline log | `detail` column has the exception — usually Ollama or Qdrant unreachable |
| `chunk_count=0` unexpectedly | Check `raw_text` is populated on the inbox row; extraction may have returned empty |
| Qdrant points missing | Check `qdrant_upsert` step in log; then check Qdrant dashboard |
| No neighbours written | Check `neighbour` step — only fires if there are other points already in Qdrant |
| `no_change` when you expected re-embed | Jaccard change ratio below 5% — the text changes were minor |
| `400 Unsupported file type` on `/doc` | Only `pdf`, `txt`, `md` supported currently |
| `502 Transcription failed` on `/transcribe/embed` | Whisper service unreachable — check `WHISPER_BASE_URL` |
| Audio embedded but `raw_text` empty | Whisper returned blank — try with `language=en` form field |

---

## Iterating

When making code changes:

1. Rebuild the API container: `docker compose up --build api`
2. Run the relevant scenario curl above
3. Check pipeline log for the new `inbox_id`
4. Paste the log output here to diagnose

When making schema changes:

1. Edit `postgres/init/01_schema.sql`
2. `docker compose down -v && docker compose up --build` to re-run init
3. Verify with `\dt` in psql

When adding a new file type to `/doc`:

1. Add extractor to `app/doc_extraction.py`
2. Test with scenario 4 above using the new type
3. Check pipeline log — `embed_error` with "Unsupported" means the extractor wasn't wired up

---

## Reset tables (testing only)

Clears all data without dropping the schema — faster than `down -v` when you just want a clean slate:

```sql
TRUNCATE ob_pipeline_log, ob_neighbours, ob_vectors, ob_inbox RESTART IDENTITY CASCADE;
```

Then delete Qdrant points too:

```bash
curl -X DELETE http://localhost:26333/collections/openbrain \
  && curl -X PUT http://localhost:26333/collections/openbrain \
    -H "Content-Type: application/json" \
    -d '{
      "vectors": {
        "dense": {"size": 768, "distance": "Cosine"}
      }
    }'
```

Or if you want a full wipe including schema (re-runs `01_schema.sql` on next up):

```bash
docker compose down -v && docker compose up --build
```

---

## Useful psql one-liners

```sql
-- All items and their state
SELECT id, type, source, status, vectorised, created_at FROM ob_inbox ORDER BY created_at DESC LIMIT 20;

-- Items that failed
SELECT id, type, status, created_at FROM ob_inbox WHERE status IN ('embed_error', 'extract_error') ORDER BY created_at DESC;

-- Items not yet vectorised
SELECT id, type, filename, status, created_at FROM ob_inbox WHERE vectorised = FALSE ORDER BY created_at;

-- Neighbour graph summary
SELECT entity_id, count(*) AS neighbour_count, min(distance) AS closest
FROM ob_neighbours GROUP BY entity_id ORDER BY closest;

-- Pipeline log for last hour
SELECT inbox_id, step, status, detail, created_at
FROM ob_pipeline_log
WHERE created_at > NOW() - INTERVAL '1 hour'
ORDER BY created_at;
```
