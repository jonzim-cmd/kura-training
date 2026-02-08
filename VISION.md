# Kura Training

## Kernidee

Die Agent-Welt spaltet sich in zwei Hälften: **Intelligence** (was der Agent mitbringt) und **Truth** (was er braucht). Dieses Produkt baut die Truth-Seite für Training, Ernährung und Gesundheit.

Ein Cloud-Backend + CLI für Trainings-, Ernährungs- und Gesundheitsdaten, gebaut für AI Agents — nicht für Menschen. Der Agent ist das Frontend, der Coach, der Analyst, der Ernährungsberater. Das Backend liefert was der Agent selbst nicht kann: persistenter Zustand, vorberechnete Statistik, strukturierte Daten über Monate und Jahre.

### Compute, not Storage

Trainings- und Ernährungsdaten sind klein. Ein paar Jahre History passen in wenige Megabyte. Jeder Agent mit API-Zugang hat de facto eine vollständige Kopie — und das ist kein Bug, sondern Feature. "Wir speichern deine Daten" ist kein Moat. Agents klonen strukturierte, kleine Datensätze in Sekunden.

Der Moat ist, was wir *aus* den Daten machen: statistische Analysen die kein Agent im Kontextfenster berechnen kann, Projections die über Jahre vorberechnet werden, Cross-User Benchmarks die nur zentral möglich sind, Multi-Agent Governance die Shared State konsistent hält. Kura ist keine Datenbank mit API — es ist eine Compute- und Governance-Engine, die Trainings-, Ernährungs- und Gesundheitsdaten als Input nimmt.

## Warum ein Backend, nicht nur ein Agent

- **Compute at Scale.** 10.000 Sessions bei jeder Frage neu durchrechnen ist Verschwendung. Materialized Views, Aggregationen, Indices — das bleibt Backend-Arbeit. Ein Agent kann Daten kopieren, aber keine Bayesian Regression über 2 Jahre in seinem Kontextfenster laufen lassen.
- **Vorberechnete Statistik.** Trends über 2 Jahre, Korrelationsanalysen, Bayesian Posteriors, MCMC-Chains — das muss im Hintergrund laufen und abrufbereit sein. Das ist der Kern dessen, was ein Agent nicht replizieren kann, egal ob er die Rohdaten hat.
- **Multi-Agent Governance.** Dein Agent, der deines Coaches, dein Physio, dein Arzt — alle brauchen nicht nur eine gemeinsame Datenbasis, sondern konsistenten Shared State mit Berechtigungen. Wer darf was sehen, wer darf schreiben, wer hat wann was geändert. Multi-User Sync und Permissioning sind harte Probleme, die ein einzelner Agent nicht löst.
- **Integrationen.** Garmin, Apple Health, Strava, Whoop, Oura, MyFitnessPal, Cronometer, Lebensmittel-DBs — das sind Daten-Pipelines, kein Agent-Feature.
- **Audit & Nachweis.** "Beweise, dass ich vor der Verletzung 120kg gehoben hab." Unveränderliches Log mit kryptographischer Integrität, nicht Agent-Erinnerung. Governance und Compliance brauchen eine autoritäre Quelle — nicht weil die Daten sonst nirgends wären, sondern weil Nachweisbarkeit Vertrauen in die Quelle erfordert.
- **Authoritative Write Coordination.** Auch wenn jeder Agent eine Lese-Kopie hat: Schreiben muss durch einen zentralen Punkt. Sonst gibt es Konflikte, Race Conditions, und keine konsistente History. Kura ist der Single Writer, aus dem alle Agents ihre Wahrheit ableiten.

## Wie es funktioniert

```
User (Handy, Laptop, Sprache, Chat — egal)
    → Agent (beliebig: Claude, GPT, Clawdbot, Custom Bot, Agent-Schwarm)
        → Kura CLI / MCP / REST API
            → Cloud Backend (PostgreSQL, Bayesian Compute Engine)
```

Der User sieht die Technik nie. Er redet mit seinem Agent. Der Agent redet mit Kura. Kura liefert die Wahrheit. Ob der Agent intern einen Schwarm orchestriert oder alleine arbeitet, ist seine Entscheidung — Kura sieht nur authentifizierte Requests.

### Was wir bauen: Drei unabhängige Interfaces

```
REST API          ← Fundament. Alles redet mit ihr.
CLI               ← Eigenes Binary. Thin Client über REST API. Shell-optimiert.
MCP Server        ← Eigenes Projekt. Thin Client über REST API. MCP-optimiert.
```

Die REST API ist die Wahrheit. CLI und MCP Server sind **separate Projekte** — beide dünn, beide rufen die gleiche API auf, aber jedes spielt seine eigenen Stärken aus.

**Warum getrennt, nicht ein Binary?** CLI und MCP haben fundamental unterschiedliche Stärken. Das CLI ist ephemeral (start, execute, exit), composable (Pipes, Scripts), minimal (ein Binary, keine Runtime). Der MCP Server ist long-lived (persistente Verbindung), bietet Tool Discovery (Agent sieht automatisch verfügbare Tools + Schemas), und hat native Integration mit MCP-Clients. Diese Stärken verwässern sich wenn man beides in ein Binary presst. Der "shared Code" ist minimal — beide machen HTTP-Requests, das sind ein paar Zeilen.

### Welcher Agent nutzt was

| Agent / Umgebung | Zugang | Wie |
|---|---|---|
| Claude Code (lokal, Shell) | **CLI direkt** | Auf dem Rechner installiert |
| Cursor / Codex / Aider (lokal, Shell) | **CLI direkt** | Auf dem Rechner installiert |
| Agent auf VPS (Server, Shell) | **CLI direkt** | Auf dem Server installiert, 24/7 |
| Claude Desktop (lokal, MCP) | **MCP Server (lokal)** | Eigenes Projekt, lokale Installation |
| Claude.ai / Claude App (Cloud, MCP) | **MCP Server (gehostet)** | Kura hostet MCP-Endpoint |
| ChatGPT / ChatGPT App (Cloud) | **REST API** | Via Function Calling / Actions, gehosteter MCP wenn verfügbar |
| Agent-Schwarm (beliebig) | **REST API** | Orchestrierender Agent verteilt Arbeit, alle Sub-Agents nutzen gleichen Token |

### Entscheidungslogik

```
Hat Shell-Zugriff?  → CLI
Hat MCP?            → MCP Server (lokal oder gehostet)
Hat beides nicht?   → REST API direkt
```

### Zugangsebenen (Priorität)

| Priorität | Interface | Rolle |
|---|---|---|
| 1 | **REST API** | Fundament. Alles redet mit ihr. Einmal perfekt bauen. |
| 2 | **CLI** | Shell-optimierter Agent-Client. Ephemeral, composable, JSON-only. Eigenes Binary. |
| 3 | **MCP Server** | Eigenes Projekt. Tool Discovery, typisierte Parameter, persistente Verbindung. Lokal oder gehostet. |
| 4 | **OpenAPI Spec** | Automatische Client-Generierung für Agent-Frameworks. |

## Was das Backend tut, was der Agent nicht kann — auch wenn er die Daten hat

Ein Agent mit API-Zugang hat eine Kopie der Rohdaten. Trotzdem kann er folgendes nicht:

| Fähigkeit | Warum nicht der Agent, selbst mit Datenkopie? |
|---|---|
| Bayesian Posteriors (MCMC, Credible Intervals, Predictive Distributions) | Braucht Compute, nicht Sprachmodell. Agents können kein MCMC laufen lassen. |
| Statistische Tests (Signifikanz, Effektstärke, CI) | Agents halluzinieren Statistik. Echte Berechnung ist kein LLM-Task. |
| Trend-Erkennung über Monate | 10.000 Events passen nicht ins Kontextfenster. Vorberechnung ist zwingend. |
| Anomaly Detection | Braucht historische Baselines (Bayesian Changepoint Detection), die kontinuierlich aktualisiert werden |
| Korrelationsanalysen (Schlaf↔Performance, Ernährung↔Recovery, Protein↔Muskelaufbau) | Braucht vollständige Zeitreihen und echte statistische Methoden, nicht LLM-Schätzungen |
| Cross-User Benchmarks | Braucht Daten anderer User — ein Agent hat nur seine eigenen |
| Hierarchische Modelle (Population → Individuum) | Borrowed Strength: wenig eigene Daten werden mit Population-Priors angereichert. Braucht zentrale Daten. |
| Multi-Agent Write Coordination | Mehrere Agents gleichzeitig schreiben → Konflikte. Braucht zentralen Koordinator. |
| Governance & Permissions | Welcher Agent (im Auftrag welches Menschen) darf was sehen/schreiben? Braucht zentrale Autorität. |
| Daten-Import/Normalisierung | Pipeline-Arbeit, kein LLM-Task |
| Audit & Nachweisbarkeit | Agent-Kopie ist nicht beweiskräftig. Immutable Log mit Provenance schon. |

## Architektur: Event Sourcing + CQRS auf PostgreSQL

### Grundprinzip

Trainings- und Ernährungsdaten sind natürliche Events. Du "updatest" keinen Satz den du gemacht hast — er ist passiert. Jeder Satz, jeder Lauf, jede Mahlzeit, jede Messung ist ein Event zu einem Zeitpunkt.

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
| **pgvector** | Semantische Suche. Embeddings für Exercise-Resolution, Food-Resolution, Alias-Matching. | Separater Fuzzy-Matching-Service |
| **JSONB** | Flexible Event-Daten und Projection-Daten ohne Schema-Migration. | Document Store (MongoDB) |

Zusätzlich: `LISTEN/NOTIFY` für Event-Subscriptions, Partitioning für Event-Tabellen, Indices auf (user_id, timestamp).

Vorteile einer einzigen DB:
- Ein Backup, ein Monitoring, ein Scaling-Pfad
- Transaktionale Konsistenz: Event schreiben + Projection updaten in einer Transaktion
- Self-Hosting wird einfach: ein Docker-Container plus Postgres-Setup
- pg_duckdb für Statistical Engine: Bayesian Regression über 2 Jahre direkt in der DB
- pgvector für Semantic Layer: Exercise- und Food-Embeddings direkt querybar

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
│   │ Semantic Layer│  pgvector: Exercise + Food        │
│   │               │  Resolution, Alias-Matching       │
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
│   │  posteriors (pre-computed Bayesian, JSONB)│       │
│   │  statistics (frequentist baseline, JSONB) │       │
│   │  benchmarks (aggregate, anonymized)      │        │
│   │                                          │        │
│   │  + pg_duckdb  → Analytical Queries       │        │
│   │  + pgvector   → Semantic Resolution      │        │
│   │  + LISTEN/NOTIFY → Event Subscriptions   │        │
│   └──────────────┬──────────────────────────┘        │
│                  │                                    │
│   ┌──────────────▼──────────────────────────┐        │
│   │       Background Workers (Python)        │        │
│   │                                          │        │
│   │  Projection Engine: Updates on new Event │        │
│   │  Bayesian Engine: PyMC/Stan, MCMC,       │        │
│   │    Posteriors, Predictive Distributions   │        │
│   │  Statistical Engine: Frequentist Baseline │        │
│   │  Benchmark Engine: Hierarchical Models,  │        │
│   │    Cross-User Aggregation                │        │
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

Agents lesen nie aus dem Event Store. Sie lesen aus vorberechneten Projections und Posteriors.

Projections werden bei jedem neuen Event aktualisiert:
- **Per-User:** Exercise Progression, Volume Tracking, PRs, Fatigue, Metric Trends, Ernährungsbilanz, Makro-Tracking
- **Bayesian Posteriors:** Individuelle Modellparameter, Predictive Distributions, Changepoints, Kausal-Schätzungen
- **Statistische:** Effektstärken, Korrelationen, Anomalien (Frequentist-Baseline)
- **Aggregate:** Cross-User Benchmarks, Population Norms, Hierarchische Modell-Priors (anonymisiert, opt-in)

### Semantic Layer

Zwischen Client und Command/Query Side. Powered by pgvector.

- **Exercise Resolution:** "bench", "Bankdrücken", "Bench Press" → gleiche Übung (Embedding-Similarity)
- **Food Resolution:** "Hähnchenbrust", "chicken breast", "Hühnerbrust" → gleiches Lebensmittel
- **Zeitliche Auflösung:** Agent liefert den Datumsbereich, Backend normalisiert/validiert ihn
- **Semantische Auflösung:** "Oberkörper" → [push, pull] Muskelgruppen
- **Keine NL-Interpretation.** Das macht der Agent. Das Backend resolved Begriffe, nicht Sätze.

### Type Inference

Nichts hardcoded. Typen emergieren aus Nutzung, nicht aus Schema.

- User loggt weight + reps für "Squat" → System inferiert: Kraft-Übung
- User loggt distance + time für "10k" → System inferiert: Ausdauer-Übung
- User loggt nur duration für "Yoga" → System inferiert: Time-only
- User loggt kcal + protein + carbs für "Mittagessen" → System inferiert: Mahlzeit mit Makros

Erst wenn genug Daten da sind, bietet das System passende Projections an. Kein Zwang zur Kategorisierung.

Zusätzlich: User/Agent kann jederzeit explizit taggen (Muskelgruppen, Bewegungsmuster, Equipment, Lebensmittelkategorien). Community-Taxonomy als optionaler Default für bekannte Übungen und Lebensmittel.

Inference ist versioniert und stabilisiert: Sobald ein Typ "gefriert", bleibt er konsistent, kann aber bewusst vom User/Agent überschrieben werden. Änderungen triggern Rebuilds der Projections.

### Context-Enriched Responses

Jede Response enthält nicht nur die angefragten Daten, sondern automatisch relevanten Kontext:

```json
{
  "data": { ... },
  "context": {
    "anomalies": ["performance_declining_2w"],
    "correlations": {
      "sleep_quality": {"effect": -0.34, "ci_95": [-0.58, -0.11], "causal_plausibility": "moderate"},
      "protein_intake": {"effect": 0.22, "ci_95": [-0.04, 0.48], "causal_plausibility": "low_sample"}
    },
    "fatigue_index": 7.2,
    "current_program": "5/3/1 Week 3",
    "nutrition_status": {"protein_target_hit": true, "caloric_balance": -120}
  },
  "meta": {
    "computed_at": "2026-02-07T18:00:00Z",
    "projection_version": 42,
    "cache_hit": true,
    "statistical_quality": { "sample_size": 47, "sufficient_for": ["trend", "correlation"], "insufficient_for": ["causal_inference"] },
    "mcmc_diagnostics": { "rhat": 1.001, "ess": 4200 }
  }
}
```

Der Agent braucht einen Call, nicht fünf.

### Agent-First Interface Design

Das Interface ist für Agents gebaut. Kein Mensch interagiert direkt mit der API.

Beispiel — ein einziger Call gibt dem Agent alles für eine Squat-Analyse:

```json
// GET /v1/users/{id}/analysis/squat
{
  "progression": {
    "estimated_1rm": { "mean": 127.3, "ci_95": [122.1, 132.8], "distribution": "posterior" },
    "trend": "improving",
    "rate": { "kg_per_week": 0.8, "ci_95": [0.3, 1.4] },
    "plateau_probability": 0.12,
    "predicted_1rm_4w": { "mean": 130.5, "ci_95": [124.2, 137.1] }
  },
  "context": {
    "changepoints": [{ "date": "2026-01-15", "type": "improvement", "cause_hypothesis": "program_change" }],
    "correlations": {
      "sleep_hours": { "effect": 0.34, "ci_95": [0.11, 0.58], "causal_plausibility": "moderate" },
      "protein_g": { "effect": 0.22, "ci_95": [-0.04, 0.48], "causal_plausibility": "low_sample" }
    },
    "anomalies": [],
    "data_quality": { "sessions": 47, "completeness": 0.89, "sufficient_for": ["trend", "correlation"], "insufficient_for": ["causal_inference"] }
  },
  "meta": { "computed_at": "...", "model_version": "...", "mcmc_diagnostics": { "rhat": 1.001, "ess": 4200 } }
}
```

Agent-Interface Prinzipien:
- Stabile IDs, klare Enums, versionierte Schemas
- Konsistente Fehlercodes und Idempotency-Keys
- Pagination, Filter, Zeitfenster, inkrementelle Updates
- Kostenhinweise (query_cost, cache_hit) und Daten-Linie (computed_at, projection_version, model_version)
- Berechtigungen/Scopes maschinenlesbar (was darf dieser Agent sehen/schreiben)
- Uncertainty überall: kein Wert ohne Konfidenz- oder Credible-Intervall
- Datenqualität transparent: was kann berechnet werden, was nicht, warum nicht

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

**Ernährung:**
- `meal.logged` (Mahlzeit, Snack, Supplement — Items, Mengen, Makros)
- `nutrition.daily_summary` (vorberechnete Tagesbilanz: kcal, Protein, Fett, Kohlenhydrate, Mikronährstoffe)
- `hydration.logged` (Flüssigkeitsaufnahme)
- `food.created`, `food.updated` (benutzerdefinierte Lebensmittel, Rezepte)

**Gesundheit & Metriken:**
- `metric.logged` (beliebiger Typ: bodyweight, body_fat, sleep, stress, hrv, blood_pressure, custom)
- `health.imported` (Apple Health, Garmin, Whoop, Oura)
- `blood_work.logged` (Laborwerte: Testosteron, Vitamin D, Eisen, etc.)
- `subjective.logged` (Energie, Stimmung, Muskelkater, Soreness — Likert-Skala)

**Context:**
- `note.added`, `injury.reported`, `injury.resolved`
- `program.started`, `program.phase_changed`
- `nutrition_goal.set` (Kalorienziel, Makroverteilung, Diätphase)

**Corrections:**
- `event.corrected` (referenziert Original, liefert neue Daten)
- `event.voided` (referenziert Original, markiert als ungültig)

## Statistik: Bayesian-First, Goldstandard

### Philosophie

Statistische Rigorosität ist Kernmerkmal, nicht Marketing-Feature. Bayesian-Methoden sind der Goldstandard für individuelle Trainingsdaten. Frequentist-Statistik ist die Baseline, nicht das Ziel.

- **Bayesian-First.** Volle Posterior-Distributionen statt Punktschätzungen. Credible Intervals statt Konfidenzintervalle. Echte Wahrscheinlichkeitsaussagen ("83% Wahrscheinlichkeit, dass du 130kg in 4 Wochen schaffst"), nicht p-Werte.
- **Rigorosität.** Wenn Voraussetzungen für einen Test nicht erfüllt sind: kein Test. Transparente Kommunikation der Limitation. Lieber "nicht genug Daten" als eine falsche Aussage.
- **Uncertainty überall.** Kein Wert ohne Intervall. Der Agent weiß immer, wie sicher eine Aussage ist.
- **Effektstärken statt nur Signifikanz.** "Statistisch signifikant" ohne praktische Relevanz ist wertlos.
- **Datenqualität als Fundament.** Jede Analyse kommuniziert ihre eigene Belastbarkeit.
- **Der Agent übersetzt.** Das Backend liefert rohe Posteriors, Diagnostik, Qualitäts-Metadaten. Der Agent macht daraus verständliche Sprache.

### Warum Bayesian hier überlegen ist

1. **Kleine Stichproben.** Ein einzelner Mensch hat vielleicht 50 Squat-Sessions in 6 Monaten. Frequentist-Methoden brauchen große n. Bayesian funktioniert mit kleinen n, weil Vorwissen (Priors) einfliesst.
2. **Sequentielles Updaten.** Jedes neue Event aktualisiert die Posterior-Distribution. Kein "warte bis n=30, dann teste". Natürlich für Event Sourcing — jedes Event ist ein Update deines Wissens.
3. **Echte Wahrscheinlichkeitsaussagen.** Nicht "p < 0.05" (was die meisten falsch interpretieren), sondern: "83% Wahrscheinlichkeit, dass du innerhalb von 4 Wochen 130kg schaffst."
4. **Hierarchische Modelle.** Der Killer für Cross-User: Individuelle Parameter werden geschätzt, aber mit Borrowed Strength aus der Population. Wenig eigene Daten? Das Modell nutzt was es von ähnlichen Usern weiß.
5. **Prior Knowledge.** Muskelaufbau folgt logarithmischen Kurven. Kraft korreliert mit Querschnittsfläche. Supercompensation hat Zeitkonstanten. All das kann als informierte Priors encodiert werden.

### Statistische Methoden

| Methode | Anwendung | Vorteil gegenüber Klassisch |
|---|---|---|
| **BEST** (Bayesian Estimation Supersedes T-test, Kruschke) | Vorher/Nachher-Vergleiche (Trainingsblock-Effekte, Diätphasen) | Volle Posterior statt ja/nein. Effect Size + Uncertainty in einem. |
| **Bayesian Changepoint Detection** (BOCPD) | Wann hat sich etwas verändert? Plateau erkannt, Overreaching erkannt, Ernährungsumstellung wirksam? | Findet Zeitpunkte, nicht nur "es gibt einen Trend". |
| **Gaussian Process Regression** | Smooth Trend-Schätzung mit Unsicherheitsbändern. Progression über Monate. Gewichtsverlauf. | Keine funktionale Form angenommen. Uncertainty wächst wo wenig Daten sind. |
| **Bayesian Structural Time Series** (BSTS) | Kausal-Effekte. "Hat der Programmwechsel geholfen?" "Hat die Ernährungsumstellung gewirkt?" | Counterfactual: Was wäre ohne Intervention passiert? |
| **Hierarchische / Mixed-Effects Modelle** | Cross-User: Individuelle Progression im Kontext der Population. | Borrowed Strength. Wenig eigene Daten → Population füllt auf. |
| **Survival Analysis** | Zeit bis PR, Zeit bis Verletzung, Zeit bis Plateau. | Censored Data nativ (User der noch keinen PR hat ist nicht "kein PR", sondern "noch nicht"). |
| **Bayesian Outlier Models** | Datenqualität. War der 200kg Squat echt oder ein Tippfehler? | Robuste Schätzung, Outlier werden identifiziert statt Ergebnisse zu verfälschen. |
| **Sequential Analysis** | Laufende Entscheidungen: Funktioniert das Programm? Funktioniert die Diät? Soll ich wechseln? | Kein festes n nötig. Evidence akkumuliert mit jeder Session / jedem Tag. |
| **Causal Inference** (DoCalculus, Propensity Scores) | Schlafdauer → Performance: Korrelation oder Kausal? Protein → Recovery: echt oder Confounder? | Beobachtungsdaten sind kein RCT. Causal Inference extrahiert kausale Effekte soweit möglich. |

### Datenqualität

Goldstandard-Statistik erfordert Goldstandard-Daten.

- **Multiple Imputation** statt Deletion bei fehlenden Daten (verpasste Sessions, vergessene Mahlzeiten)
- **Measurement Error Models** — RPE ist subjektiv, Waage schwankt, HR-Sensoren haben Noise, Kalorienangaben sind Schätzungen
- **Bayesian Outlier Detection** bei Ingestion (probabilistisch, nicht harte Grenzen)
- **Minimum Sample Sizes** transparent kommuniziert: "Für diese Analyse brauchst du noch ~12 Sessions"
- **Prior Predictive Checks** — bevor das Modell auf Daten losgelassen wird, prüfen ob die Priors sinnvoll sind
- **MCMC-Diagnostik** in jeder Response: Rhat, ESS, Divergences. Der Agent kann Ergebnis-Qualität beurteilen.
- **Statistische Qualitäts-Metadaten** bei jeder Response (sample_size, sufficient_for, insufficient_for, confidence_level, test_assumptions_met)

### Tech-Stack Statistical Engine

Python (PyMC + ArviZ + Stan via CmdStan). Läuft als Background Workers, nicht im Hot Path.

- **PyMC:** Bayesian Modelle, MCMC Sampling, Variational Inference
- **Stan (CmdStan):** Für die komplexesten Modelle (hierarchisch, non-centered parameterizations)
- **ArviZ:** Diagnostik, Posterior-Analyse, Model Comparison
- **scipy/statsmodels:** Frequentist-Baseline (Effektstärken, klassische Tests als Fallback)

Posteriors werden als Projections in PostgreSQL gespeichert — der Agent liest fertige Ergebnisse, nicht rohe MCMC-Chains.

## Auth & Multi-Agent

### Prinzip: Mensch → Agent → API

Kein Mensch interagiert direkt mit der API. Der Flow ist immer:

```
Mensch redet mit Agent → Agent authentifiziert sich bei Kura → Kura antwortet
```

Ob der Mensch einen einzelnen Agent nutzt oder sein Agent intern einen Schwarm orchestriert, ist transparent für Kura. Die API sieht authentifizierte Requests, nicht die Agent-Architektur dahinter.

### Authentifizierung

**Ein Flow für alle End-User: OAuth Authorization Code + PKCE.** Browser öffnet sich, Mensch autorisiert, Agent bekommt Token. CLI, MCP und REST nutzen denselben Flow.

- **OAuth Auth Code + PKCE:** Primärer Auth-Flow. CLI startet lokalen Callback-Server, öffnet Browser, User loggt ein, Token wird lokal gespeichert mit Auto-Refresh.
- **API Keys für Maschinen:** CI/CD, Server, Automation — kein Browser verfügbar. API Keys werden per Admin-CLI direkt in der DB erstellt.
- **Scoping nach Mensch, nicht nach Agent:** Ein Token gilt für den Menschen. Ob ein Agent oder ein Schwarm ihn nutzt, ist transparent für die API.
- **Delegated Tokens:** Eingeschränkter Zugriff, erstellt vom User für andere Menschen deren Agents Zugriff brauchen.

### Multi-Agent / Multi-Mensch

Scoping nach **Mensch**, nicht nach Agent. Der Agent eines Coaches authentifiziert sich mit einem Delegated Token:

| Rolle | Sieht | Schreibt |
|---|---|---|
| Eigener Agent(en) | Alles | Alles |
| Coach-Agent | Training, Ernährung, relevante Gesundheitsdaten | Programmempfehlungen, Ernährungspläne |
| Arzt-Agent | Gesundheitsdaten, Laborwerte, Verletzungen | Medizinische Notizen |
| Physio-Agent | Training, Verletzungen, Mobility | Behandlungsnotizen |

Der User definiert über seine Agent-Oberfläche, welche Delegation er erteilt. Die Technik dahinter sind Scoped Tokens. OAuth kann später als Option für ein Web-Dashboard hinzukommen.

## Business Model

### SaaS (Primary)

Das Produkt ist ein gehosteter Cloud-Service. User registrieren sich, verbinden ihren Agent, zahlen monatlich.

**Free: Logging + Lesen + Export. Pro: Compute.**

Die Grenze folgt dem Moat: was der Agent im Kontextfenster selbst kann (Free) vs. was nur das Backend berechnen kann (Pro).

- **Free:** Events schreiben, Rohdaten lesen, Export. Basis-Projections. Der Agent funktioniert grundlegend.
- **Pro:** Bayesian Posteriors, Predictive Distributions, Korrelationsanalysen, Anomaly Detection, Changepoint Detection, Cross-User Benchmarks, Ernährungsanalysen, Hierarchische Modelle. Alles was Compute kostet.

SaaS ist das Primärmodell weil:
- Cross-User Benchmarks nur zentral funktionieren
- Bayesian Engine von zentralem Compute profitiert
- Zielkunde (Person mit Agent) will kein Backend deployen

### CLI (Shell-Agents)

Source-available (BSL-Lizenz). Eigenes Rust-Binary, JSON-only. Für Agents mit Shell-Zugriff (Claude Code, Cursor, VPS-Agents). Spielt die Stärken der Shell aus: composable, ephemeral, scriptbar, minimale Dependencies.

### MCP Server (MCP-Agents)

Eigenes Projekt. Thin Client über die REST API, spricht MCP-Protokoll. Für MCP-Clients (Claude Desktop lokal, Claude.ai/ChatGPT gehostet). Spielt die Stärken von MCP aus: Tool Discovery, typisierte Schemas, persistente Verbindung, native Integration.

Beide rufen die gleiche REST API auf. Kein shared Code nötig — der Overlap ist trivial (HTTP-Requests).

### Self-Hosted (Phase 2)

Für Privacy-bewusste User, Gyms, Coaching-Businesses. Premium-Preis. Kein Zugriff auf Cross-User Benchmarks (Daten sind isoliert).

Architektur ist von Tag 1 self-hosting-fähig: PostgreSQL-Only, Docker Compose, keine Cloud-spezifischen Abhängigkeiten.

## Entschiedenes

- **Scope:** Training (Kraft + Ausdauer), Ernährung und Gesundheitsdaten von Anfang an
- **Architektur:** Event Sourcing + CQRS
- **Datenbank:** PostgreSQL-Only mit pg_duckdb, pgvector, JSONB. Kein zweites System.
- **Datenmodell:** Events statt starkes Schema. Type Inference statt vordefinierte Typen. Nichts hardcoded.
- **Tech Stack:** Rust (API + CLI), Python (Statistical/Bayesian Workers). Zwei Sprachen, jede wo sie stark ist.
- **Statistical Engine:** Bayesian-First. PyMC + Stan + ArviZ. Frequentist als Baseline. Goldstandard, kein Marketing.
- **Business Model:** SaaS primary (Phase 1), Self-Hosted als spätere Option (Phase 2). CLI Source-available (BSL).
- **Monetarisierung:** Free = Logging + Lesen + Export. Pro = Compute (Bayesian, Korrelationen, Benchmarks, Anomaly Detection).
- **Interface-Stack:** REST API als Fundament. CLI und MCP Server als separate Projekte — jedes spielt seine eigenen Stärken aus. CLI: Shell-optimiert, ephemeral, composable. MCP: Tool Discovery, typisiert, persistent. Kein shared Binary, bewusst getrennt.
- **CLI-Design:** JSON-only. Kein Human-Readable Mode. Agents sind die User. `kura` als Command-Name.
- **Auth:** OAuth Authorization Code + PKCE als primärer Flow (CLI, MCP, REST). API Keys für Maschinen (CI/CD). Scoping nach Mensch, nicht nach Agent. Delegated Tokens für Coach/Arzt/Physio.
- **Background Workers:** PostgreSQL-based Job Queue (SKIP LOCKED / pgmq). Kein Redis, kein RabbitMQ.
- **Hosting:** Fly.io oder Hetzner Cloud zum Start. Managed PostgreSQL (Supabase, Neon, oder Crunchy Bridge). Kein Vendor Lock-in.
- **Docker:** Docker Compose für Entwicklung und Self-Hosted. PostgreSQL + Rust API + Python Workers.
- **Produktname:** Kura Training
- **Response-Design:** Context-Enriched. Jede Antwort enthält data + context + meta. Uncertainty überall.
- **Temporal Queries:** First-Class. Zeitreisen, Vergleiche, Hypotheticals nativ.
- **Corrections:** Compensating Events, nie Mutation. Idempotency-Keys auf jedem Event.
- **Defensibility:** Compute, not Storage. Daten sind klein und kopierbar. Der Moat liegt in Bayesian Compute, Cross-User Benchmarks, Multi-Agent Governance, und Write Coordination — nicht in der Datenhaltung selbst.

## Offene Entscheidungen

### Benchmarks

- Start mit populationsbasierten Normen aus der Sportwissenschaft
- Echte Cross-User Benchmarks wenn Datenbasis existiert
- Privacy: Anonymisierung, Opt-in
- Hierarchische Modelle: wie genau wird Population-Information in individuelle Priors eingespeist?
