'use client';

import { useState } from 'react';
import { useTranslations, useLocale } from 'next-intl';
import { Link } from '@/i18n/routing';
import { apiFetch } from '@/lib/api';
import styles from '../../auth.module.css';

export default function ForgotPasswordPage() {
  const t = useTranslations('auth');
  const locale = useLocale();
  const [email, setEmail] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [done, setDone] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleSubmit = async (event: React.FormEvent) => {
    event.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      const response = await apiFetch('/v1/auth/forgot-password', {
        method: 'POST',
        body: JSON.stringify({ email, locale }),
      });
      if (!response.ok) {
        const body = await response.json().catch(() => null);
        throw new Error(body?.message || t('forgotPasswordError'));
      }
      setDone(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : t('forgotPasswordError'));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className={styles.authPage}>
      <div className={styles.authContainer}>
        <div className={styles.authHeader}>
          <h1 className={styles.authTitle}>{t('forgotPasswordTitle')}</h1>
          <p className={styles.authSubtitle}>{t('forgotPasswordSubtitle')}</p>
        </div>

        {error && <div className={styles.errorBanner}>{error}</div>}

        {done ? (
          <div className={styles.authForm}>
            <p>{t('forgotPasswordSuccess')}</p>
            <p className={styles.authSwitch}>
              <Link href="/login">{t('backToLogin')}</Link>
            </p>
          </div>
        ) : (
          <form onSubmit={handleSubmit} className={styles.authForm}>
            <div className={styles.field}>
              <label htmlFor="email" className="kura-label">{t('email')}</label>
              <input
                id="email"
                type="email"
                required
                autoComplete="email"
                className="kura-input"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
              />
            </div>

            <button
              type="submit"
              className="kura-btn kura-btn--primary"
              style={{ width: '100%' }}
              disabled={submitting}
            >
              {submitting ? t('forgotPasswordSubmitting') : t('forgotPasswordButton')}
            </button>

            <p className={styles.authSwitch}>
              <Link href="/login">{t('backToLogin')}</Link>
            </p>
          </form>
        )}
      </div>
    </div>
  );
}
