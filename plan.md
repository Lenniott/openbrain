### OpenBrain chunking API – plan

This plan replaces the n8n document/embedding flows with a small Docker‑Compose Python API that handles:

- File ingest to S3/Minio and `ob_inbox` (`POST /doc`)
- Text ingest + chunking + embeddings into Qdrant (`POST /embed`)
- Vector‑based search, including anchored searches via `vector_id` (`POST /search`)

It is designed to preserve the **behavioural semantics** of the existing n8n flows (no silent drops, no duplicate vectorisation for unchanged docs, always re‑embed significantly changed docs) while making the orchestration simpler and faster.

---

### Stack & services

- **API service**
  - Python + FastAPI (async, built‑in OpenAPI, easy file upload).
  - Exposes `/doc`, `/embed`, `/search`, `/transcribe`, `/health`.
  - Talks directly to Postgres, Qdrant, Minio, an embedding service, and an audio transcription service.

- **Postgres**
  - Uses `table_v2.sql` as the schema, especially:
    - `ob_inbox` as the universal inbox table.
    - `ob_vectors` as the per‑chunk (or single) vector table.
  - The API only needs a subset of the existing functions, but it **respects**:
    - `vectorised`, `vectorised_at`, `status`, `type`, `source`, `filename`.
    - `ob_get_not_vectorised` etc. can continue to exist for batch jobs.

- **Qdrant**
  - Single collection, e.g. `openbrain`.
  - Vector size 768 (for `nomic-embed-text`).
  - Qdrant point **id = `ob_vectors.id`** (the “vector_id”).
  - Payload fields (indexed where useful):
    - `inbox_id` (UUID)
    - `chunk_index` (int)
    - `type` (`note | document | query | ...`)
    - `source`
    - `filename`
    - `path` (for file‑sourced chunks)
    - `captured_at` / `created_at`

- **Embedding service**
  - Ollama with `nomic-embed-text` (or any HTTP endpoint exposing `POST /embed` → vector).
  - API client wraps it with retries and timeouts.

- **Transcription service**
  - Faster‑Whisper HTTP service (or similar) that accepts audio (e.g. m4a, mp3, wav) and returns transcribed text.
  - API client wraps it with retries, timeouts, and configurable model.

- **Minio / S3**
  - Endpoint: `http://192.168.0.47:9002`
  - Region: `us-east-1`
  - Access key: `lenniott` (secret key via env).
  - Bucket: `files`
  - Object key: exactly the filename from headers / request.
  - `ob_inbox.fields->'minio_key'` stores the key.

---

### Data model alignment

- **`ob_inbox`**
  - `id UUID` – inbox id.
  - `raw_text TEXT` – for notes/queries and optional document description.
  - `source TEXT` – where it came from (`github | claude | email | telegram | shortcut | s3 | ...`).
  - `type TEXT` – `note | document | query | ...`.
  - `fields JSONB` – document metadata (Minio key, mime type, etc.).
  - `status TEXT` – `pending | ok | needs_review | extract_error | embed_error`.
  - `confidence FLOAT` – used to decide auto‑vectorisation (threshold 0.8).
  - `filename TEXT`, `filetype TEXT` – for document items.
  - `vectorised BOOLEAN` – true only when embedding is complete and in sync.
  - `vectorised_at TIMESTAMPTZ` – embedding completion time.

- **`ob_vectors`**
  - `id UUID` – **vector id**; used as Qdrant point id.
  - `inbox_id UUID` – reference back to `ob_inbox`.
  - `chunk_index INT` – 0‑based chunk index (single‑vector items use 0).
  - `chunk_text TEXT` – the actual chunk string.
  - Additional columns (timestamps, retrieval stats) as per existing schema.

The API will always:

- Insert into `ob_inbox` first.
- Insert one or more `ob_vectors` rows (`id` becomes `vector_id`).
- Upsert Qdrant points with `id = ob_vectors.id` and `payload.inbox_id = ob_vectors.inbox_id`.

---

### Endpoint design

#### `POST /doc` – file ingest, S3, diff, and embed

Purpose: Replace the `ob-document-v2` `doc_s3` n8n workflow plus the follow‑up `ob-embed` call. Handles **PDF, CSV, TXT, MD** files.

Inputs:

- Multipart form:
  - `file`: the uploaded file.
- Headers (or JSON fields if easier on the client):
  - `x-ob-filename` (required).
  - `x-ob-filetype` (optional; extension or mime hint).
  - `x-ob-source` (optional; defaults to `'unknown'`).
  - `x-ob-session-id` (optional).
  - `x-ob-description` (optional; human summary goes into `raw_text`).

Key behaviours:

- Detect whether this is a **new** or **existing** document (by `(filename, type='document')`).
- For new docs:
  - Save file to Minio.
  - Create a new `ob_inbox` row with `type='document'`, `fields.minio_key=filename`, `vectorised=false`, `status='pending'`, `confidence=1.0`.
  - Extract text for embedding.
  - Run chunk + embed pipeline if `confidence >= 0.8`; otherwise mark `needs_review`.
- For existing docs:
  - Download the existing S3 object.
  - Extract text from both old and new file.
  - Compute a **Jaccard word‑level change ratio** between old and new text.
  - If change ratio ≤ 0.05 → **no re‑embed**; just respond `no_change`.
  - If > 0.05 → treat as modified:
    - Overwrite Minio object.
    - Update `ob_inbox.vectorised=false`, `vectorised_at=NULL`, bump `updated_at`.
    - Re‑run chunk + embed pipeline based on new text.

Edge‑case guarantees:

- If extraction fails: set `status='extract_error'`, `vectorised=false`, respond with an error and `inbox_id` (no Qdrant writes).
- If embedding or Qdrant fails: set `status='embed_error'`, `vectorised=false`, respond with error and `inbox_id`.
- Unchanged docs never get double‑vectorised; we only re‑embed when the Jaccard change ratio crosses the threshold.

Flow:

```mermaid
flowchart TD
  start[doc_request] --> validateHeaders[validate filename & file present]
  validateHeaders -->|missing/invalid| fail400[return 400 with error]
  validateHeaders --> lookupInbox[SELECT * FROM ob_inbox WHERE filename & type='document']

  lookupInbox -->|no row| newDoc[new document]
  lookupInbox -->|row exists| existingDoc[existing document]

  newDoc --> uploadS3[upload file to S3 (bucket=files, key=filename)]
  uploadS3 --> insertInbox[INSERT ob_inbox (type=document, fields.minio_key=filename, vectorised=false, confidence=1.0, status='pending')]
  insertInbox --> extractNewText[extract_text(file_bytes, filetype)]
  extractNewText --> embedPipelineNew{confidence>=0.8 AND extract ok?}

  embedPipelineNew -->|no| markNeedsReviewNew[UPDATE ob_inbox status='needs_review'] --> respondNew
  embedPipelineNew -->|yes| runChunkEmbedNew[chunk + embed + Qdrant + ob_vectors rows + mark vectorised=true,status='ok'] --> respondNew[return {status:ok, inbox_id, filename, is_new:true}]

  existingDoc --> downloadOld[download existing file from S3]
  existingDoc --> extractIncoming[extract_text(incoming file)]
  downloadOld --> extractOld[extract_text(old file)]

  extractIncoming --> diffJoin
  extractOld --> diffJoin[compute changeRatio via Jaccard(tokens_old, tokens_new)]

  diffJoin --> changedEnough{changeRatio > 0.05?}

  changedEnough -->|no| respondNoChange[return {status:no_change, inbox_id, filename}]
  changedEnough -->|yes| overwriteS3[overwrite S3 object with new file]

  overwriteS3 --> markStale[UPDATE ob_inbox SET vectorised=false, vectorised_at=NULL, updated_at=NOW(), status='pending']
  markStale --> embedPipelineExisting{confidence>=0.8 AND extract ok?}

  embedPipelineExisting -->|no| markNeedsReviewExisting[UPDATE ob_inbox status='needs_review'] --> respondExisting
  embedPipelineExisting -->|yes| runChunkEmbedExisting[re-chunk + re-embed + Qdrant + ob_vectors rows + mark vectorised=true,status='ok'] --> respondExisting[return {status:ok, inbox_id, filename, is_new:false}]

  runChunkEmbedNew -->|embed/Qdrant failure| markEmbedErrorNew[UPDATE ob_inbox status='embed_error', vectorised=false] --> respondNewError[500/202 with error + inbox_id]
  runChunkEmbedExisting -->|embed/Qdrant failure| markEmbedErrorExisting[UPDATE ob_inbox status='embed_error', vectorised=false] --> respondExistingError[500/202 with error + inbox_id]
```

---

#### `POST /embed` – text notes, captures, and queries

Purpose: Replace the text‑based n8n capture + embed workflows. Any non‑file content (notes, queries, chat summaries, etc.) should go here.

Inputs (JSON):

- `raw_text` (required) – the full text to be embedded.
- `type` (required) – `note | query | document | chat_summary | ...`.
- `source` (optional) – caller/source identifier.
- `session_id` (optional).
- `fields` (optional JSON metadata).
- `confidence` (optional, default `1.0`).

Behaviour:

1. Validate basic fields.
2. Insert a new row in `ob_inbox` with:
   - `raw_text`, `type`, `source`, `session_id`, `fields`, `confidence`.
   - `status='pending'`, `vectorised=false`, `vectorised_at=NULL`.
3. Decide path:
   - If text length ≤ `chunk_size` (e.g. 600 chars) and not explicitly forced to chunk:
     - Go through the **single‑vector** path.
   - Otherwise:
     - Go through the **chunking** path (600 + 100 overlap).
4. Respect the confidence threshold:
   - If `confidence < 0.8`:
     - Do **not** embed.
     - Set `status='needs_review'`, `vectorised=false`.
   - If `confidence >= 0.8`:
     - Embed and write vectors to Qdrant.

Vectors & Qdrant mapping:

- For each embedding (single or per chunk) create an `ob_vectors` row.
- Use `ob_vectors.id` as the **Qdrant point id** (`vector_id`).
- Set `payload.inbox_id = ob_vectors.inbox_id`, `payload.chunk_index = ob_vectors.chunk_index`, and other metadata.

Flow:

```mermaid
flowchart TD
  start[embed_request] --> validateBody[validate raw_text & type]
  validateBody -->|missing/invalid| fail400[return 400 with error]
  validateBody --> determineConfidence[set confidence=body.confidence or 1.0]

  determineConfidence --> insertInbox[INSERT ob_inbox (raw_text, type, source, session_id, confidence, status='pending', vectorised=false)]
  insertInbox --> needChunk{text length > chunk_size OR force_chunk?}

  needChunk -->|no| singleVectorPath[single-vector path]
  needChunk -->|yes| chunkPath[chunking path]

  %% Single-vector path
  singleVectorPath --> shouldEmbedSingle{confidence>=0.8?}
  shouldEmbedSingle -->|no| markNeedsReviewSingle[UPDATE ob_inbox status='needs_review'] --> respondSingle
  shouldEmbedSingle -->|yes| insertVectorSingle[INSERT ob_vectors (inbox_id, chunk_index=0, chunk_text=raw_text)]
  insertVectorSingle --> embedSingle[embed(raw_text)]

  embedSingle --> upsertQdrantSingle[upsert 1 point (id=ob_vectors.id, table='inbox', inbox_id, chunk_index=0)] --> markVectorisedSingle[UPDATE ob_inbox SET vectorised=true, vectorised_at=NOW(), status='ok'] --> respondSingle[return {inbox_id, chunk_count:0, embedded:true}]

  embedSingle -->|embed/Qdrant failure| markEmbedErrorSingle[UPDATE ob_inbox status='embed_error', vectorised=false] --> respondSingleError[500/202 with error + inbox_id]

  %% Chunking path
  chunkPath --> doChunk[chunk_text(raw_text) -> chunks[]]
  doChunk --> insertChunks[INSERT ob_vectors rows (one per chunk)]
  insertChunks --> shouldEmbedChunks{confidence>=0.8?}

  shouldEmbedChunks -->|no| markNeedsReviewChunks[UPDATE ob_inbox status='needs_review'] --> respondChunks
  shouldEmbedChunks -->|yes| embedChunks[embed each chunk (batched)]

  embedChunks --> upsertQdrantChunks[upsert N points (id=ob_vectors.id, table='vectors', inbox_id, chunk_index)] --> markVectorisedChunks[UPDATE ob_inbox SET vectorised=true, vectorised_at=NOW(), status='ok'] --> respondChunks[return {inbox_id, chunk_count:N, embedded:true}]

  embedChunks -->|embed/Qdrant failure| markEmbedErrorChunks[UPDATE ob_inbox status='embed_error', vectorised=false] --> respondChunksError[500/202 with error + inbox_id]
```

---

#### `POST /search` – query text or existing vector id

Purpose: Replace/centralise search flows. Supports:

- Free‑text queries (embed on the fly, don’t necessarily persist).
- Searches anchored on an existing `vector_id` (Qdrant point id) for “find neighbours of this”.

Inputs (JSON):

- One of:
  - `query_text` – text to embed and search with.
  - `vector_id` – UUID of an existing `ob_vectors.id` (Qdrant point id).
- Optional:
  - `limit` (default e.g. 20).
  - `filters` (type/source/filename, etc.).

Behaviour:

- If `query_text` provided:
  - Embed `query_text`.
  - Query Qdrant using that vector and filters.
  - (Optional) If you want to persist queries, you can also store this in `ob_inbox` + `ob_vectors` as `type='query'` and use that `vector_id` later.

- If `vector_id` provided:
  - Look up `ob_vectors.id = vector_id`.
    - If not found: return 404.
  - Use `vector_id` directly as the Qdrant point id:
    - Either use a “search around point” API if available.
    - Or fetch the stored vector once from Qdrant by id, then run a vector search.
  - No re‑embedding is needed.

- For each Qdrant hit:
  - Use `payload.inbox_id` (and optionally `chunk_index`) to join back to Postgres:
    - Join `ob_vectors` on `id = point.id` if we need `chunk_text`.
    - Join `ob_inbox` on `ob_vectors.inbox_id = ob_inbox.id`.
  - Return score, `vector_id`, `inbox_id`, `type`, `source`, `chunk_text`, `filename`, etc.

Edge‑case guarantees:

- If Qdrant is unavailable: return `503` with clear error, no fake results.
- If `vector_id` points to a deleted or missing vector: 404 with `{ error: "vector_not_found" }`.

Flow:

```mermaid
flowchart TD
  start[search_request] --> decideMode{query_text or vector_id?}

  decideMode -->|query_text provided| modeText[raw text mode]
  decideMode -->|vector_id provided| modeVector[anchor vector mode]
  decideMode -->|neither| fail400[return 400 with error]

  %% Raw text mode
  modeText --> embedQuery[embed(query_text)]
  embedQuery -->|embed failure| respondEmbedError[500 with error]
  embedQuery --> qdrantSearchText[Qdrant search with vector + optional filters]

  %% Vector-id mode
  modeVector --> fetchVector[SELECT * FROM ob_vectors WHERE id = :vector_id]
  fetchVector -->|not found| respond404[404 vector_not_found]
  fetchVector --> qdrantSearchAnchor[Qdrant search using point id = vector_id (+ filters)]

  %% Common result handling
  qdrantSearchText --> enrichResults
  qdrantSearchAnchor --> enrichResults[for each hit: join ob_vectors + ob_inbox]

  enrichResults --> respondOk[return ranked results with scores and context]
```

---

#### `POST /transcribe` – audio → text (for notes and captures)

Purpose: Provide a fast path for audio notes (e.g. m4a files) to become text that can then be sent through `/embed` (either automatically or by the caller).

Inputs:

- Multipart form:
  - `file`: audio file (`.m4a`, `.mp3`, `.wav`, etc.).
- Optional fields/headers:
  - `language` (hint for transcription).
  - `session_id`, `source`, `type` (if we want to optionally auto‑embed as a note).

Behaviour (v1):

- Send the raw audio bytes to the configured Faster‑Whisper service:
  - `WHISPER_BASE_URL`, `WHISPER_MODEL`, `WHISPER_TIMEOUT_SECONDS`.
- Receive transcribed text.
- Return JSON:
  - At minimum: `{ text: "<transcript>" }`.
  - Optionally (if we wire it) we can:
    - Immediately call the internal `/embed` pipeline:
      - Create `ob_inbox` row with `type='note'` (or caller‑provided type), `raw_text=transcript`.
      - Return `{ text, inbox_id, embedded: true/false }`.

Flow (API‑only, no auto‑embed):

```mermaid
flowchart TD
  start[transcribe_request] --> validateAudio[validate audio file present/supported]
  validateAudio -->|missing/invalid| fail400[return 400 with error]
  validateAudio --> callWhisper[POST audio to WHISPER_BASE_URL with model + timeout]
  callWhisper -->|error/timeout| respondWhisperError[502 with error]
  callWhisper --> parseTranscript[extract transcript text]
  parseTranscript --> respondTranscript[return { text }]
```

If/when we want auto‑embed, we simply extend `parseTranscript` to call the same internal function used by `/embed` and include `inbox_id` / `vector_id` in the response.

---

### Implementation outline

- **Docker Compose**
  - Services: `api`, `postgres`, `qdrant`, `minio`, optional `ollama`, optional `whisper`.
  - Volumes for Postgres + Qdrant + Minio data.
  - `api` depends_on the others; environment variables wire host/ports.

- **FastAPI structure**
  - `main.py` with routers: `/doc`, `/embed`, `/search`, `/transcribe`, `/health`.
  - Modules:
    - `db.py` – SQLAlchemy/async engine, `ob_inbox`/`ob_vectors` models.
    - `s3_client.py` – Minio client wrapper.
    - `embedding.py` – client for Ollama / embed endpoint.
    - `transcription.py` – client for Faster‑Whisper audio transcription.
    - `chunking.py` – 600 + 100 overlap chunker.
    - `extraction.py` – PDF/CSV/TXT/MD text extractors.
    - `qdrant_client.py` – collection init + upsert + search helpers.
    - `diffing.py` – Jaccard word‑level diff with threshold 0.05.

- **Config / environment variables**
  - `.env` (and `.env.example`) defines all runtime configuration:
    - **Core API**
      - `APP_HOST` – bind host for FastAPI (e.g. `0.0.0.0` or `http://192.168.0.47` if fronted by a proxy).
      - `APP_PORT` – API port (e.g. `7788`).
      - `APP_LOG_LEVEL` – `info | debug | warning`.
    - **Postgres**
      - `POSTGRES_HOST` – hostname (`postgres` inside compose).
      - `POSTGRES_PORT` – port (default `5432`).
      - `POSTGRES_DB` – database name (e.g. `n8ndb` or `openbrain`).
      - `POSTGRES_USER` – username.
      - `POSTGRES_PASSWORD` – password.
    - **Qdrant**
      - `QDRANT_HOST` – hostname (`qdrant` inside compose).
      - `QDRANT_PORT` – port (default `6333`).
      - `QDRANT_COLLECTION` – collection name (`openbrain`).
      - `QDRANT_API_KEY` – API key, if Qdrant auth is enabled.
    - **Minio / S3**
      - `S3_ENDPOINT_URL` – Minio/S3 endpoint URL (e.g. `http://192.168.0.47:9002` or `http://minio:9000` in compose).
      - `S3_REGION` – region (`us-east-1`).
      - `S3_ACCESS_KEY_ID` – access key.
      - `S3_SECRET_ACCESS_KEY` – secret key.
      - `S3_BUCKET_FILES` – bucket for document files (`files`).
    - **Embedding service**
      - `EMBEDDING_BASE_URL` – base URL for the embedding HTTP service (e.g. `http://ollama:11434`).
      - `EMBEDDING_MODEL` – model name (`nomic-embed-text`).
      - `EMBEDDING_TIMEOUT_SECONDS` – request timeout.
    - **Transcription service**
      - `WHISPER_BASE_URL` – base URL for Faster‑Whisper (or similar) HTTP service.
      - `WHISPER_MODEL` – model name/size (`tiny`, `base`, `medium`, etc.).
      - `WHISPER_TIMEOUT_SECONDS` – request timeout for audio transcription.
    - **Chunking / vectorisation**
      - `CHUNK_SIZE` – character length per chunk (default `600`).
      - `CHUNK_OVERLAP` – overlap between chunks (default `100`).
      - `CONFIDENCE_THRESHOLD` – confidence cutoff for auto‑vectorisation (default `0.8`).
    - **Misc**
      - `ENVIRONMENT` – `local | staging | prod` for minor behaviour toggles.

- **Testing**
  - Unit tests:
    - Chunking, diffing, extraction per file type.
  - Integration tests:
    - `/doc` on each file type → `ob_inbox` + `ob_vectors` + Qdrant points.
    - `/embed` for short/long/low‑confidence cases.
    - `/search` for both `query_text` and `vector_id` modes.

This plan is the contract for the implementation: the API code and Docker Compose setup should be kept in sync with this document, and any behaviour changes should be reflected here first. 
