# Frontend API

This document is the contract the React UI uses against Django REST Framework. It lists the endpoints each screen calls, the JSON the serializers return, and TypeScript examples copied from those pages.

Interactive schema after login: `/api/v1/docs/` on the API origin (OpenAPI at `/api/v1/schema/`). Use that for field-level schema. Use this file for how the UI actually calls the API.

Validation at the HTTP boundary is DRF serializers, not Pydantic. Types on the client live in `frontend/src/api.ts`.

## How the UI calls the API

Every request goes through `api<T>()` in `frontend/src/api.ts`. The UI origin proxies `/api` and `/files` to the Django API. Pages call relative `/api/v1/…` paths only.

```ts
import { api, ApiError } from "./api";

const cases = await api<{ results: CaseRow[] }>("/api/v1/cases/?page_size=100");
```

The helper:

- sets `Accept: application/json`
- sets `Content-Type: application/json` unless the body is `FormData` (image queries)
- sends `credentials: "include"` so the session cookie is included
- reads the `csrftoken` cookie and sends `X-CSRFToken` on every request that has a token
- returns `undefined` on HTTP 204
- throws `ApiError` (`status`, `body`, `message`) when `response.ok` is false

```ts
export class ApiError extends Error {
  status: number;
  body: Record<string, unknown>;
}
```

TanStack Query wraps most GETs (list, poll, session). Mutations and login use `api()` inside `useMutation` or a form handler.

`rowsOf()` normalises a paginated `{ results }` envelope or a raw array. Custom actions such as `GET /cases/{id}/runs/` return a raw array. List viewsets return `{ results, next, previous, count? }`.

```ts
export function rowsOf<T>(data: T[] | { results?: T[] } | undefined | null): T[] {
  if (!data) return [];
  return Array.isArray(data) ? data : data.results ?? [];
}
```

## Authentication, CSRF, and session

Auth is Django session authentication (`SessionAuthentication`). There is no JWT. After login the browser holds a session cookie and a CSRF cookie.

Unsafe methods (`POST`, `PATCH`, `DELETE`) require CSRF. Login therefore starts with a GET that sets the cookie:

1. `GET /api/v1/auth/csrf/` (204, `Set-Cookie: csrftoken=...`)
2. `POST /api/v1/auth/login/` with `X-CSRFToken`

`RequireSession` (`frontend/src/screens/RequireSession.tsx`) gates every route except `/login`. It calls `GET /api/v1/auth/session/`. A 401 sends the user to `/login`. An `mfa-incomplete` type sends them to `/login/mfa`.

Example accounts (password equals username): `investigator`, `administrator`. MFA after password is controlled by server settings (`MFA_CHALLENGE_AFTER_PASSWORD`); the MFA routes below exist in the UI and API.

Roles: `administrator`, `investigator`, `analyst`, `reviewer`, `auditor`.

Capabilities returned on login/session (display only; the API enforces permission classes, not these strings):

| Role | Typical capabilities |
| --- | --- |
| investigator, administrator | `case.create`, `evidence.register`, `search.query`, `review.rate` |
| analyst | `search.query` |
| reviewer | `review.rate`, `review.approve`, `export.request` |
| administrator | plus `admin.users`, `admin.corpora` |
| auditor | `audit.read` |

## Pagination

Default page size 25, maximum 100 (`page_size` query param; larger values are clamped). List viewsets use page-number pagination:

```json
{
  "count": 12,
  "next": null,
  "previous": null,
  "results": []
}
```

The UI usually requests `?page_size=100`. Nested actions (`/cases/{id}/runs/`, `/runs/{id}/queries/`, `/queries/{id}/results/`) return a JSON array, not the paginated envelope.

## Errors

All failures use one `application/problem+json` envelope (`apps.common.exceptions.problem`):

```json
{
  "type": "https://shoerag.example/errors/invalid-credentials",
  "title": "Authentication failed",
  "status": 401,
  "detail": "Authentication failed.",
  "instance": "/api/v1/auth/login/",
  "correlation_id": "…",
  "errors": []
}
```

`api()` surfaces `body.detail` or `body.title` as `ApiError.message`. Field errors are in `errors[]` as `{ field, code, detail }`.

Statuses the UI already handles:

| Status | Meaning | Typical `type` slug |
| --- | --- | --- |
| 400 | Validation | `validation-failed` |
| 401 | Bad credentials or MFA incomplete | `invalid-credentials`, `mfa-incomplete`, `mfa-invalid` |
| 403 | Role or permission | `not-permitted` |
| 404 | Missing object or file | `not-found` |
| 409 | Conflict (results not ready, duplicate) | `results-not-ready`, `username-not-unique` |
| 422 | Constraint refused (weights, rating range) | `fusion-weight-out-of-range`, `rating-out-of-range` |
| 423 | Account locked after failed logins | `account-locked` |
| 429 | Rate limited | `rate-limited` |
| 503 | Database not ready | `service-unavailable` |

Quote `correlation_id` when reporting a 500. Header `Correlation-ID` is also returned.

## Shared TypeScript types

From `frontend/src/api.ts`. Response JSON uses `snake_case`, matching these fields.

```ts
export type SessionUser = {
  public_id: string;
  username: string;
  first_name: string;
  last_name: string;
  email: string;
  role: string;
  mfa_enforced: boolean;
  is_active: boolean;
};

export type Session = {
  user: SessionUser;
  capabilities: string[];
  mfa_required?: boolean;
  enrol_required?: boolean;
};

export type LoginResult = {
  user: SessionUser | null;
  mfa_required: boolean;
  enrol_required?: boolean;
  capabilities?: string[];
  methods?: string[];
};

export type CaseRow = {
  public_id: string;
  name: string;
  reference: string;
  description: string;
  status: string; // open | under_review | closed
  opened_at: string;
  closed_at: string | null;
};

export type CorpusRow = {
  id: number;
  code: string;
  name: string;
  description: string;
  data_classification: string; // real | synthetic
  is_active: boolean;
  evidence_count?: number;
  indexed_count?: number;
};

export type MountRow = {
  id: number;
  corpus: number;
  corpus_code: string;
  path: string;
  label: string;
  is_enabled: boolean;
  created_at?: string;
  last_scan_at: string | null;
  last_scan_status: string | null;
  last_scan_files_seen: number;
  last_scan_error_count: number;
};

export type EncoderRow = {
  id: number;
  name: string;
  family: string; // clip | dinov2
  version: string;
  preprocess_version: string;
  dimensions: number;
  is_active: boolean;
};

export type EvidenceRow = {
  public_id: string;
  corpus: number;
  corpus_code?: string;
  case: number | null;
  original_filename: string;
  source_path?: string;
  state: string; // registered | indexed | missing | quarantined
  registered_at: string;
  thumbnail_access?: string;
};

export type AccessGrant = {
  url: string;
  expires_at: string;
  variant: string;
};

export type RecallRow = {
  encoder_id: number;
  encoder_name: string;
  family: string;
  measured_recall: string | null;
  recall_k: number | null;
  measured_at: string | null;
  m: number | null;
  ef_construction: number | null;
  ef_search: number | null;
};

export type RunRow = {
  public_id: string;
  label: string;
  status: string; // pending | running | complete | failed | cancelled
  top_k: number;
  model_weight: string;
  metadata_weight: string;
  created_at?: string;
  query_count?: number;
  saved?: boolean;
};

export type QueryRow = {
  public_id: string;
  sequence: number;
  query_type: string; // image | text
  status: string; // pending | encoding | searching | complete | failed
  result_count: number;
  routed_spectrum: string | null;
  query_text?: string | null;
  probe_filename?: string | null;
  probe_evidence_id?: string | null;
};

export type ResultEvidence = {
  public_id: string;
  original_filename: string;
  source_path: string;
  corpus_code: string;
  state: string;
  thumbnail_access: string;
};

export type ResultRow = {
  public_id: string;
  rank: number;
  score_fused: string | number | null;
  score_model: string | number | null;
  score_clip: string | number | null;
  score_dinov2: string | number | null;
  score_metadata?: string | number | null;
  evidence_file: number;
  evidence?: ResultEvidence;
  approval_state?: string | null;
};

export type TaskRow = {
  public_id: string;
  task_name: string;
  status: string; // queued | started | running | retrying | succeeded | failed | cancelled
  phase: string | null;
  progress_current: number;
  progress_total: number | null;
  queued_at: string;
  started_at: string | null;
  finished_at: string | null;
};

export type AuditRow = {
  public_id: string;
  occurred_at: string;
  action: string;
  actor_username: string;
  actor_role: string;
  target_type: string;
  target_public_id: string | null;
  outcome: string;
};

export type ReportRow = {
  public_id: string;
  format: string;
  status: string;
  available_at: string | null;
  expires_at: string | null;
  download_count: number;
};

export type SettingRow = {
  id: number;
  key: string;
  value: unknown;
  description: string;
  updated_at: string;
};

export type Page<T> = { results: T[]; next: string | null; previous: string | null };
```

## Screen to endpoint map

| Route | Screen | Endpoints used |
| --- | --- | --- |
| `/login` | `LoginPage.tsx` | `GET auth/csrf/`, `POST auth/login/` |
| `/login/mfa` | `MfaPage.tsx` | `POST auth/mfa/verify/`, `POST auth/logout/` |
| (all signed-in routes) | `RequireSession.tsx` | `GET auth/session/` |
| shell | `Shell.tsx` | `GET tasks/?…`, `POST auth/logout/` |
| `/datasets` | `DatasetsPage.tsx` | `corpora/`, `mounts/`, `POST mounts/`, `POST mounts/{id}/scan/`, `PATCH mounts/{id}/`, `evidence/?mount=` |
| `/register-evidence` | `IndexPage.tsx` | `corpora/`, `corpora/coverage/`, `encoders/recall/`, `POST corpora/{id}/encode/`, `POST tasks/{id}/cancel/`, `evidence/?corpus=&state=indexed` |
| `/search` | `SearchPage.tsx` | `cases/`, `corpora/`, `POST cases/`, `cases/{id}/runs/`, `runs/{id}/queries/`, `queries/{id}/`, `queries/{id}/results/`, `POST runs/{id}/save/`, `review/ratings/`, `review/approvals/`, `review/notes/` |
| `/cases`, `/cases/:id` | `CasesPage.tsx` | `cases/`, `POST cases/`, `cases/{id}/`, `cases/{id}/runs/`, `POST cases/{id}/status/`, `POST reports/` |
| `/review` | `ReviewPage.tsx` | `cases/`, `cases/{id}/runs/`, `runs/{id}/queries/`, `queries/{id}/results/`, ratings, notes, approvals |
| thumbs / fullscreen | `SignedThumb.tsx` | `POST evidence/{id}/access-url/` |
| Task Centre | `TaskCentre.tsx` | same task list as shell, `POST tasks/{id}/cancel/` |
| `/workers` | `WorkersPage.tsx` | `encoders/`, `tasks/` |
| `/settings` | `SettingsPage.tsx` | `settings/` |
| `/admin` | `AdminPage.tsx` | `users/`, `POST users/`, `POST users/{id}/deactivate/`, `POST users/{id}/password/` |
| `/audit` | `AuditPage.tsx` | `audit/` |
| `/account/mfa/enrol` | `MfaEnrolPage.tsx` | `POST auth/mfa/totp/`, `POST auth/mfa/totp/confirm/` |
| `/video`, `/analytics` | `DeferredPage.tsx` | none |

---

## Login (`LoginPage.tsx`)

`GET /api/v1/auth/csrf/`  
AllowAny. Sets the CSRF cookie. Body empty. Status 204.

`POST /api/v1/auth/login/`  
AllowAny. Body validated by `LoginSerializer`.

Request:

```json
{ "username": "investigator", "password": "investigator" }
```

Success (no MFA challenge):

```json
{
  "user": {
    "public_id": "…",
    "username": "investigator",
    "first_name": "",
    "last_name": "",
    "email": "",
    "role": "investigator",
    "mfa_enforced": true,
    "is_active": true
  },
  "mfa_required": false,
  "enrol_required": false,
  "capabilities": ["auth.session", "case.create", "evidence.register", "search.query", "review.rate"]
}
```

If MFA is challenged (`MFA_CHALLENGE_AFTER_PASSWORD`): `user` is null, `mfa_required` is true, `enrol_required` is true when TOTP is not confirmed, `methods` is `["totp", "recovery_code"]`. Session is created but marked pending.

Failures: 401 `invalid-credentials`, 423 locked, 429 rate limit, 503 database down. The page maps those statuses to copy; it does not show `detail` to the user.

```ts
await api("/api/v1/auth/csrf/");
const result = await api<LoginResult>("/api/v1/auth/login/", {
  method: "POST",
  body: JSON.stringify({ username, password }),
});
if (result.mfa_required && result.enrol_required) navigate("/account/mfa/enrol");
else if (result.mfa_required) navigate("/login/mfa");
else navigate(intended || (result.user?.role === "auditor" ? "/audit" : "/datasets"));
```

---

## Session gate (`RequireSession.tsx`)

`GET /api/v1/auth/session/`  
IsAuthenticated.

```json
{
  "user": { "public_id": "…", "username": "investigator", "role": "investigator", "…": "…" },
  "capabilities": ["auth.session", "case.create"],
  "mfa_required": false,
  "enrol_required": false
}
```

If the session still has `mfa_pending_user_id`, `mfa_required` is true and `capabilities` is `[]`.

```ts
useQuery({
  queryKey: ["session"],
  queryFn: () => api<Session>("/api/v1/auth/session/"),
  retry: false,
});
```

---

## Logout (`Shell.tsx`)

`POST /api/v1/auth/logout/`  
IsAuthenticated. 204. Records `LOGOUT` and clears the session.

```ts
await api("/api/v1/auth/logout/", { method: "POST" });
```

---

## MFA verify (`MfaPage.tsx`)

`POST /api/v1/auth/mfa/verify/`  
AllowAny, but a pending MFA session is required.

```json
{ "code": "123456" }
```

`code` or `token` is accepted. Spaces are stripped. TOTP devices then recovery (static) devices are tried. Success shape matches login. Failures: 401 `mfa-incomplete` (no pending login) or `mfa-invalid`.

```ts
await api<LoginResult>("/api/v1/auth/mfa/verify/", {
  method: "POST",
  body: JSON.stringify({ code: value }),
});
navigate("/datasets");
```

---

## MFA enrol (`MfaEnrolPage.tsx`)

`POST /api/v1/auth/mfa/totp/`  
IsAuthenticated. Creates an unconfirmed TOTP device. The secret is shown once.

```json
{
  "secret": "…",
  "otpauth_uri": "otpauth://totp/…",
  "warning": "This key is shown once. It cannot be retrieved later."
}
```

`POST /api/v1/auth/mfa/totp/confirm/`

```json
{ "code": "123456" }
```

Success: `{ "recovery_codes": ["…", "…"] }` (10 hex codes). Confirms the device, sets `mfa_enforced` and `mfa_confirmed_at`, replaces recovery codes.

```ts
const start = await api<Start>("/api/v1/auth/mfa/totp/", { method: "POST" });
const { recovery_codes } = await api<{ recovery_codes: string[] }>(
  "/api/v1/auth/mfa/totp/confirm/",
  { method: "POST", body: JSON.stringify({ code }) },
);
```

---

## Datasets (`DatasetsPage.tsx`)

Investigator adds a folder mount to a corpus. Creating a mount also queues `datasets.scan_mount` on Celery. Progress is polled via the shell Task Centre.

`GET /api/v1/corpora/?page_size=100`  
IsAuthenticated. Paginated `CorpusRow[]`.

`GET /api/v1/mounts/?page_size=100`  
IsAuthenticated. Paginated `MountRow[]`.

`POST /api/v1/mounts/`  
IsInvestigator. Path is resolved and validated as an existing readable folder. Inactive corpus is rejected.

```json
{ "corpus": 1, "path": "/data/evidence/ndfsim", "label": "NDFsim" }
```

201 `MountRow`. Scan starts immediately.

`POST /api/v1/mounts/{id}/scan/`  
IsInvestigator. Re-queues scan. 202 if a new task was created, 200 if the same in-flight task is reused. Body: `{ "public_id": "<task public_id>" }`.

`PATCH /api/v1/mounts/{id}/`  
IsAdministrator. The Remove action sets `is_enabled: false` (soft disable, not DELETE).

```json
{ "is_enabled": false }
```

`GET /api/v1/evidence/?mount={id}&page_size=100`  
IsAuthenticated. Filters `EvidenceFile` by mount. Used by the folder preview grid.

```ts
useQuery({
  queryKey: ["corpora"],
  queryFn: () => api<{ results: CorpusRow[] }>("/api/v1/corpora/?page_size=100"),
});
useQuery({
  queryKey: ["mounts"],
  queryFn: () => api<{ results: MountRow[] }>("/api/v1/mounts/?page_size=100"),
});

const row = await api<MountRow>("/api/v1/mounts/", {
  method: "POST",
  body: JSON.stringify({ corpus: Number(corpus), path, label }),
});
await api(`/api/v1/mounts/${id}/scan/`, { method: "POST" });
await api(`/api/v1/mounts/${id}/`, {
  method: "PATCH",
  body: JSON.stringify({ is_enabled: false }),
});
const files = await api<{ results: EvidenceRow[] }>(
  `/api/v1/evidence/?mount=${mount.id}&page_size=100`,
);
```

Evidence `state`: `registered` after scan, `indexed` after encode, `missing` if bytes cannot be read.

---

## Register evidence / Index (`IndexPage.tsx`)

Route `/register-evidence` (legacy `/index` redirects here).

`GET /api/v1/corpora/?page_size=100`  
`GET /api/v1/corpora/coverage/`  
IsAuthenticated. Coverage returns `{ "results": CorpusRow[] }` with `evidence_count` and `indexed_count` annotated.

`GET /api/v1/encoders/recall/`  
IsAuthenticated. `{ "results": RecallRow[] }` for active encoders (ANN build recall if a build exists).

`POST /api/v1/corpora/{id}/encode/`  
IsInvestigator. 202. Queues encode tasks for active encoders. Empty body.

```json
{
  "runs": [{ "public_id": "…", "created": true }],
  "already_in_progress": false
}
```

If every pair is a reuse, `already_in_progress` is true and the page shows that message.

`GET /api/v1/tasks/?page_size=100&task_name=indexing.encode_corpus&task_name=indexing.encode_batch&task_name=datasets.scan_mount`  
(see Tasks). Polled while encode is in flight.

`POST /api/v1/tasks/{public_id}/cancel/`  
Pause on the page cancels in-flight encode tasks.

`GET /api/v1/evidence/?corpus={id}&state=indexed&page_size=24`  
Indexed preview strip.

```ts
const coverage = await api<{ results: CorpusRow[] }>("/api/v1/corpora/coverage/");
const recall = await api<{ results: RecallRow[] }>("/api/v1/encoders/recall/");

const started = await api<{
  runs: { public_id: string; created?: boolean }[];
  already_in_progress?: boolean;
}>(`/api/v1/corpora/${id}/encode/`, { method: "POST" });

await api(`/api/v1/tasks/${task.public_id}/cancel/`, { method: "POST" });

const preview = await api<{ results: EvidenceRow[] }>(
  `/api/v1/evidence/?corpus=${corpusId}&state=indexed&page_size=24`,
);
```

---

## Search (`SearchPage.tsx`)

This is the main retrieval flow. Ranking on the worker:

- `s_model = λ × s_DINO + (1 − λ) × s_CLIP`
- `s_final = α × s_model + (1 − α) × s_meta`

The page sends `model_weight` as λ and `metadata_weight` as `(1 − α)`. Text queries force CLIP only (`use_clip: true`, `use_dinov2: false`).

### Load lists

```ts
api<{ results: CaseRow[] }>("/api/v1/cases/?page_size=100");
api<{ results: CorpusRow[] }>("/api/v1/corpora/?page_size=100");
api<RunRow[]>(`/api/v1/cases/${caseId}/runs/`);
api<QueryRow[]>(`/api/v1/runs/${historyRun}/queries/`);
```

`GET /cases/{id}/runs/` returns a **raw array** (newest first, `query_count` annotated).  
`GET /runs/{id}/queries/` returns a **raw array** ordered by `sequence`.

### Create case from Search

`POST /api/v1/cases/`  
IsInvestigator.

```json
{ "name": "Case 12" }
```

201 `CaseRow`. Optional fields: `reference`, `description`. Duplicate name for the same owner: 409 `case-name-not-unique`.

### Create run then queries

`POST /api/v1/cases/{caseId}/runs/`  
IsAuthenticated (create uses investigator on the parent case). 201 `RunRow`.

```json
{
  "top_k": 10,
  "model_weight": "0.500",
  "metadata_weight": "0.500",
  "corpus_ids": [1],
  "use_clip": true,
  "use_dinov2": true
}
```

Optional `label`. Defaults: `top_k=10`, `model_weight=0.500`, `metadata_weight=0.150`, both encoders on. Weights must be in `[0, 1]`. `top_k` must be 1–500. At least one encoder must be selected.

`POST /api/v1/runs/{runId}/queries/`  
CanSearch. 202 `QueryRow`. Status starts `pending`; Celery encodes and searches.

Text (JSON):

```json
{ "query_type": "text", "query_text": "black trainer left" }
```

Image (`FormData`, no JSON Content-Type):

```
file: <binary>
query_type: image
corpus: 1
```

The probe is registered as evidence (SHA-256; already-registered content is reused). A corpus is required; the page sends the first selected corpus.

Optional `probe_public_id` to search from an existing content object (not used by the current UI).

### Poll query, then load results

`GET /api/v1/queries/{queryId}/`  
Polled every 2.5s while status is not `complete`.

`GET /api/v1/queries/{queryId}/results/`  
Raw `ResultRow[]` ordered by `rank`. 409 `results-not-ready` if status is not `complete`. The page treats 409 as an empty list. First successful view records `RESULTS_VIEWED`.

Each result includes nested `evidence` and `thumbnail_access` (`/api/v1/evidence/{public_id}/access-url/`). `score_fused` is `s_final`. The grid shows `confidence=` as that value to three decimals.

### Save run

`POST /api/v1/runs/{runId}/save/`  
Marks the run saved so Cases / Search can reload it (`saved: true` on `RunRow`).

### Rate, approve, note (from result cards)

Same review endpoints as Review (below).

```ts
const run = await api<RunRow>(`/api/v1/cases/${caseId}/runs/`, {
  method: "POST",
  body: JSON.stringify({
    top_k: Math.min(100, Math.max(1, Number(k) || 10)),
    model_weight: lambda.toFixed(3),
    metadata_weight: (1 - alpha).toFixed(3),
    corpus_ids: corpusIds,
    use_clip: useClip,
    use_dinov2: useDinov2,
  }),
});

const textQuery = await api<QueryRow>(`/api/v1/runs/${run.public_id}/queries/`, {
  method: "POST",
  body: JSON.stringify({ query_type: "text", query_text: item.text }),
});

const body = new FormData();
body.append("file", item.file);
body.append("query_type", "image");
if (corpusIds[0]) body.append("corpus", String(corpusIds[0]));
const imageQuery = await api<QueryRow>(`/api/v1/runs/${run.public_id}/queries/`, {
  method: "POST",
  body,
});

useQuery({
  queryKey: ["query", publicId],
  queryFn: () => api<QueryRow>(`/api/v1/queries/${publicId}/`),
  refetchInterval: 2500,
  enabled: status !== "complete",
});

const results = await api<ResultRow[]>(`/api/v1/queries/${publicId}/results/`);

await api<RunRow>(`/api/v1/runs/${runId}/save/`, { method: "POST" });
```

---

## Cases / Sessions (`CasesPage.tsx`)

List: `GET /api/v1/cases/?page_size=100`.  
Create: `POST /api/v1/cases/` with `{ name, reference }`.  
Detail: `GET /api/v1/cases/{public_id}/`.  
Runs: `GET /api/v1/cases/{public_id}/runs/`.  
Close: `POST /api/v1/cases/{public_id}/status/` with `{ "status": "closed" }`. Allowed statuses: `open`, `under_review`, `closed`.  
Report: `POST /api/v1/reports/` (see Reports).

```ts
api<{ results: CaseRow[] }>("/api/v1/cases/?page_size=100");
api<CaseRow>("/api/v1/cases/", {
  method: "POST",
  body: JSON.stringify({ name, reference }),
});
api<CaseRow>(`/api/v1/cases/${id}/`);
api<RunRow[]>(`/api/v1/cases/${id}/runs/`);
api(`/api/v1/cases/${id}/status/`, {
  method: "POST",
  body: JSON.stringify({ status: "closed" }),
});
api<ReportRow>("/api/v1/reports/", {
  method: "POST",
  body: JSON.stringify({ case_public_id: id, format: "pdf" }),
});
```

`POST /api/v1/cases/{id}/members/` exists (`user_public_id`, `access_level`) but is not called by the UI.

---

## Review (`ReviewPage.tsx`)

Human-in-the-loop: pick case, run, query, then rate / note / approve a result. Agentic tab does not call the API.

```ts
api<{ results: CaseRow[] }>("/api/v1/cases/?page_size=100");
api<RunRow[]>(`/api/v1/cases/${caseId}/runs/`);
api<QueryRow[]>(`/api/v1/runs/${runId}/queries/`);
api<ResultRow[]>(`/api/v1/queries/${queryId}/results/`);
```

`POST /api/v1/review/ratings/`  
CanReview.

```json
{ "result_public_id": "…", "value": 4, "scope": "result" }
```

`value` is 1–500 on the serializer field list but the DB constraint is 1–5 (`rating-out-of-range`). Same author + result upserts. Search stars use the same body.

`POST /api/v1/review/notes/`

```json
{ "result_public_id": "…", "body": "Outsole pattern matches exhibit A.", "scope": "result" }
```

Body length 1–20000 after sanitisation (`note-length`).

`POST /api/v1/review/approvals/`  
Toggles validation approval for that result (one approval row per result). Response includes `state` (`requested`, `approved`, `countersigned`, `withdrawn`, …). Search treats `approved` / `countersigned` as approved and any other state as withdrawn.

```json
{ "result_public_id": "…" }
```

`PATCH /api/v1/review/approvals/{public_id}/` is registered for `action`: `decide` (`approved` bool), `countersign`, `withdraw`. The current UI only POSTs the toggle.

```ts
api("/api/v1/review/ratings/", {
  method: "POST",
  body: JSON.stringify({ result_public_id: row.public_id, value, scope: "result" }),
});
api("/api/v1/review/notes/", {
  method: "POST",
  body: JSON.stringify({ result_public_id: row.public_id, body: noteBody, scope: "result" }),
});
const approval = await api<{ state: string }>("/api/v1/review/approvals/", {
  method: "POST",
  body: JSON.stringify({ result_public_id: row.public_id }),
});
```

---

## Signed images (`SignedThumb.tsx`)

Bytes are not served as raw `/media/` paths. The UI posts for a short-lived grant (default TTL 600 seconds, `SIGNED_URL_TTL_SECONDS`), then uses `grant.url` (usually `/files/{token}` on the same origin).

`POST /api/v1/evidence/{public_id}/access-url/`  
IsAuthenticated. Records `EVIDENCE_URL_ISSUED`.

```json
{ "variant": "thumbnail" }
```

`variant` is `thumbnail` (derived artefact if present) or `original`.

```json
{
  "url": "/files/…",
  "expires_at": "2026-09-27T12:30:00Z",
  "variant": "thumbnail",
  "audit_event": "…"
}
```

```ts
useQuery({
  queryKey: ["access-url", evidenceId, variant],
  queryFn: () =>
    api<AccessGrant>(`/api/v1/evidence/${evidenceId}/access-url/`, {
      method: "POST",
      body: JSON.stringify({ variant }),
    }),
  enabled: Boolean(evidenceId),
  staleTime: 45_000,
  retry: false,
});
```

404 is shown as “Missing file”.

---

## Tasks (`Shell.tsx`, `TaskCentre.tsx`, `IndexPage.tsx`)

`GET /api/v1/tasks/`  
IsAuthenticated. Administrators see all tasks; others see only their own. Repeat `task_name` to filter (comma-separated also works).

Constants in `frontend/src/taskProgress.ts`:

```ts
export const TASKS_QUERY =
  "/api/v1/tasks/?page_size=100&task_name=indexing.encode_corpus&task_name=indexing.encode_batch&task_name=datasets.scan_mount";
export const ENCODE_CORPUS_QUERY =
  "/api/v1/tasks/?page_size=20&task_name=indexing.encode_corpus";
```

In-flight statuses used by the bar: `queued`, `started`, `running`, `retrying`.

`POST /api/v1/tasks/{public_id}/cancel/`  
202. Returns the updated `TaskRow`. Celery must be running (`default`, `encoding`, `reporting`, `maintenance`). Job progress is stored on `TaskRun` in PostgreSQL, not a Celery result backend. If the worker is down, tasks stay queued.

```ts
useQuery({
  queryKey: ["tasks"],
  queryFn: () => api<{ results: TaskRow[] }>(TASKS_QUERY),
  refetchInterval: 4000,
});
await api(`/api/v1/tasks/${id}/cancel/`, { method: "POST" });
```

---

## Workers (`WorkersPage.tsx`)

`GET /api/v1/encoders/`  
`GET /api/v1/tasks/` (unfiltered, first page).

```ts
api<{ results: EncoderRow[] }>("/api/v1/encoders/");
api<{ results: TaskRow[] }>("/api/v1/tasks/");
```

`POST /api/v1/encoders/` is administrator-only and unused by the UI.

---

## Reports (`CasesPage.tsx`)

`POST /api/v1/reports/`  
CanExport (reviewer / administrator; investigator is export-capable in the role model). Queues `reporting.render_report`.

```json
{ "case_public_id": "…", "format": "pdf" }
```

Optional `scope` object. 201 `ReportRow` (`status` starts pending).

`POST /api/v1/reports/{public_id}/access-url/` issues a signed download when `storage_key` is set; 409 if not ready. The current UI only creates the report and shows `public_id` + `status`.

---

## Settings (`SettingsPage.tsx`)

Administrator only.

`GET /api/v1/settings/`  
`POST /api/v1/settings/`

```json
{ "key": "search.default_lambda", "value": 0.5, "description": "" }
```

`value` is JSON. Duplicate key: 409 `setting-key-exists`. `PATCH /api/v1/settings/{id}/` exists; the page always POSTs.

```ts
api<{ results: SettingRow[] }>("/api/v1/settings/");
api("/api/v1/settings/", {
  method: "POST",
  body: JSON.stringify({ key, value: JSON.parse(value || "null"), description: "" }),
});
```

---

## Admin users (`AdminPage.tsx`)

Administrator only. Lookup is `public_id`.

`GET /api/v1/users/`  
`POST /api/v1/users/`

```json
{ "username": "analyst1", "password": "analyst1", "role": "analyst" }
```

Roles: `administrator`, `investigator`, `analyst`, `reviewer`, `auditor`. Export-capable roles get `mfa_enforced` on create.

`POST /api/v1/users/{public_id}/deactivate/`  
Sets `is_active` false.

`POST /api/v1/users/{public_id}/password/`

```json
{ "password": "new-password" }
```

204 on success.

`POST /api/v1/users/{public_id}/role/` with `{ "role": "reviewer" }` is registered and unused by the UI.

```ts
api<{ results: SessionUser[] }>("/api/v1/users/");
api("/api/v1/users/", {
  method: "POST",
  body: JSON.stringify({ username, password, role }),
});
api(`/api/v1/users/${id}/deactivate/`, { method: "POST" });
api(`/api/v1/users/${id}/password/`, {
  method: "POST",
  body: JSON.stringify({ password: next }),
});
```

---

## Audit (`AuditPage.tsx`)

`GET /api/v1/audit/`  
IsAuditor. Reading the list records `AUDIT_READ`. Append-only; no update or delete routes.

```ts
api<{ results: AuditRow[] }>("/api/v1/audit/");
```

---

## Version and health (not used by the SPA)

`GET /healthz` — `{ "status": "ok" }`, no auth, no database.  
`GET /api/v1/version/` — IsAuthenticated, `{ "name": "shoerag-web", "version": "1.0.0" }`.

---

## Registered actions the UI does not call

These exist on the viewsets. Documented so a second client does not invent a second contract.

| Method | Path | Notes |
| --- | --- | --- |
| POST | `/api/v1/cases/{id}/members/` | `user_public_id`, `access_level` |
| PATCH | `/api/v1/cases/{id}/` | Case fields |
| POST | `/api/v1/evidence/uploads/` | Multipart `file` + `corpus` (Search uses run query upload instead) |
| GET | `/api/v1/evidence/{id}/artifacts/` | Derived artefacts |
| GET | `/api/v1/evidence/{id}/verifications/` | Digest checks |
| POST | `/api/v1/corpora/` | Administrator create corpus |
| PATCH | `/api/v1/review/approvals/{id}/` | `decide` / `countersign` / `withdraw` |
| POST | `/api/v1/reports/{id}/access-url/` | Download when ready |
| POST | `/api/v1/users/{id}/role/` | Change role |
| GET | `/api/v1/schema/`, `/api/v1/docs/` | OpenAPI / Swagger |

---

## End-to-end example: login, index, search, approve

This is the investigator path the screens implement. Celery must be running for scan, encode, and search to leave `queued`.

```ts
import { api, CaseRow, CorpusRow, LoginResult, QueryRow, ResultRow, RunRow } from "./api";

async function runInvestigatorFlow(probe: File) {
  await api("/api/v1/auth/csrf/");
  await api<LoginResult>("/api/v1/auth/login/", {
    method: "POST",
    body: JSON.stringify({ username: "investigator", password: "investigator" }),
  });

  const corpora = await api<{ results: CorpusRow[] }>("/api/v1/corpora/?page_size=100");
  const corpus = corpora.results[0];

  await api(`/api/v1/corpora/${corpus.id}/encode/`, { method: "POST" });
  // Poll GET /api/v1/tasks/?task_name=indexing.encode_corpus until succeeded.

  const caseRow = await api<CaseRow>("/api/v1/cases/", {
    method: "POST",
    body: JSON.stringify({ name: "Investigation 2026-001" }),
  });

  const run = await api<RunRow>(`/api/v1/cases/${caseRow.public_id}/runs/`, {
    method: "POST",
    body: JSON.stringify({
      top_k: 10,
      model_weight: "0.850",
      metadata_weight: "0.150",
      corpus_ids: [corpus.id],
      use_clip: true,
      use_dinov2: true,
    }),
  });

  const form = new FormData();
  form.append("file", probe);
  form.append("query_type", "image");
  form.append("corpus", String(corpus.id));
  let query = await api<QueryRow>(`/api/v1/runs/${run.public_id}/queries/`, {
    method: "POST",
    body: form,
  });

  while (query.status !== "complete" && query.status !== "failed") {
    await new Promise((r) => setTimeout(r, 2500));
    query = await api<QueryRow>(`/api/v1/queries/${query.public_id}/`);
  }

  const results = await api<ResultRow[]>(`/api/v1/queries/${query.public_id}/results/`);
  const top = results[0];
  await api("/api/v1/review/ratings/", {
    method: "POST",
    body: JSON.stringify({ result_public_id: top.public_id, value: 5, scope: "result" }),
  });
  await api("/api/v1/review/approvals/", {
    method: "POST",
    body: JSON.stringify({ result_public_id: top.public_id }),
  });
  await api<RunRow>(`/api/v1/runs/${run.public_id}/save/`, { method: "POST" });
}
```

On a React page the same calls sit in `useQuery` / `useMutation` as in `SearchPage.tsx` and `IndexPage.tsx`. Do not add a second HTTP client.
