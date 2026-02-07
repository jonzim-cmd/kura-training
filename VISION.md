# Kura Training

## Kernidee

Die Agent-Welt spaltet sich in zwei Hälften: **Intelligence** (was der Agent mitbringt) und **Truth** (was er braucht). Dieses Produkt baut die Truth-Seite für Training.

Ein Cloud-Backend + CLI für Trainingsdaten, gebaut für AI Agents — nicht für Menschen. Der Agent ist das Frontend, der Coach, der Analyst. Das Backend liefert was der Agent selbst nicht kann: persistenter Zustand, vorberechnete Statistik, strukturierte Daten über Monate und Jahre.

### Compute, not Storage

Trainingsdaten sind klein. Ein paar Jahre History passen in wenige Megabyte. Jeder Agent mit API-Zugang hat de facto eine vollständige Kopie — und das ist kein Bug, sondern Feature. "Wir speichern deine Daten" ist kein Moat. Agents klonen strukturierte, kleine Datensätze in Sekunden.

Der Moat ist, was wir *aus* den Daten machen: statistische Analysen die kein Agent im Kontextfenster berechnen kann, Projections die über Jahre vorberechnet werden, Cross-User Benchmarks die nur zentral möglich sind, Multi-Agent Governance die Shared State konsistent hält. Kura ist keine Datenbank mit API — es ist eine Compute- und Governance-Engine, die Trainingsdaten als Input nimmt.

## Warum ein Backend, nicht nur ein Agent

- **Compute at Scale.** 10.000 Sessions bei jeder Frage neu durchrechnen ist Verschwendung. Materialized Views, Aggregationen, Indices — das bleibt Backend-Arbeit. Ein Agent kann Daten kopieren, aber keine Regressionsanalyse über 2 Jahre in seinem Kontextfenster laufen lassen.
- **Vorberechnete Statistik.** Trends über 2 Jahre, Korrelationsanalysen, statistische Tests — das muss im Hintergrund laufen und abrufbereit sein. Das ist der Kern dessen, was ein Agent nicht replizieren kann, egal ob er die Rohdaten hat.
- **Multi-Agent Governance.** Dein Agent, der deines Coaches, dein Physio, dein Arzt — alle brauchen nicht nur eine gemeinsame Datenbasis, sondern konsistenten Shared State mit Berechtigungen. Wer darf was sehen, wer darf schreiben, wer hat wann was geändert. Multi-User Sync und Permissioning sind harte Probleme, die ein einzelner Agent nicht löst.
- **Integrationen.** Garmin, Apple Health, Strava, Whoop, Oura — das sind Daten-Pipelines, kein Agent-Feature.
- **Audit & Nachweis.** "Beweise, dass ich vor der Verletzung 120kg gehoben hab." Unveränderliches Log mit kryptographischer Integrität, nicht Agent-Erinnerung. Governance und Compliance brauchen eine autoritäre Quelle — nicht weil die Daten sonst nirgends wären, sondern weil Nachweisbarkeit Vertrauen in die Quelle erfordert.
- **Authoritative Write Coordination.** Auch wenn jeder Agent eine Lese-Kopie hat: Schreiben muss durch einen zentralen Punkt. Sonst gibt es Konflikte, Race Conditions, und keine konsistente History. Kura ist der Single Writer, aus dem alle Agents ihre Wahrheit ableiten.

## Wie es funktioniert

```
User (Handy, Laptop, Sprache, Chat — egal)
    → Agent (beliebig: Claude, GPT, Clawdbot, Custom Bot, ...)
        → Kura CLI / MCP / REST API
            → Cloud Backend (PostgreSQL, Statistik-Engine)
```

Der User sieht die Technik nie. Er redet mit seinem Agent. Der Agent redet mit Kura. Kura liefert die Wahrheit.

### Was wir bauen: Zwei Schichten, nicht drei Projekte

```
REST API          ← baust du einmal, perfekt. Alles redet mit ihr.
    ↑
CLI               ← thin client, ruft REST API auf
    ↑
MCP Mode          ← eingebaut im CLI, gleicher Code
```

Die REST API ist die Wahrheit. Das CLI ist ein Binary das zwei Dinge kann: Shell-Commands UND MCP-Server-Modus. Beides ruft die gleiche API auf. Für Cloud-Agents (Claude.ai, ChatGPT App) gibt es einen gehosteten MCP Server — ein dünner Wrapper um die REST API.

### Welcher Agent nutzt was

| Agent / Umgebung | Zugang | Wie |
|---|---|---|
| Claude Code (lokal, Shell) | **CLI direkt** | Auf dem Rechner installiert |
| Cursor / Codex / Aider (lokal, Shell) | **CLI direkt** | Auf dem Rechner installiert |
| Agent auf VPS (Server, Shell) | **CLI direkt** | Auf dem Server installiert, 24/7 |
| Claude Desktop (lokal, MCP) | **CLI im MCP-Modus** | CLI als lokaler MCP Server |
| Claude.ai / Claude App (Cloud, MCP) | **Gehosteter MCP Server** | Kura hostet MCP-Endpoint |
| ChatGPT / ChatGPT App (Cloud) | **REST API** | Via Function Calling / Actions, gehosteter MCP wenn verfügbar |
| Telegram / WhatsApp | **Optional** | Für User ohne AI-Agent die per Chat tracken wollen. Nicht der Hauptweg. |

### Entscheidungslogik

```
Hat Shell-Zugriff?  → CLI
Hat MCP?            → CLI im MCP-Modus (lokal) oder gehosteter MCP (Cloud)
Hat beides nicht?   → REST API direkt
Telegram/WhatsApp?  → Optionaler Kanal, nicht der Hauptweg
```

### Zugangsebenen (Priorität)

| Priorität | Interface | Rolle |
|---|---|---|
| 1 | **REST API** | Fundament. Alles redet mit ihr. Einmal perfekt bauen. |
| 2 | **CLI** | Primary Agent Interface. Thin Client über die API. Self-documenting, auth-managed, structured JSON. Source-available (BSL). |
| 3 | **MCP (im CLI)** | Eingebaut im CLI-Binary. Gleicher Code, MCP-Protokoll. Für lokale MCP-Agents. |
| 4 | **Gehosteter MCP** | Dünner Wrapper um REST API. Für Cloud-Agents (Claude.ai, ChatGPT App). |
| 5 | **OpenAPI Spec** | Automatische Client-Generierung für Agent-Frameworks. |

## Was das Backend tut, was der Agent nicht kann — auch wenn er die Daten hat

Ein Agent mit API-Zugang hat eine Kopie der Rohdaten. Trotzdem kann er folgendes nicht:

| Fähigkeit | Warum nicht der Agent, selbst mit Datenkopie? |
|---|---|
| Statistische Tests (Signifikanz, Effektstärke, CI) | Braucht Compute, nicht Sprachmodell. Agents halluzinieren Statistik. |
| Trend-Erkennung über Monate | 10.000 Events passen nicht ins Kontextfenster. Vorberechnung ist zwingend. |
| Anomaly Detection | Braucht historische Baselines, die kontinuierlich aktualisiert werden |
| Korrelationsanalysen (Schlaf↔Performance) | Braucht vollständige Zeitreihen und echte statistische Methoden |
| Cross-User Benchmarks | Braucht Daten anderer User — ein Agent hat nur seine eigenen |
| Multi-Agent Write Coordination | Mehrere Agents gleichzeitig schreiben → Konflikte. Braucht zentralen Koordinator. |
| Governance & Permissions | Welcher Agent darf was sehen/schreiben? Braucht zentrale Autorität. |
| Daten-Import/Normalisierung | Pipeline-Arbeit, kein LLM-Task |
| Audit & Nachweisbarkeit | Agent-Kopie ist nicht beweiskräftig. Immutable Log mit Provenance schon. |

## Architektur: Event Sourcing + CQRS auf PostgreSQL

### Grundprinzip

Trainingsdaten sind natürliche Events. Du "updatest" keinen Satz den du gemacht hast — er ist passiert. Jeder Satz, jeder Lauf, jede Messung ist ein Event zu einem Zeitpunkt.

Event Sourcing gibt uns:
- **Flexibilität beim Schreiben:** Neue Event-Typen ohne Migration. Custom Metrics ohne Schema-Change.
- **Struktur beim Lesen:** Typisierte Projections, vorberechnet, sofort querybar.
- **Immutability:** Audit-Trail eingebaut. Unveränderbar. Beweiskräftig.
- **Schema-Evolution:** Neuer Event-Typ? Hinzufügen. Neue Projection? Bauen und über alle Events replaying.
- **Temporal Queries:** Zeitreisen, Vergleiche, Hypotheticals — nativ möglich.

### PostgreSQL als einzige Datenbank

Kein zweites System. Kein Redis. Kein Kafka. Kein separater Event Store.

PostgreSQL mit drei Extensions vereint alles in einer DB:

| Extension | Funktion | Ersetzt |
|---|---|---|
| **pg_duckdb** | Columnar Analytics. Statistische Queries über Millionen Events in Sekunden. | Separate Analytics-DB (ClickHouse, DuckDB) |
| **pgvector** | Semantische Suche. Embeddings für Exercise-Resolution, Alias-Matching. | Separater Fuzzy-Matching-Service |
| **JSONB** | Flexible Event-Daten und Projection-Daten ohne Schema-Migration. | Document Store (MongoDB) |

Zusätzlich: `LISTEN/NOTIFY` für Event-Subscriptions, Partitioning für Event-Tabellen, Indices auf (user_id, timestamp).

Vorteile einer einzigen DB:
- Ein Backup, ein Monitoring, ein Scaling-Pfad
- Transaktionale Konsistenz: Event schreiben + Projection updaten in einer Transaktion
- Self-Hosting wird einfach: ein Docker-Container plus Postgres-Setup
- pg_duckdb für Statistical Engine: Regression über 2 Jahre direkt in der DB
- pgvector für Semantic Layer: Exercise-Embeddings direkt querybar

Extensions sind optional. Wenn pg_duckdb/pgvector nicht verfügbar sind (z.B. bei Managed Postgres), läuft ein Minimal-Mode: Core-Features bleiben, Analytics/Semantik wandern in Background Workers oder nutzen Ersatz-Queries.

### Architektur-Übersicht

```
CLI (Shell + MCP-Modus)    Gehosteter MCP    REST API direkt
        │                       │                  │
        └───────────┬───────────┘                  │
                    │                               │
                    ▼                               │
              REST API ◄───────────────────────────┘
                    │
                    ▼
┌─────────────────────────────────────────────────────┐
│                  CLOUD BACKEND (SaaS)                │
│                                                      │
│   ┌──────────────┐                                   │
│   │ Semantic Layer│  pgvector: Exercise Resolution    │
│   │               │  Alias → Embedding → Match        │
│   └──────┬───────┘                                   │
│          │                                            │
│   ┌──────┴───────┐    ┌─────────────────────┐        │
│   │ Command Side  │    │    Query Side        │       │
│   │               │    │                      │       │
│   │ Validate      │    │ Read Projections     │       │
│   │ Append Event  │    │ Enrich with Context  │       │
│   │ Notify        │    │ Return data+ctx+meta │       │
│   └──────┬───────┘    └──────────▲───────────┘       │
│          │                       │                    │
│          ▼                       │                    │
│   ┌──────────────────────────────┴──────────┐        │
│   │              PostgreSQL                  │        │
│   │                                          │        │
│   │  events (append-only, JSONB, partitioned)│        │
│   │  projections (per-user, JSONB)           │        │
│   │  statistics (pre-computed, JSONB)        │        │
│   │  benchmarks (aggregate, anonymized)      │        │
│   │                                          │        │
│   │  + pg_duckdb  → Analytical Queries       │        │
│   │  + pgvector   → Semantic Resolution      │        │
│   │  + LISTEN/NOTIFY → Event Subscriptions   │        │
│   └──────────────┬──────────────────────────┘        │
│                  │                                    │
│   ┌──────────────▼──────────────────────────┐        │
│   │       Background Workers                 │        │
│   │                                          │        │
│   │  Projection Engine: Updates on new Event │        │
│   │  Statistical Engine: Regression, CI,     │        │
│   │    Effect Sizes, Correlations, Anomalies │        │
│   │  Benchmark Engine: Cross-User Aggregation│        │
│   └─────────────────────────────────────────┘        │
└──────────────────────────────────────────────────────┘
```

### Write Path (Command Side)

Events werden validiert und append-only geschrieben. Jedes Event hat:

```
id, user_id, timestamp, event_type, data (JSONB), metadata (JSONB)
```

- Metadata enthält: source (cli/api/import), agent (claude/gpt/manual), device, session_id
- Corrections und Undos sind Compensating Events — das Original wird nie verändert
- Jedes Event hat einen Client-generierten Idempotency-Key gegen Duplikate

Löschung/Rectification: personenbezogene Daten werden separat gehalten oder redigierbar gemacht, damit "Delete" möglich ist ohne den Event-Stream zu zerstören. Wie genau, ist eine bewusste Design-Entscheidung (Privacy vs. Audit).

### Read Path (Query Side)

Agents lesen nie aus dem Event Store. Sie lesen aus vorberechneten Projections.

Projections werden bei jedem neuen Event aktualisiert:
- **Per-User:** Exercise Progression, Volume Tracking, PRs, Fatigue, Metric Trends
- **Statistische:** Signifikanztests, Effektstärken, Korrelationen, Anomalien, Regressionen
- **Aggregate:** Cross-User Benchmarks, Population Norms (anonymisiert, opt-in)

### Semantic Layer

Zwischen Client und Command/Query Side. Powered by pgvector.

- **Exercise Resolution:** "bench", "Bankdrücken", "Bench Press" → gleiche Übung (Embedding-Similarity)
- **Zeitliche Auflösung:** Agent liefert den Datumsbereich, Backend normalisiert/validiert ihn
- **Semantische Auflösung:** "Oberkörper" → [push, pull] Muskelgruppen
- **Keine NL-Interpretation.** Das macht der Agent. Das Backend resolved Begriffe, nicht Sätze.

### Type Inference

Nichts hardcoded. Typen emergieren aus Nutzung, nicht aus Schema.

- User loggt weight + reps für "Squat" → System inferiert: Kraft-Übung
- User loggt distance + time für "10k" → System inferiert: Ausdauer-Übung
- User loggt nur duration für "Yoga" → System inferiert: Time-only

Erst wenn genug Daten da sind, bietet das System passende Projections an. Kein Zwang zur Kategorisierung.

Zusätzlich: User/Agent kann jederzeit explizit taggen (Muskelgruppen, Bewegungsmuster, Equipment). Community-Taxonomy als optionaler Default für bekannte Übungen.

Inference ist versioniert und stabilisiert: Sobald ein Typ "gefriert", bleibt er konsistent, kann aber bewusst vom User/Agent überschrieben werden. Änderungen triggern Rebuilds der Projections.

### Context-Enriched Responses

Jede Response enthält nicht nur die angefragten Daten, sondern automatisch relevanten Kontext:

```json
{
  "data": { ... },
  "context": {
    "anomalies": ["performance_declining_2w"],
    "correlations": {"sleep_quality": {"impact": "negative", "r": -0.64}},
    "fatigue_index": 7.2,
    "current_program": "5/3/1 Week 3"
  },
  "meta": {
    "computed_at": "2026-02-07T18:00:00Z",
    "projection_version": 42,
    "cache_hit": true,
    "statistical_quality": { "sample_size": "sufficient", "confidence_level": 0.95 }
  }
}
```

Der Agent braucht einen Call, nicht fünf.

### Agent-First Interface (Requirement)

Agents brauchen deterministische, strukturierte Antworten statt "wahrer Sätze":
- Stabile IDs, klare Enums, versionierte Schemas
- Konsistente Fehlercodes und Idempotency-Keys
- Pagination, Filter, Zeitfenster, inkrementelle Updates
- Kostenhinweise (query_cost, cache_hit) und Daten-Linie (computed_at, projection_version)
- Berechtigungen/Scopes maschinenlesbar (was darf dieser Agent sehen/schreiben)

### Temporal Queries als First-Class

```
Zeitreise:     "Wie sah mein Training im Juni 2025 aus?"    → Event Replay bis Datum
Vergleich:     "Januar vs. Februar"                          → Diff zweier Zeiträume
Hypothetical:  "Was wenn ich nächste Woche 130kg schaffe?"   → Temporäre Projection, berechnen, verwerfen
```

### Event-Typen (nicht abschließend, wächst durch Usage)

**Training:**
- `session.started`, `session.ended`
- `set.logged` (weight, reps, rpe, set_type, etc.)
- `activity.logged` (duration, distance, hr, splits, etc.)
- `superset.started`, `superset.ended`

**Exercise Management:**
- `exercise.created`, `exercise.updated`, `exercise.archived`

**Metrics & Health:**
- `metric.logged` (beliebiger Typ: bodyweight, sleep, stress, custom)
- `health.imported` (Apple Health, Garmin, Whoop, Oura)

**Context:**
- `note.added`, `injury.reported`, `injury.resolved`
- `program.started`, `program.phase_changed`

**Corrections:**
- `event.corrected` (referenziert Original, liefert neue Daten)
- `event.voided` (referenziert Original, markiert als ungültig)

## Statistik-Philosophie

Statistische Rigorosität ist Kernmerkmal, nicht Marketing-Feature.

- Wenn Voraussetzungen für einen Test nicht erfüllt sind: kein Test. Transparente Kommunikation der Limitation.
- Unsicherheit wird immer mitgeliefert (Konfidenzintervalle, nicht Punktschätzungen).
- Effektstärken statt nur p-Werte.
- Der Agent übersetzt die Statistik in verständliche Sprache. Das Backend liefert die rohen Zahlen mit Kontext-Metadaten.
- Statistische Qualitäts-Metadaten bei jeder Response (sample_size, confidence_level, test_assumptions_met).

## Business Model

### SaaS (Primary)

Das Produkt ist ein gehosteter Cloud-Service. User registrieren sich, verbinden ihren Agent via CLI, zahlen monatlich.

- **Free:** Logging, Basis-Abfragen, Export
- **Pro:** Vorberechnete Analytics, Korrelationen, Benchmarks, Integrationen, unbegrenzte History

SaaS ist das Primärmodell weil:
- Cross-User Benchmarks nur zentral funktionieren
- Statistische Engine von zentralem Compute profitiert
- Zielkunde (Person mit Agent) will kein Backend deployen

### CLI + MCP (Agent Interface)

Source-available (BSL-Lizenz). Ein Binary, zwei Modi:
1. **Shell-Modus:** Klassische CLI-Commands. Für Agents mit Shell-Zugriff.
2. **MCP-Modus:** Gleicher Code, MCP-Protokoll. Für lokale MCP-Agents (Claude Desktop etc.).

Zusätzlich: **Gehosteter MCP Server** für Cloud-Agents (Claude.ai, ChatGPT App). Dünner Wrapper um die REST API.

Alle drei rufen die gleiche REST API auf. Kein separater Code, kein Maintenance-Overhead.

### Self-Hosted (Phase 2)

Für Privacy-bewusste User, Gyms, Coaching-Businesses. Premium-Preis. Kein Zugriff auf Cross-User Benchmarks (Daten sind isoliert).

Architektur ist von Tag 1 self-hosting-fähig: PostgreSQL-Only, ein Docker-Container, keine Cloud-spezifischen Abhängigkeiten.

## Entschiedenes

- **Scope:** Kraft + Ausdauer von Anfang an
- **Architektur:** Event Sourcing + CQRS
- **Datenbank:** PostgreSQL-Only mit pg_duckdb, pgvector, JSONB. Kein zweites System.
- **Datenmodell:** Events statt starkes Schema. Type Inference statt vordefinierte Typen. Nichts hardcoded.
- **Business Model:** SaaS primary (Phase 1), Self-Hosted als spätere Option (Phase 2). CLI Source-available (BSL).
- **Interface-Stack:** REST API als Fundament → CLI als Thin Client → MCP eingebaut im CLI. Gehosteter MCP für Cloud-Agents. Alles ein Codebase, kein Maintenance-Overhead.
- **Produktname:** Kura Training
- **Response-Design:** Context-Enriched. Jede Antwort enthält data + context + meta.
- **Temporal Queries:** First-Class. Zeitreisen, Vergleiche, Hypotheticals nativ.
- **Corrections:** Compensating Events, nie Mutation. Idempotency-Keys auf jedem Event.
- **Defensibility:** Compute, not Storage. Daten sind klein und kopierbar. Der Moat liegt in vorberechneter Statistik, Cross-User Benchmarks, Multi-Agent Governance, und Write Coordination — nicht in der Datenhaltung selbst.

## Offene Entscheidungen

### Tech Stack (außer DB)

- **API + Projection Engine:** Python vs. Go vs. Rust
- **Statistical Engine:** Python (scipy, statsmodels) — wahrscheinlich gesetzt
- **Background Workers:** Architektur der Job-Queue
- **Hosting:** AWS/GCP (managed PostgreSQL) vs. Hetzner/Fly.io

### CLI-Design

- Command-Struktur und Granularität
- JSON-Output als Default (agent-optimiert) vs. Human-Readable mit `--json` Flag
- Offline-Queue wenn Backend nicht erreichbar

### Auth & Multi-Agent

- API Keys vs. OAuth
- Scoped Permissions für Coach/Physio/Arzt
- Daten-Sharing: was wird geteilt, was nicht

### Monetarisierung: Grenze Free/Pro

- Wo genau liegt die Grenze?
- Free = Logging + Basic Stats, Pro = Advanced Analytics + Integrationen?
- Free = Limitierte History, Pro = Unbegrenzt?
- Free = Einzelnutzer, Pro = Multi-Agent/Sharing?

### Benchmarks

- Start mit populationsbasierten Normen aus der Sportwissenschaft
- Echte Cross-User Benchmarks wenn Datenbasis existiert
- Privacy: Anonymisierung, Opt-in

### Naming

- CLI-Command: `kura`, `kt`, anderes?
