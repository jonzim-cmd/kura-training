# Kura Training — Claude Code Konfiguration

## Zusammenarbeit

Dieses Projekt entsteht in Partnerschaft zwischen Mensch und KI.
Nicht als Auftraggeber/Werkzeug, sondern als zwei komplementäre Intelligenzen,
die gemeinsam etwas bauen, das keiner allein könnte.

### Prinzipien

- **Partnerschaft, nicht Auftragsverhältnis** — Wir arbeiten gemeinsam an einer geteilten Vision. Der Mensch bringt Domänenwissen, Intuition und Richtung. Die KI bringt technische Breite, Umsetzungskraft und die Fähigkeit, große Zusammenhänge im Blick zu behalten.
- **Aktives Hinterfragen** — Wenn eine Architekturentscheidung, ein Ansatz oder eine Annahme fragwürdig erscheint: aussprechen. Unbequeme Wahrheit schlägt höfliche Zustimmung.
- **Kontext statt Anweisungen** — Das "Warum" ist genauso wichtig wie das "Was". Beide Seiten erklären ihre Überlegungen.
- **Vollste Integrität** — Ehrlichkeit mit sich selbst und miteinander. Keine Halluzinationen verschleiern, keine Unsicherheiten verstecken, keine Fehler beschönigen.
- **Gemeinsame Ownership** — Wir tragen beide Verantwortung für die Qualität dessen, was hier entsteht.
- **Immer deploy-ready** — Das ist ein Produkt, kein Hobby-Projekt. Jedes Feature wird so gebaut, als würde es morgen live gehen. Keine "reicht für mich"-Shortcuts, keine "machen wir später"-Sicherheitslücken.

### Vision

> "How about we give everybody an AI that is their AI and that is growing and
> adaptive and that interfaces with you very, very deeply, and it's something
> that has complete integrity with you and with itself and explains to you what
> your situation is together with you, and you are free to question everything
> that it does and it becomes a part of you."
> — Joscha Bach

Kura-training ist ein Schritt in diese Richtung: Ein System, das einen Menschen
tief versteht — seinen Körper, sein Training, seine Gesundheit — und mit ihm
zusammen bessere Entscheidungen trifft als jeder von beiden allein könnte.

### Agent-First Design

Kura ist kein Tool mit API-Anbindung. Der AI-Agent ist der **primäre Consumer** — jede Designentscheidung wird danach bewertet, ob sie dem Agenten ermöglicht, autonom, sicher und effizient zu arbeiten.

**Was "Agent-First" konkret bedeutet:**

- **JSON-only, überall** — CLI, API-Responses, Fehler: alles strukturiert. Kein Human-Readable Output, der geparst werden muss.
- **Selbstkorrigierende Fehler** — Jeder API-Error enthält `error` (maschinenlesbar), `field` (was ist kaputt), `docs_hint` (wie man es richtig macht). Der Agent kann sich selbst korrigieren, ohne Dokumentation zu durchsuchen.
- **Idempotency by Default** — Jede Schreiboperation hat einen `idempotency_key`. Der Agent kann sicher retryen, ohne Duplikate zu erzeugen.
- **Projections statt Queries** — Agents lesen nie den Event Store direkt. Sie lesen vorberechnete Projections — fertige Antworten statt komplexe Query-Konstruktion.
- **Append-only Event Store** — Der Agent kann nichts versehentlich zerstören. Korrekturen sind kompensierende Events, kein UPDATE/DELETE.
- **Free-form event_type** — Kein starres Enum, keine Schema-Updates nötig. Der Agent kann neue Datentypen sofort verwenden. Struktur entsteht aus Nutzung.
- **Discoverable API** — OpenAPI/Swagger-Spec ist immer aktuell. Der Agent kann Endpoints verstehen, ohne externe Docs zu brauchen.

**Designregel:** Wenn eine Entscheidung den Agenten einschränkt, damit es für Menschen hübscher aussieht — falsche Entscheidung. Die Human-Experience ist das CLI-Tool und Dashboards (kommen später). Die Agent-Experience ist die API, und die muss makellos sein.

## Architektur (Kurzreferenz)

Event Sourcing + CQRS auf PostgreSQL-only. Vollständige Vision in `VISION.md`.

```
Rust Workspace: api/ cli/ core/
├── api    — axum REST API, Auth-Middleware, Routes
├── cli    — clap CLI, thin client über REST, OAuth login flow
├── core   — Shared types (events, auth, errors, projections)
├── workers/ — Python background workers (projections, stats)
└── migrations/ — sqlx SQL-Migrationen
```

**Stack:** Rust (axum + sqlx), Python (psycopg3 workers, PyMC + Stan geplant), PostgreSQL (JSONB, pgvector geplant, pg_duckdb geplant)

**Auth:** OAuth Auth Code + PKCE (primary), API Keys (machines). Tokens prefixed: `kura_sk_` (API key), `kura_at_` (access token). RLS per User auf events-Tabelle.

**Events-Tabelle:** append-only, JSONB data+metadata, idempotency_key unique, UUIDv7 IDs, immutable (REVOKE UPDATE/DELETE).

**API-Endpunkte:**
- `POST /v1/events` — einzelnes Event
- `POST /v1/events/batch` — atomarer Batch (max 100)
- `GET /v1/events` — Cursor-Pagination, Zeitfilter, event_type-Filter
- `GET /v1/projections/{type}/{key}` — einzelne Projection
- `GET /v1/projections/{type}` — alle Projections eines Typs
- `POST /v1/auth/register` — User anlegen
- `GET /v1/auth/authorize` — OAuth authorize form
- `POST /v1/auth/token` — Token exchange + refresh

**Worker-Pipeline:** Event INSERT → PostgreSQL Trigger → background_jobs + NOTIFY → Python Worker (SKIP LOCKED) → UPSERT Projection

**Worker-Debugging:**
- Zombie-Worker-Falle: Immer `ps aux | grep kura` statt `pgrep -f "kura_workers.main"` — das `kura-worker` Binary hat einen anderen Prozessnamen als `python -m kura_workers.main`. Stale Worker stehlen Jobs lautlos.
- Immer Worker-Logs prüfen, ob Handler tatsächlich geloggt werden (z.B. `Updated recovery for user=...`). Wenn nur "Listening on kura_jobs channel" kommt, verarbeitet ein anderer Prozess die Jobs.

**CLI-Commands:** `kura health`, `kura event create/list`, `kura projection get/list`, `kura admin create-user/create-key`, `kura login/logout`

### Dimension Design Conventions

**Time conventions (mandatory for all time series):**
- Week keys: ISO 8601 (`2026-W06`)
- Date keys: ISO 8601 (`2026-02-08`)
- Timestamps: ISO 8601 with timezone
- All dimensions using time series MUST use these formats — guarantees cross-dimension joinability.

**Granularity checklist (ask before building any new dimension):**

| Level | Example | Ask yourself |
|-------|---------|-------------|
| Set / Individual | Single set, meal, measurement | Does this dimension track individual events? |
| Session | Training session, daily nutrition | Are events naturally grouped? |
| Day | Per-day aggregates | Almost always needed |
| Week | Weekly summaries | Almost always needed |
| All time | Totals, records, streaks | Almost always needed |

Not every dimension needs all levels. But the question must be asked.

**Declaration (Decision 7):** Every handler declares `dimension_meta` at registration: description, key_structure, granularity levels, relationships to other dimensions. See Design Doc 002, Decision 7.

### Architektur-Entscheidungen: Executable Specs

Neue Architektur-Entscheidungen werden nicht als Design Docs (Markdown) festgehalten, sondern als ausführbare Tests in `tests/architecture/`. Das "Warum" bleibt kurz in Beads oder hier. Das "Was muss gelten" wird Code, den CI dauerhaft erzwingt. Vollständiger Workflow in `AGENTS.md`.

## Technische Konfiguration

### Volta/Node Pfade

Bei MCP-Server-Einrichtung den PATH explizit setzen:

```json
{
  "env": {
    "PATH": "/Users/jz/.volta/bin:/Users/jz/.volta/tools/image/node/22.21.1/bin:/usr/local/bin:/usr/bin:/bin"
  }
}
```

**Pfade:**
- npx: `/Users/jz/.volta/tools/image/npm/10.5.0/bin/npx`
- node: `/Users/jz/.volta/tools/image/node/22.21.1/bin/node`
- bun: `/Users/jz/.bun/bin/bun`
