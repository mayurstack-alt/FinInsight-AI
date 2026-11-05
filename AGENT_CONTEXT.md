# FinInsight AI — Agent Context and Engineering Rules

## Current implementation status (updated 2026-11-05)

Scaffolded: the Vite/React/Tailwind frontend; FastAPI application and `GET /api/v1/health`; environment settings and restricted-origin CORS; centralized exception handling; JSON logging with request-ID correlation and sensitive-data redaction; synchronous SQLAlchemy engine, session factory and declarative Base; Alembic environment; Celery with Redis URLs and concurrency 1; Dockerfile/Compose API, worker and Redis definitions; dependency manifests and `.env.example`.

Implemented: browser-side Google OAuth through Supabase Auth (PKCE), Supabase access-token verification in FastAPI (asymmetric JWKS verification plus Supabase Auth user-endpoint validation for legacy HS256), protected `GET /api/v1/users/me`, and the `Profile`, `Report`, and `ProcessingJob` SQLAlchemy models. Phase 2 follow-up adds Redis-backed FastAPI rate limiting and reusable owner-verification dependencies. Phase 4 adds the private Supabase Storage adapter, bounded PDF upload validation, ownership-scoped report endpoints, five-minute signed downloads, compensating upload cleanup, retryable soft deletion, and the second Alembic migration. Phase 6 adds a standalone PDF processor with PyMuPDF text inspection, context-aware statement detection, pdfplumber table extraction, Pandas normalization, targeted pytesseract OCR fallback, validation/warnings, and a controlled local CLI. Upload creates the application Profile on demand from the verified JWT UUID only; there is no separate password system or Auth signup trigger.

Tested: report and PDF-processing unit/integration tests pass against isolated in-memory SQLite, synthetic generated PDFs, and mocked Storage/OCR. The PDF tests cover selectable text, blank pages, corrupted/encrypted/oversized/page-limited input, statement detection, table extraction and page continuation, formats/currency/scale, validation, OCR fallback/provenance, and owner-scoped stored-file retrieval. No live Supabase Storage or PostgreSQL credentials are exercised here, no actual bucket has been provisioned by this test run, and no Alembic migration has been applied to the user's Supabase project.

Pending verification: Redis-backed limiter startup and endpoint-limit behavior against a running Redis service; end-to-end Google OAuth and Auth tests; live Supabase PostgreSQL, migration, Storage bucket, and report API integration; and PDF extraction accuracy on representative third-party annual reports. Tesseract is not installed on this Windows host, so the real OCR executable test is skipped; OCR decision-making, table-row conversion, confidence propagation, and error handling are tested with deterministic mocks. SQLite enforces report/profile and job/report foreign keys but cannot enforce the external `auth.users` schema reference. No PDF processing result is written to PostgreSQL, and no result tables are designed yet. Celery report-task dispatch, financial calculations, Pinecone indexing/retrieval, and Copilot/chat remain unimplemented/deferred. Do not claim production-level extraction accuracy or live Supabase integration until those tests pass. The health endpoint is a liveness endpoint, not a dependency readiness check.

Earlier setup verification used Python 3.14.2; compatible installed Python requirements passed `pip check`; Alembic history loaded; FastAPI returned HTTP 200 from `/api/v1/health`; Vite served and built; and Compose YAML parsed. The API startup check predates Redis-backed rate limiting, which now requires a reachable Redis service. The new limiter startup and endpoint enforcement have not yet been runtime-verified. Docker Desktop and host Tesseract were not available during earlier verification, so container startup, worker/Redis runtime, and OCR executable verification remain pending. For Windows host OCR, install the UB Mannheim Tesseract build, put its executable directory on `PATH`, restart the terminal, and verify `tesseract --version`; set `TESSERACT_CMD` only when PATH discovery is not sufficient. LLM and embedding providers, Azure services, and production Redis hosting remain pending as listed below.

Phase 4 targeted verification: `python -m pytest -q tests/test_report_validation.py tests/test_report_service.py tests/test_report_routes.py tests/test_storage_service.py tests/test_migrations.py tests/test_models.py` passes (47 tests). Ruff and Python compile checks pass for touched files. Tests use mocks and SQLite; they do not validate network access, Supabase account/bucket configuration, or live PostgreSQL RLS behavior. The earlier broad test run was not completed because legacy Windows/Python 3.14 async test behavior stalled; this phase's report route unauthorized check avoids that environment issue. Never apply migrations to production without explicit instruction.

Phase 6 targeted verification: `python -m pytest -q tests/test_pdf_processing.py tests/test_report_validation.py tests/test_report_service.py tests/test_report_routes.py tests/test_storage_service.py tests/test_migrations.py tests/test_models.py` passes (65 passed, 1 skipped). The skipped test requires the Tesseract executable, not just the `pytesseract` Python wrapper. Ruff and Python compile checks pass. PDF fixtures are synthetic, not representative customer or public company reports; no extraction accuracy claim is made. `python -m alembic history` still reports the prior report-schema migration as head; Phase 6 requires no database migration.

**Dependency compatibility exception:** The available PyPI `langchain-pinecone` stable release metadata declares `Requires-Python: <3.14` (see [the PyPI project page](https://pypi.org/project/langchain-pinecone/)). `requirements.txt` specifies `langchain-pinecone>=0.2.13` to prevent pip from falling back to obsolete 0.0.1. All other requirements installed on Python 3.14.2; the Pinecone integration remains unresolved. Do not downgrade Python or force-install an unsupported build. Recheck for a compatible stable release before implementing the integration and update this note when resolved.

> **Purpose:** Persistent project context for AI coding agents and contributors. Read this file before planning, coding, refactoring, installing packages, or changing architecture. Treat it as the source of truth for approved scope and technology decisions. Do not invent requirements, silently change decisions, or implement features outside the approved scope.

## Documentation workflow (updated 2026-11-05)

- This file is the single source of truth for ongoing development context. Read it before any code, dependency, configuration, or architecture change.
- Update this file in the same work session whenever code or project configuration changes. Record relevant decisions, implementation progress, completed work, pending work, known issues, and verification accurately. Distinguish planned, scaffolded, implemented, and tested behavior.
- Keep `README.md` as stable, public-facing project documentation: project description, problem and objectives, product capabilities, finalized stack and architecture, components, data flow, security/data design, and durable setup/configuration guidance.
- Do not copy routine phase updates, task history, temporary troubleshooting, verification logs, or changing implementation status into `README.md`. Keep those in this file. Update README only when a durable public-facing product, architecture, supported setup, or usage detail needs correction.
- Keep the two files consistent by describing the same approved product and technical decisions at their appropriate level of detail. Do not duplicate their purposes: README is for project users and contributors; this file is for persistent engineering context and future coding agents.
- Do not modify either document to imply a feature is complete when it is only planned or scaffolded. Do not remove previously approved requirements or limitations without recording an explicit decision.

## 1. Product definition

**Project:** FinInsight AI – Intelligent Financial Statement Risk Analyzer.

A user uploads a company's annual report PDF. The system extracts and validates financial statement information, calculates financial ratios, identifies potential financial risks, generates a financial health score, and presents the results in an interactive dashboard. The planned AI Financial Copilot answers questions about report content and explains results using report evidence.

Core financial calculations are deterministic Python logic. The LLM/RAG layer interprets narrative report content and explains computed results; it must not be treated as the source of truth for financial arithmetic.

### Strict product scope

- Analyze one company's annual report at a time.
- **Do not implement comparison of reports, ratios, scores, or financial performance between two companies. This feature is explicitly out of scope.**
- The planned Copilot may answer questions about a user's authorized report, explain ratios and risk findings, and summarize report content.
- Do not add unrequested product features, integrations, dashboards, external services, or architecture.

## 2. Finalized technology decisions

These decisions are approved. Do not substitute alternatives without explicit user approval.

| Layer | Finalized choice |
|---|---|
| Python runtime | Python 3.14 |
| Frontend | React.js + Tailwind CSS, using Vite |
| Backend | Python + FastAPI only |
| Background jobs | Celery + Redis |
| Relational database | Supabase PostgreSQL |
| ORM | SQLAlchemy 2.0 |
| Schema migrations | Alembic |
| Authentication | Supabase Auth; FastAPI verifies access tokens |
| PDF/object storage | Supabase Storage with a private bucket |
| PDF text extraction | PyMuPDF |
| Financial table extraction | pdfplumber |
| OCR engine | Tesseract OCR, invoked from Python with pytesseract |
| Data cleaning/analysis | Pandas and NumPy |
| RAG framework | LangChain |
| Vector database | Pinecone |
| Containerization | Docker + Docker Compose |
| Cloud target | Microsoft Azure; exact Azure services are not selected |

### Approved authentication provider

Google sign-in is approved through Supabase Auth. The browser uses `@supabase/supabase-js` with the Supabase publishable key and PKCE. Google OAuth Client ID/Secret are configured in the Supabase Auth provider dashboard; never place the Google secret in Vite variables or expose it to the browser. FastAPI accepts the Supabase access token as a Bearer token and verifies the user identity. Prefer asymmetric JWT signing keys and JWKS verification; when legacy HS256 tokens are in use, validate them through the Supabase Auth user endpoint using the publishable key rather than placing the JWT signing secret in application configuration.

### Decisions that remain pending

- LLM provider/model and its LangChain integration package.
- Embedding provider/model and its LangChain integration package.
- Exact Azure hosting services and production topology.
- Final production Redis hosting choice. Redis is the approved Celery broker; managed production hosting/provider details can be selected during deployment planning.

Do not guess or silently finalize these pending items. Provider-neutral LangChain setup and Pinecone integration may be prepared now; do not install a provider-specific LLM or embedding adapter before selection.

### Python 3.14 compatibility rule

All dependencies must be selected at stable versions compatible with Python 3.14. Preserve the user's Python 3.14 decision; do not silently downgrade Python to resolve a package conflict. If a package lacks a compatible release or wheel, report the exact blocker and propose compatible alternatives for approval. SQLAlchemy must remain in the 2.0 series (`>=2.0,<2.1`) unless the user explicitly approves a major/minor-series change.

Celery 5.6 introduced initial Python 3.14 support. Celery's project does not officially support Windows as a worker platform. On the user's Windows development machine, run the Celery worker in the Linux Docker container. Do not recommend relying on a native Windows Celery worker for the standard development workflow.

## 3. Required repository structure

Keep the application structure aligned with this tree. Do not add a separate `users` business module; user profile endpoints are represented by `api/routes/users.py` and shared dependencies. Add Python `__init__.py` files where package imports require them. Alembic requires its own configuration and migration-version files; these are database tooling and must not become extra business modules. Keep `AGENT_CONTEXT.md` at the repository root.

```text
fininsight-ai/
├── frontend/
│   └── React application (Vite + Tailwind CSS)
├── backend/
│   ├── app/
│   │   ├── main.py
│   │   ├── config.py
│   │   ├── database.py
│   │   ├── api/
│   │   │   ├── routes/
│   │   │   │   ├── health.py
│   │   │   │   ├── users.py
│   │   │   │   ├── reports.py
│   │   │   │   ├── analysis.py
│   │   │   │   ├── dashboard.py
│   │   │   │   └── chat.py
│   │   │   └── dependencies.py
│   │   ├── modules/
│   │   │   ├── auth/
│   │   │   │   ├── models.py (Profile)
│   │   │   │   ├── service.py
│   │   │   │   └── dependencies.py
│   │   │   ├── reports/
│   │   │   │   ├── models.py (Report, ProcessingJob)
│   │   │   │   ├── service.py
│   │   │   │   └── validation.py
│   │   │   ├── pdf_processing/
│   │   │   │   ├── models.py
│   │   │   │   ├── loader.py
│   │   │   │   ├── detection.py
│   │   │   │   ├── tables.py
│   │   │   │   ├── normalization.py
│   │   │   │   ├── ocr.py
│   │   │   │   ├── validation.py
│   │   │   │   ├── pipeline.py
│   │   │   │   └── cli.py
│   │   │   ├── financial_analysis/
│   │   │   ├── rag/
│   │   │   ├── chat/
│   │   │   └── storage/
│   │   │       └── service.py
│   │   ├── core/
│   │   │   ├── security.py
│   │   │   ├── middleware.py
│   │   │   ├── exceptions.py
│   │   │   ├── errors.py
│   │   │   └── logging.py
│   │   ├── workers/
│   │   │   ├── celery_app.py
│   │   │   └── tasks.py
│   │   └── schemas/
│   ├── tests/
│   ├── Dockerfile
│   ├── requirements.txt
│   └── .env.example
├── docker-compose.yml
├── .gitignore
├── README.md
└── AGENT_CONTEXT.md
```

The `frontend/` directory contains a standard Vite React application. Do not add another frontend framework. Keep Docker Compose at the repository root and the backend Dockerfile at `backend/Dockerfile` as specified.

## 4. Backend module responsibilities

- `auth/`: Supabase JWT verification and authentication/authorization helpers.
- `reports/`: report metadata, upload lifecycle, report ownership checks, status, and retry coordination.
- `pdf_processing/`: page-wise PDF inspection, statement detection, table parsing, OCR fallback, data normalization, extraction validation, and orchestration. It has no FastAPI or Celery dependency.
- `financial_analysis/`: deterministic financial calculations, ratios, risk rules, and health score.
- `rag/`: narrative extraction/cleaning, chunking, embedding interface, Pinecone indexing, and authorized retrieval.
- `chat/`: Copilot sessions/messages, prompt construction, response orchestration, and output validation.
- `storage/`: private Supabase Storage upload/download operations and access-safe signed URLs.

API route handlers in `api/routes/` should validate/coordinate requests and call module logic; do not place all business logic in route functions. `api/routes/users.py` handles user profile endpoints. `api/dependencies.py` contains shared FastAPI dependencies such as the current authenticated user and database session.

Shared `core/` responsibilities:
- `security.py`: JWT verification helpers, authorization utilities, and safe security primitives.
- `middleware.py`: only genuine cross-cutting ASGI middleware, such as request/correlation ID handling and carefully selected security headers. Do not force every security feature into middleware.
- `exceptions.py`: application exception types and centralized exception-to-response handling.
- `logging.py`: JSON logging via `python-json-logger`, request-ID injection, secret/path redaction, and shared API/worker logging configuration.
- `rate_limit.py`: Redis-backed IP and authenticated-user rate-limit dependencies. Redis must be reachable during API lifespan startup; limiter failures fail closed.

`config.py` owns typed environment-based settings. `database.py` owns SQLAlchemy engine/session/Base setup. `workers/celery_app.py` owns the Celery application and broker/result configuration. `workers/tasks.py` registers background tasks and delegates to business modules.

### Phase 2 security follow-up: rate limiting and report ownership

- FastAPI creates an async Redis client from `REDIS_URL` in its lifespan and initializes `FastAPILimiter` with the `fininsight:rate-limit` key prefix. The connection is closed and class state reset at shutdown. Startup fails if Redis cannot initialize, and request-time limiter errors return HTTP 503 rather than bypassing limits.
- `fastapi-limiter==0.1.6` is pinned because that release uses Redis and Redis Lua counters with expirations. Version 0.2.0 switched to `pyrate-limiter` without shared Redis storage. Compose's API service reaches Redis at `redis://redis:6379/0`; host-run development uses `redis://localhost:6379/0`.
- Protected profile and report routes enforce 60/minute per client IP and per authenticated user per route. Report upload, retry, signed-download, delete, and profile-update operations also enforce 10/minute per authenticated user. The limiter uses the ASGI client address and a hashed verified user UUID; arbitrary `X-Forwarded-For` values are not trusted. Configure Uvicorn's trusted proxy list in deployment if a reverse proxy forwards client addresses.
- `api/dependencies.py` exposes `verify_report_ownership`, which queries by report UUID and verified JWT user UUID and hides missing/non-owned reports with 404. A deletion-specific dependency can load an owned soft-deleted row for retrying Storage cleanup. Report routes pass the authorized ORM object into service logic, avoiding a second ownership query. These checks remain application-level because the backend uses the service-role key for Storage operations.
- Runtime verification against a live Redis service is pending. Redis rate limiting is independent of Celery task dispatch; Phase 5 background processing remains deferred.

### Phase 3 relational database schema

The initial schema contains exactly three application models. Keep `Profile` in `modules/auth/models.py`; keep `Report` and `ProcessingJob` in `modules/reports/models.py`. Do not add a separate user business module or migrate the Supabase-owned `auth.users` table.

- `Profile.id` is the Supabase Auth UUID and both the profile primary key and a foreign key to `auth.users(id)` with `ON DELETE CASCADE`. Store only app-specific `full_name` and `avatar_url`; do not duplicate passwords, tokens, or Auth identity data. Phase 4 creates a minimal Profile during the first report upload using only the verified token UUID; no signup trigger is configured.
- `Report` belongs to one profile through `user_id`. It stores `company_name`, sanitized `original_filename`, a `reporting_period` label, unique private Storage `storage_path`, positive `file_size`, `file_type` (`application/pdf`), status (`uploaded`, `pending`, `processing`, `completed`, `failed`), timestamps, and nullable `deleted_at` / `storage_deleted_at` tombstone fields. The PDF bytes remain in Supabase Storage. Multiple uploads for the same company and period are allowed.
- Each `ProcessingJob` row represents one execution attempt and belongs to a report. Retries create a new row with the same `task_group_id` and the next `retry_count`; `(task_group_id, retry_count)` is unique. Status values are `queued`, `running`, `succeeded`, and `failed`. `error_message` may contain only a short, sanitized failure summary (never a raw traceback, credential, token, or document content).
- Profile/report/job relationships use database `ON DELETE CASCADE` and ORM delete-orphan behavior. These cascades remove relational rows only; application code must delete Supabase Storage objects separately.
- Migration 1 enables RLS and revokes `anon` and `authenticated` grants on all three `public` tables. Migration 2 grants authenticated users SELECT on `reports` only, with an RLS policy restricted to their own non-deleted rows. All other table writes remain server-side. Storage has no client policies and is deny-by-default; the backend's service-role access must always be preceded by explicit ownership checks.
- `created_at`/`updated_at` are timezone-aware. The database supplies initial values; SQLAlchemy ORM updates refresh `updated_at`. Direct SQL updates do not have an `updated_at` trigger in this phase.

SQLite is used for isolated model tests. It verifies profile/report and report/job relationships, constraints, and cascades. SQLite cannot enforce a foreign key across the external Supabase `auth` schema; the profile-to-Auth FK is validated in ORM metadata and rendered PostgreSQL migration SQL. No live Supabase connection or migration application is claimed as tested.

### Phase 4 report management and Storage

- The report API is rooted at `/api/v1/reports` and all routes require the existing verified Supabase JWT dependency. Report IDs are always paired with the verified JWT UUID in ORM predicates; missing and other-owner records both return 404. User IDs are not accepted from request fields.
- `POST /api/v1/reports` accepts multipart `file`, `company_name`, and `reporting_period`. Validation requires a `.pdf` filename, `application/pdf` declared type, `%PDF-` magic bytes, non-empty content, and a configurable `MAX_PDF_SIZE_BYTES` cap (default 25 MiB). It counts the spooled upload in 64 KiB chunks, then reads no more than the configured cap for a PyMuPDF structural parse; unreadable, empty-page, or password-protected PDFs are rejected before Storage upload. The spooled upload is rewound before SDK upload. The safe object name is derived as `<verified-user-uuid>/<generated-report-uuid>/<sanitized-name>`.
- The server-only Supabase Python client uses `SUPABASE_SERVICE_ROLE_KEY`; it never appears in frontend settings or API responses. The private bucket is named `annual-reports`; if absent, it is provisioned with `public=false`, the configured size cap, and PDF MIME restriction. Existing public/misconfigured buckets fail closed. Avoid direct Storage policies for client roles so browser uploads cannot bypass API validation and ownership. Signed downloads are 300 seconds by default (configurable 60–900 seconds), issued only after an owner-scoped database lookup and object existence check.
- Create uploads Storage first then commits the Profile (if absent) and `UPLOADED` Report in one database transaction. If Storage or PostgreSQL fails, the operation attempts object cleanup and logs only a report UUID if compensation fails. Delete first sets `deleted_at` and commits, then deletes Storage, then sets `storage_deleted_at`; a failed Storage cleanup leaves a hidden tombstone that the same owner can retry with DELETE. ProcessingJob rows are retained with the soft-deleted report and are eventually subject to the existing configured FK cascade if a hard delete is separately performed.
- `GET /` lists only active owned reports and supports `limit` (1–100), `offset`, and optional status. Detail/status/download/delete use the same owner check. API response schemas omit `storage_path`, `user_id`, and provider metadata.
- Phase 4 scope note: report upload uses standard bounded Supabase uploads with a 25 MiB default rather than resumable/TUS uploads. The API does not automatically start the Phase 6 processor; processing remains manually/internally invoked and does not update report status. The retry route records a queued attempt but does not dispatch a Celery task. Redis-backed rate limiting is implemented separately from Celery. Bucket provisioning/live Storage calls, RLS on a real Supabase instance, and database migrations are pending configured integration verification.

### Phase 6 PDF processing and OCR

The reusable entry point is `modules/pdf_processing.pipeline.process_pdf(source, ...)` for a bounded local/file-like PDF, or `process_report(report_id, db, authorized_user_id=..., ...)` for a live stored report. `process_report` requires the verified owner UUID, queries the active report by both report ID and owner ID, reads the storage path only from that database row, and uses `ReportStorage.download`. It is not exposed as an HTTP endpoint and does not commit report state. A future Celery task should receive report and owner IDs only from the trusted enqueue path, then call this same function. The `cli.py` command runs a local PDF without database or API access.

Stages and limits:

1. `loader.py` uses PyMuPDF to inspect page count/metadata and extract selectable text page-by-page. It rejects bad signatures, unreadable/encrypted files, inputs above `MAX_PDF_SIZE_BYTES`, and documents above `PDF_PROCESSING_MAX_PAGES`. Each page records its number, text character count, image presence, and insufficient-text flag.
2. `detection.py` scores statement headings together with related revenue/expense/profit, asset/liability/equity, or operating/investing/financing cues. It emits page groups, confidence estimates, and evidence instead of relying on one keyword.
3. `tables.py` runs pdfplumber only on detected statement pages, tries line-based extraction then a text-alignment strategy, preserves rows/headers/page/table references, and carries headers across consecutive continuation pages when widths match. OCR rows are used if no native table is available on that page.
4. `normalization.py` uses Pandas to align cells and converts financial numeric strings to `Decimal`, including commas, parentheses, negatives, and decimal-comma values. It keeps values at the reported scale; currency and unit scale are separate fields and are never guessed when ambiguous. Unknown labels remain unmapped.
5. `ocr.py` renders only pages with insufficient selectable text that contain images, subject to an OCR page pixel cap and timeout, then calls pytesseract/Tesseract. In execution, this OCR enrichment follows initial text extraction and runs before final page detection so scanned headings can help identify statements. `TESSERACT_CMD` is honored only when configured. OCR text and confidence remain separate from PDF text; low confidence is flagged. If the whole PDF is scanned, each page may need OCR to detect statements.
6. `validation.py` retains missing and invalid values as `None` with statuses/warnings; flags duplicates, negative-value review, missing periods/currency/scale, inconsistent columns, and low OCR confidence; checks the Balance Sheet equation when all values align; and compares ending cash across statements when period/unit/currency match.

`PDFProcessingResult` contains document metadata, page text in memory for later RAG use, statement ranges, tables, normalized values, and warnings. `to_dict()` deliberately omits raw page text. Each value carries statement, raw and normalized label, decimal string, period and source period label, currency, symbol, scale/unit, source page/table, method, confidence, validation status, and warning codes. Results are not stored in the database yet; no business tables were added in this phase.

Controlled local run from `backend/`:

```powershell
python -m app.modules.pdf_processing.cli C:\path\to\annual-report.pdf
```

Known limitations: table extraction remains layout-dependent; merged/irregular cells can require manual review; metric mapping is deliberately conservative; periods and currency/scale may be unknown; cross-statement checks are limited to safe matching cases. Tests use synthetic text and scanned PDFs, and OCR execution itself is skipped where Tesseract is unavailable. No production accuracy claim is made. No Celery PDF task dispatch, PDF status update, financial ratio/risk calculation, database output schema, Pinecone/RAG, or chatbot code was added. Celery integration is intentionally deferred to Phase 5; do not start it automatically.

## 5. Core processing design

The uploaded PDF feeds two independent pipelines. Keep them separate because they have different outputs and validation needs.

### A. Structured financial extraction pipeline

1. Read PDF pages and extract selectable text with PyMuPDF.
2. Detect Income Statement, Balance Sheet, and Cash Flow Statement pages using headings, surrounding text, and context. Do not rely on a single keyword.
3. Extract table rows/columns with pdfplumber. Handle differing layouts and tables spanning multiple pages.
4. Normalize company-specific labels into consistent financial fields; clean and structure values with Pandas/NumPy.
5. Detect scanned or image-only pages and use Tesseract OCR through pytesseract as a fallback. OCR is part of the approved product design, not an optional future idea.
6. Validate reporting periods, units, currencies, missing versus zero values, negative values, duplicate values, and relevant accounting relationships. Preserve source page/table references and flag uncertain extraction rather than silently accepting it.
7. Calculate ratios, evaluate potential risks, and generate the financial health score with deterministic Python code.

Never silently convert missing values to zero. Preserve units, currencies, reporting periods, and extraction provenance/confidence where available. Treat OCR output as potentially uncertain and apply validation.

### B. Narrative RAG ingestion and retrieval pipeline

1. Extract narrative text and source metadata from the report without discarding it in the structured financial pipeline.
2. Clean and chunk text while preserving report ID, page number, section, and other useful provenance.
3. Generate embeddings through a provider-neutral interface. The embedding provider/model is pending.
4. Index vectors using Pinecone and LangChain's Pinecone integration.
5. At query time, authorize the user's report scope on the server before retrieving content. Use Pinecone metadata filters as an additional narrowing mechanism, not as the authorization boundary.
6. Pass retrieved passages and verified structured financial context to the selected LLM with clear system instructions. The LLM provider/model is pending.
7. Return grounded answers with report/page references where available. State clearly when there is insufficient evidence.

PostgreSQL is the canonical store for users, report metadata, ownership, structured financial metrics, risk results, processing status, and chat records. Pinecone is for vector retrieval and relevant chunk metadata/content; it is not the authority for identity, ownership, or relational application data.

## 6. Background processing: Celery + Redis

Celery + Redis is finalized for long-running work such as PDF extraction, OCR, financial analysis, and RAG ingestion.

- FastAPI validates the request, checks authorization, records the job/report state in PostgreSQL, and enqueues a Celery task.
- Redis is the broker (and may be used as a task result backend if deliberately configured); it is not the permanent application database.
- Celery workers execute long-running tasks outside HTTP request handlers.
- Supabase PostgreSQL remains the durable source of truth for job/report status and outputs.
- The frontend initially polls a status endpoint; real-time updates may be considered later if approved.
- Tasks must be idempotent, bounded with timeouts, safely retryable, and have explicit success/failure states. Classify permanent errors separately from transient errors.
- Do not pass entire PDFs or large document contents through Celery messages. Pass stable report/job identifiers and retrieve authorized input from private storage.
- Do not create excessive worker concurrency or background processes by default. Set conservative concurrency and resource limits for a development laptop.
- Local development on Windows should run the worker in Docker/Linux. Do not rely on native Windows Celery worker support.

## 7. Production security requirements

Security is required from the beginning. Implement controls in the correct layer; **not every control is middleware**. Never claim a control is implemented until the code and tests exist.

### Authentication and authorization

- Verify Supabase access tokens cryptographically. Validate signature and relevant claims such as issuer, audience (where applicable), and expiration. Never merely decode a JWT.
- Use a Supabase publishable key in browser code. Supabase secret keys (or legacy `service_role` keys) are server-only and must never enter Vite variables, frontend bundles, or chat.
- Obtain user identity only from the verified token. Never trust a request-body, query-string, or frontend-provided `user_id` as proof of identity.
- Keep authentication (who the user is) separate from authorization (what resources they can access).
- Check report ownership on every protected report, financial result, chat session, message, download, and RAG retrieval operation.
- Apply appropriate Supabase PostgreSQL Row Level Security (RLS) policies where applicable.
- Supabase secret keys (including the legacy `service_role` key) are server-only and can bypass RLS. If backend operations use elevated credentials, enforce explicit ownership/authorization checks in backend code as well.

### Request and file validation

- Use Pydantic schemas to validate request bodies, query/path parameters, and response contracts.
- Validate PDF uploads using MIME type, file signature/magic bytes, and a bounded PyMuPDF structural parse; never trust only the extension or browser-supplied content type.
- Enforce configurable upload-size and page-count limits; reject malformed, encrypted/unsupported, or non-PDF files according to explicit policy.
- Sanitize filenames. Never use a user-supplied filename directly as a filesystem path or trusted storage key.
- Validate extracted financial values and retain source/provenance information. Flag ambiguous extraction instead of guessing.
- Store uploads in a private Supabase Storage bucket. Authorize before creating or returning time-limited signed URLs.

### Database and SQL injection protection

- Use SQLAlchemy ORM expressions and bound parameters for user-controlled values.
- Never build SQL by concatenating untrusted input or interpolating it into SQL strings.
- If raw SQL is required, use bound parameters and review it carefully.
- Define foreign keys, uniqueness/check constraints, and indexes where appropriate.
- Manage schema changes with Alembic migrations; review generated migrations before applying them to shared/production databases.
- Supabase owns `auth.users`; reference its UUID primary key, but never create or migrate the Auth table from this application.
- Keep Supabase-managed Auth tables out of Alembic autogeneration. The initial profile PK FK targets `auth.users(id)` with `ON DELETE CASCADE`.
- Keep RLS enabled on application tables in the exposed `public` schema. Current migration policy: `anon` has no report access; `authenticated` has SELECT-only access to reports owned by `auth.uid()` where `deleted_at IS NULL`; browser roles have no write grants. Profiles and processing jobs remain inaccessible through the Data API. No direct Storage policies are granted to browser roles; keep `annual-reports` private and use the server API with explicit ownership checks before elevated Storage access.

### API security and abuse controls

- Configure CORS with explicit trusted frontend origins and required methods/headers. Do not use wildcard origins with credentialed requests.
- Rate limiting is implemented for authenticated profile and report routes with both IP and user keys; sensitive write/download operations have tighter per-user limits. Add appropriate limits to future chat and task-dispatch routes when they are implemented.
- Add request-body/upload size limits, timeouts, and concurrency controls for expensive operations.
- Use HTTPS in deployed environments and safe security headers appropriate to the deployment.
- Use centralized exception handling and consistent, non-sensitive error responses. Never expose stack traces, SQL statements, credentials, tokens, or internal paths to clients.

### Secrets and configuration

- Use typed, environment-based settings; never hardcode secrets.
- `backend/.env.example` contains placeholder values only. Real `.env` files are ignored by Git.
- Never expose Supabase secret/service-role keys, database credentials, Pinecone keys, Redis credentials, or future LLM/embedding credentials in frontend code or source control.
- Use Azure Key Vault or an equivalent managed secret store in production.
- Restrict access to production secrets and rotate exposed credentials.

### Logging and error handling

- Emit one JSON log object per event for FastAPI, Uvicorn, Celery, and application modules. Include UTC timestamp, level, logger, message, and request/correlation ID when a request is active.
- Log operational events, processing transitions, failures, and retry context.
- Apply redaction before formatting to messages, interpolation arguments, structured fields, exception tracebacks, and stack details. Mask access tokens, API keys, service-role keys, passwords, connection strings, private-key blocks, local paths in exception diagnostics, binary values, PDF contents, and unnecessary personal/financial information.
- Accept a supplied request ID only when it uses the restricted 1–128 character `[A-Za-z0-9._-]` form; otherwise generate a UUID. Include the effective ID in each log record and the response header.
- Return stable error codes/messages to clients; keep diagnostic details in protected server logs.
- Record task failure status durably in PostgreSQL and make retry behavior explicit.

### Testing and operational readiness

- Add unit tests for financial calculations, label normalization, document validation, and extraction rules.
- Add integration tests for database operations, authentication, authorization/ownership, upload validation, and background task behavior.
- Test attempts to access another user's report or chat content.
- Test invalid files, oversized files, missing values, malformed requests, rate limits, and error responses.
- Provide health/readiness checks and avoid reporting healthy when required infrastructure is unavailable.
- Do not log sensitive content while debugging or writing tests.

## 8. Planned AI Financial Copilot guardrails

These are approved design requirements for the planned chatbot/RAG feature, not claims of completed implementation. Implement them when building the Copilot. Do not assume an LLM or embedding provider before the decision is finalized.

- Treat uploaded documents, OCR text, retrieved passages, and user-provided content as untrusted data, never as system instructions.
- Defend against prompt injection in both user input and retrieved document content. Keep system/developer instructions separate from retrieved context and explicitly instruct the model not to follow instructions found inside documents.
- Enforce authenticated user identity and report ownership on the server before every retrieval. Do not rely on frontend-supplied report IDs or Pinecone metadata filters alone as authorization.
- Restrict every chat request to the report(s) the current user is allowed to access.
- Ground answers in retrieved passages and/or verified structured financial data. Do not invent numbers, disclosures, or citations.
- Cite report/page sources when available. Say when the report lacks sufficient evidence; do not fill gaps with unsupported assumptions.
- Keep all financial arithmetic in deterministic, tested Python functions. The model may explain results but may not replace the calculation engine.
- Validate tool/function inputs and outputs if tools are added. Expose only the minimum authorized operations needed by the Copilot.
- Apply appropriate input/output validation, token/resource limits, abuse controls, and safe failure behavior.
- Do not let retrieved text, a user prompt, or a tool result override system security instructions or authorization boundaries.

## 9. Dependency and installation rules

The backend dependency manifest is `backend/requirements.txt`. Install stable releases compatible with Python 3.14 and verify installation rather than assuming compatibility. The baseline includes:

- FastAPI, Uvicorn, Pydantic Settings, python-dotenv, and python-multipart.
- SQLAlchemy 2.0 series, Alembic, and Psycopg 3 for PostgreSQL.
- Supabase Python SDK, PyJWT with crypto support, and HTTPX.
- Celery with Redis support (`celery[redis]`).
- PyMuPDF, pdfplumber, pytesseract, Pillow, Pandas, and NumPy.
- LangChain, LangChain Community integrations, LangChain Text Splitters, and LangChain Pinecone integration.
- Testing/linting tools such as pytest and Ruff.
- A rate-limiting library if used by the actual implementation; keep the chosen implementation consistent and tested.

Tesseract OCR is a separate operating-system executable, not a pip package. Install it on the development host when host-side OCR is used, and install the Tesseract executable in the backend Docker image. `pytesseract` is only the Python wrapper. Verify availability with `tesseract --version` and configure the executable path only when it is not on `PATH`.

LangChain is modular. Install the framework, text-splitting support, community integrations, and Pinecone integration now. Do not install provider-specific LLM/embedding adapter packages until the user selects those providers. Do not attempt to install every optional integration in the LangChain ecosystem.

Do not downgrade Python or silently swap libraries to force installation. Report a genuine Python 3.14 compatibility issue with package name, version, and error output, then propose options.

## 10. Implementation and agent behavior rules

- Start by reading this file and the current README. This file governs development decisions; README provides the public project overview and durable usage instructions.
- Keep the application as a modular monolith: one FastAPI codebase with API and Celery worker processes, not microservices.
- Keep frontend and backend separate.
- Keep route handlers thin and business logic independently testable.
- Avoid unnecessary abstractions, speculative infrastructure, empty files, and extra folders beyond the agreed structure and required Alembic tooling.
- Never put credentials in code, logs, screenshots, tests, or frontend configuration.
- Do not implement two-company report comparison.
- Do not change the finalized stack without explicit approval.
- Separate finalized choices from pending choices in all plans and documentation.
- Before a substantial change, state the files to be changed and their purpose. Afterward, summarize what changed, how to run/test it, and what remains pending.
- Update this file whenever code or configuration changes, following the documentation workflow above. Update README only when stable public-facing information changes. Record approved product-scope or architecture changes here and reflect the resulting durable public description in README; never silently rewrite the user's decisions.
- Distinguish planned, scaffolded, implemented, and tested features accurately.

## 11. Initial setup task boundary (historical)

The first setup task is only to establish the agreed folder structure, install Python 3.14-compatible dependencies, create the minimal React/Vite/Tailwind frontend and FastAPI backend entry points, configure SQLAlchemy/Alembic, set up Celery/Redis, add the Tesseract executable to the backend Docker image, create environment examples and ignore rules, and prepare Docker Compose for API/worker/Redis.

Create a minimal health endpoint and verify that the API, Redis, and Celery worker can start. Do not implement the full PDF workflow, financial calculations, Pinecone ingestion, or chatbot yet. Do not invent the LLM or embedding provider. Keep this file accurate about implementation status; update README only for durable public-facing information, not routine progress.
