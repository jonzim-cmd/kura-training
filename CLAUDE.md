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

## Architektur (Kurzreferenz)

Event Sourcing + CQRS auf PostgreSQL-only. Vollständige Vision in `VISION.md`.

```
Rust Workspace: api/ cli/ core/
├── api    — axum REST API, Auth-Middleware, Routes
├── cli    — clap CLI, thin client über REST, OAuth login flow
├── core   — Shared types (events, auth, errors)
└── migrations/ — sqlx SQL-Migrationen
```

**Stack:** Rust (axum + sqlx), Python (PyMC + Stan, geplant), PostgreSQL (JSONB, pgvector geplant, pg_duckdb geplant)

**Auth:** OAuth Auth Code + PKCE (primary), API Keys (machines). Tokens prefixed: `kura_sk_` (API key), `kura_at_` (access token). RLS per User auf events-Tabelle.

**Events-Tabelle:** append-only, JSONB data+metadata, idempotency_key unique, UUIDv7 IDs, immutable (REVOKE UPDATE/DELETE).

**API-Endpunkte:**
- `POST /v1/events` — einzelnes Event
- `POST /v1/events/batch` — atomarer Batch (max 100)
- `GET /v1/events` — Cursor-Pagination, Zeitfilter, event_type-Filter
- `POST /v1/auth/register` — User anlegen
- `GET /v1/auth/authorize` — OAuth authorize form
- `POST /v1/auth/token` — Token exchange + refresh

**CLI-Commands:** `kura health`, `kura event create/list`, `kura admin create-user/create-key`, `kura login/logout`

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
