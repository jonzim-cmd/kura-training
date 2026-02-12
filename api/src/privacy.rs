use uuid::Uuid;

/// Resolve or create a stable pseudonymous analysis subject id for a user.
pub async fn get_or_create_analysis_subject_id(
    pool: &sqlx::PgPool,
    user_id: Uuid,
) -> Result<String, sqlx::Error> {
    if let Some(existing) = sqlx::query_scalar::<_, String>(
        "SELECT analysis_subject_id FROM analysis_subjects WHERE user_id = $1",
    )
    .bind(user_id)
    .fetch_optional(pool)
    .await?
    {
        return Ok(existing);
    }

    // UUID simple format guarantees 32 lowercase hex chars and satisfies DB check.
    let generated = format!("asub_{}", Uuid::now_v7().simple());
    let inserted = sqlx::query_scalar::<_, String>(
        "INSERT INTO analysis_subjects (user_id, analysis_subject_id) \
         VALUES ($1, $2) \
         ON CONFLICT (user_id) DO UPDATE SET analysis_subject_id = analysis_subjects.analysis_subject_id \
         RETURNING analysis_subject_id",
    )
    .bind(user_id)
    .bind(generated)
    .fetch_one(pool)
    .await?;

    Ok(inserted)
}
