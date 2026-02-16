# Auftragsverarbeiter- und Drittlandliste (DE/EN)

Stand: 16. Februar 2026
Scope: Kura Web + API + Worker (EU baseline)

## DE

| Anbieter | Rolle | Zweck | Datenkategorien | Primäre Region | Drittlandtransfer | Absicherung |
| --- | --- | --- | --- | --- | --- | --- |
| Supabase (Projekt `slawzzhovquintrsmfby`) | Auftragsverarbeiter | Authentifizierung, Datenbankbetrieb | Konto-/Login-Daten, Trainings-/Eventdaten, Metadaten | `eu-west-1` (EU) | Primär nein (EU-Betrieb) | AVV/DPA mit Supabase; bei Subprozessoren außerhalb EWR: SCC |
| Resend | Auftragsverarbeiter | Versand transaktionaler E-Mails | E-Mail-Adresse, Versandinhalt, technische Zustellinformationen | USA | Ja | AVV/DPA + EU-Standardvertragsklauseln (SCC) |
| OpenAI API (optional, nur bei aktivierten Embeddings) | Auftragsverarbeiter | Embedding-/Vektorfunktionen | Texteingaben aus freigeschalteten Pipelines (konfigurationsabhängig) | USA | Ja | AVV/DPA + SCC |

Hinweis:
- Google/GitHub/Apple bei Social Login sind in der Regel eigenständige Verantwortliche für den jeweiligen Login-Prozess, nicht klassische Auftragsverarbeiter für den Kura-Kerndienst.

## EN

| Provider | Role | Purpose | Data categories | Primary region | Third-country transfer | Safeguard |
| --- | --- | --- | --- | --- | --- | --- |
| Supabase (project `slawzzhovquintrsmfby`) | Processor | Authentication and database operations | Account/login data, training/event data, metadata | `eu-west-1` (EU) | No for primary runtime (EU) | DPA with Supabase; SCCs for any non-EEA subprocessors |
| Resend | Processor | Transactional email delivery | Email address, email content, technical delivery metadata | United States | Yes | DPA + EU Standard Contractual Clauses (SCCs) |
| OpenAI API (optional, only when embeddings are enabled) | Processor | Embedding/vector features | Text inputs from enabled pipelines (configuration-dependent) | United States | Yes | DPA + SCCs |

Note:
- Google/GitHub/Apple social-login providers are generally independent controllers for their login process, not classic processors for the core Kura service.
