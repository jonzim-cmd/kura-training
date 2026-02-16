# Log Retention Matrix (DE/EN)

Stand: 16. Februar 2026
Technische Durchsetzung: `maintenance.log_retention` Worker-Job + `log_retention_runs` Audit-Tabelle

## DE (Start-Matrix)

| Tabelle | Zweck | Frist |
| --- | --- | --- |
| `api_access_log` | API-Betrieb/Monitoring | 30 Tage |
| `security_abuse_telemetry` | Missbrauchserkennung | 90 Tage |
| `security_kill_switch_audit` | Security-Incident-Nachvollziehbarkeit | 365 Tage |
| `support_access_audit` | Break-glass/Support-Audit | 730 Tage (24 Monate) |
| `password_reset_tokens` (abgelaufen/benutzt) | Sicherheitsartefakte | 30 Tage |

## EN (Starter Matrix)

| Table | Purpose | Retention |
| --- | --- | --- |
| `api_access_log` | API operations/monitoring | 30 days |
| `security_abuse_telemetry` | Abuse detection telemetry | 90 days |
| `security_kill_switch_audit` | Security incident traceability | 365 days |
| `support_access_audit` | Break-glass/support audit | 730 days (24 months) |
| `password_reset_tokens` (expired/used) | Security token artifacts | 30 days |
