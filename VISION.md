# Training Data Platform

## Kernidee

Die Agent-Welt spaltet sich in zwei Hälften: **Intelligence** (was der Agent mitbringt) und **Truth** (was er braucht). Dieses Produkt baut die Truth-Seite für Training.

Ein Cloud-Backend + CLI für Trainingsdaten, gebaut für AI Agents — nicht für Menschen. Der Agent ist das Frontend, der Coach, der Analyst. Das Backend liefert was der Agent selbst nicht kann: persistenter Zustand, vorberechnete Statistik, strukturierte Daten über Monate und Jahre.

## Warum ein Backend, nicht nur ein Agent

- **Source of Truth.** Agent-Memory ist fuzzy. Eine Datenbank ist exakt. "Waren es 80kg oder 85kg?" braucht eine Quelle der Wahrheit, keine Erinnerung.
- **Compute at Scale.** 10.000 Sessions bei jeder Frage neu durchrechnen ist Verschwendung. Materialized Views, Aggregationen, Indices — das bleibt Backend-Arbeit.
- **Vorberechnete Statistik.** Agents haben limitierte Kontextfenster. Trends über 2 Jahre, Korrelationsanalysen, statistische Tests — das muss im Hintergrund laufen und abrufbereit sein.
- **Multi-Agent-Zugriff.** Dein Agent, der deines Coaches, dein Physio, dein Arzt — alle brauchen eine gemeinsame Datenbasis.
- **Integrationen.** Garmin, Apple Health, Strava, Whoop, Oura — das sind Daten-Pipelines, kein Agent-Feature.
- **Audit & Nachweis.** "Beweise, dass ich vor der Verletzung 120kg gehoben hab." Unveränderliches Log, nicht Agent-Erinnerung.

## Wie es funktioniert

```
User (Handy, Laptop, Sprache, Chat — egal)
    → Agent (beliebig: Claude, GPT, Clawdbot, Custom Bot, ...)
        → CLI (auf Server des Agents installiert, 24/7 online)
            → Cloud Backend (REST API, PostgreSQL, Statistik-Engine)
```

Der User spricht mit seinem Agent — per Telegram, WhatsApp, Claude App, Slack, egal. Der Agent läuft auf einem Server und bedient das CLI. Das CLI ist das primäre Interface für Agents: self-documenting (`--help`), strukturiert, auth-managed. Unter der Haube ruft das CLI die REST API des Backends auf.

Der User sieht das CLI nie. Er redet mit seinem Agent. Der Agent redet mit dem CLI. Das CLI redet mit dem Backend.

### Zugangsebenen

| Priorität | Interface | Für wen |
|---|---|---|
| 1 | **CLI** | Jeder Agent auf jedem Server. Primary Interface. Self-documenting, auth-managed, structured I/O. |
| 2 | **REST API** | Transport-Layer unter dem CLI. Auch direkt nutzbar für Agents die HTTP bevorzugen. |
| 3 | **OpenAPI Spec** | Agent-Frameworks (LangChain, Custom GPTs) die automatisch Clients generieren. |
| 4 | **MCP Server** | Claude-native Agents. Wächst mit dem Ökosystem. |

Das CLI ist die Developer Experience für Agents — wie ein Mensch einen Hammer nimmt, statt die Physik des Nagelns zu berechnen.

## Was das Backend tut, was der Agent nicht kann

| Fähigkeit | Warum nicht der Agent? |
|---|---|
| Persistenter Zustand über Jahre | Agents starten jede Session neu |
| Statistische Tests (Signifikanz, Effektstärke, CI) | Braucht Compute, nicht Sprachmodell |
| Trend-Erkennung über Monate | Passt nicht ins Kontextfenster |
| Anomaly Detection | Braucht historische Baselines |
| Korrelationsanalysen (Schlaf↔Performance) | Braucht vollständige Zeitreihen |
| Cross-User Benchmarks | Braucht Daten anderer User (nur bei SaaS) |
| Daten-Import/Normalisierung | Pipeline-Arbeit, kein LLM-Task |

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
- Self-Hosting wird trivial: ein Docker-Container
- pg_duckdb für Statistical Engine: Regression über 2 Jahre direkt in der DB
- pgvector für Semantic Layer: Exercise-Embeddings direkt querybar

### Architektur-Übersicht

```
CLI (Open Source, BSL)
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

### Read Path (Query Side)

Agents lesen nie aus dem Event Store. Sie lesen aus vorberechneten Projections.

Projections werden bei jedem neuen Event aktualisiert:
- **Per-User:** Exercise Progression, Volume Tracking, PRs, Fatigue, Metric Trends
- **Statistische:** Signifikanztests, Effektstärken, Korrelationen, Anomalien, Regressionen
- **Aggregate:** Cross-User Benchmarks, Population Norms (anonymisiert, opt-in)

### Semantic Layer

Zwischen Client und Command/Query Side. Powered by pgvector.

- **Exercise Resolution:** "bench", "Bankdrücken", "Bench Press" → gleiche Übung (Embedding-Similarity)
- **Zeitliche Auflösung:** "letzte Woche" → konkreter Datumsbereich
- **Semantische Auflösung:** "Oberkörper" → [push, pull] Muskelgruppen
- **Keine NL-Interpretation.** Das macht der Agent. Das Backend resolved Begriffe, nicht Sätze.

### Type Inference

Nichts hardcoded. Typen emergieren aus Nutzung, nicht aus Schema.

- User loggt weight + reps für "Squat" → System inferiert: Kraft-Übung
- User loggt distance + time für "10k" → System inferiert: Ausdauer-Übung
- User loggt nur duration für "Yoga" → System inferiert: Time-only

Erst wenn genug Daten da sind, bietet das System passende Projections an. Kein Zwang zur Kategorisierung.

Zusätzlich: User/Agent kann jederzeit explizit taggen (Muskelgruppen, Bewegungsmuster, Equipment). Community-Taxonomy als optionaler Default für bekannte Übungen.

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

### CLI (Primary Agent Interface)

Open Source (BSL-Lizenz). Treibt Adoption. Ist das primäre Interface für Agents — self-documenting, auth-managed, structured JSON output. Ohne Backend-Account wertlos.

Läuft auf dem Server des Agents, nicht auf dem Gerät des Users. Phone-only User merken nichts vom CLI — sie reden mit ihrem Agent, der Agent bedient das CLI.

### Self-Hosted (Phase 2)

Für Privacy-bewusste User, Gyms, Coaching-Businesses. Premium-Preis. Kein Zugriff auf Cross-User Benchmarks (Daten sind isoliert).

Architektur ist von Tag 1 self-hosting-fähig: PostgreSQL-Only, ein Docker-Container, keine Cloud-spezifischen Abhängigkeiten.

## Entschiedenes

- **Scope:** Kraft + Ausdauer von Anfang an
- **Architektur:** Event Sourcing + CQRS
- **Datenbank:** PostgreSQL-Only mit pg_duckdb, pgvector, JSONB. Kein zweites System.
- **Datenmodell:** Events statt starkes Schema. Type Inference statt vordefinierte Typen. Nichts hardcoded.
- **Business Model:** SaaS primary (Phase 1), Self-Hosted als spätere Option (Phase 2). CLI Open Source (BSL).
- **Primary Interface:** CLI auf Agent-Server. REST API als Transport darunter. User interagiert nie direkt mit CLI.
- **Response-Design:** Context-Enriched. Jede Antwort enthält data + context + meta.
- **Temporal Queries:** First-Class. Zeitreisen, Vergleiche, Hypotheticals nativ.
- **Corrections:** Compensating Events, nie Mutation. Idempotency-Keys auf jedem Event.

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

- CLI-Command: `td`, `trn`, `lift`, anderes?
- Produktname: offen
