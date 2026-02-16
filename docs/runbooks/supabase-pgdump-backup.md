# Supabase Daily pg_dump Backup (S3-Compatible)

This runbook configures a daily logical backup (`pg_dump`) and uploads it to an external S3-compatible object storage target (AWS S3, Cloudflare R2, Backblaze B2 S3 API, etc.).

## 1. Configure environment

On the production host, edit `docker/.env.production` and add:

- `BACKUP_S3_BUCKET`
- `BACKUP_S3_PREFIX`
- `BACKUP_S3_ENDPOINT_URL` (only for non-AWS providers)
- `AWS_REGION`
- `AWS_ACCESS_KEY_ID`
- `AWS_SECRET_ACCESS_KEY`

Optional:

- `KURA_BACKUP_DATABASE_URL` (if omitted, script uses `KURA_API_DATABASE_URL`)

Template values are documented in `docker/.env.production.example`.

## 2. Prerequisites on host

Ensure binaries are installed:

- `pg_dump` (PostgreSQL client tools)
- `aws` (AWS CLI v2)
- `gzip`

## 3. Run one manual test

```bash
scripts/pgdump_s3_backup.sh --env-file docker/.env.production
```

Expected result:

- Script exits with success
- Two remote objects are uploaded:
  - `.../<timestamp>.sql.gz`
  - `.../<timestamp>.sql.gz.sha256`

## 4. Install daily schedule

Default schedule is daily at `03:15` (server local time):

```bash
scripts/install_backup_cron.sh --env-file docker/.env.production
```

Custom schedule example (daily at `02:30`):

```bash
scripts/install_backup_cron.sh --env-file docker/.env.production --schedule "30 2 * * *"
```

Dry-run (print resulting crontab without installing):

```bash
scripts/install_backup_cron.sh --env-file docker/.env.production --dry-run
```

## 5. Verify cron installation

```bash
crontab -l | grep kura-training-daily-pgdump-backup
```

Default log file:

- `logs/pgdump-backup.log`

## 6. Restore drill recommendation

At least monthly:

1. Download one recent dump (`.sql.gz`)
2. Restore into a temporary database
3. Validate key tables and row counts

This keeps backup confidence high even without PITR.
