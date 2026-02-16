'use client';

import { useState } from 'react';
import { useSearchParams } from 'next/navigation';
import { useTranslations } from 'next-intl';
import { Link } from '@/i18n/routing';
import { apiFetch } from '@/lib/api';
import styles from '../../auth.module.css';

export default function ResetPasswordPage() {
  const t = useTranslations('auth');
  const searchParams = useSearchParams();
  const token = searchParams.get('token') || '';

  const [password, setPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [done, setDone] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleSubmit = async (event: React.FormEvent) => {
    event.preventDefault();
    setError(null);

    if (!token) {
      setError(t('resetPasswordMissingToken'));
      return;
    }
    if (password.length < 8) {
      setError(t('passwordTooShort'));
      return;
    }
    if (password !== confirmPassword) {
      setError(t('passwordMismatch'));
      return;
    }

    setSubmitting(true);
    try {
      const response = await apiFetch('/v1/auth/reset-password', {
        method: 'POST',
        body: JSON.stringify({
          token,
          new_password: password,
        }),
      });
      if (!response.ok) {
        const body = await response.json().catch(() => null);
        throw new Error(body?.message || t('resetPasswordError'));
      }
      setDone(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : t('resetPasswordError'));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className={styles.authPage}>
      <div className={styles.authContainer}>
        <div className={styles.authHeader}>
          <h1 className={styles.authTitle}>{t('resetPasswordTitle')}</h1>
          <p className={styles.authSubtitle}>{t('resetPasswordSubtitle')}</p>
        </div>

        {error && <div className={styles.errorBanner}>{error}</div>}

        {done ? (
          <div className={styles.authForm}>
            <p>{t('resetPasswordSuccess')}</p>
            <p className={styles.authSwitch}>
              <Link href="/login">{t('backToLogin')}</Link>
            </p>
          </div>
        ) : (
          <form onSubmit={handleSubmit} className={styles.authForm}>
            <div className={styles.field}>
              <label htmlFor="password" className="kura-label">{t('password')}</label>
              <input
                id="password"
                type="password"
                required
                autoComplete="new-password"
                className="kura-input"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
              />
            </div>

            <div className={styles.field}>
              <label htmlFor="confirmPassword" className="kura-label">{t('confirmPassword')}</label>
              <input
                id="confirmPassword"
                type="password"
                required
                autoComplete="new-password"
                className="kura-input"
                value={confirmPassword}
                onChange={(e) => setConfirmPassword(e.target.value)}
              />
            </div>

            <button
              type="submit"
              className="kura-btn kura-btn--primary"
              style={{ width: '100%' }}
              disabled={submitting}
            >
              {submitting ? t('resetPasswordSubmitting') : t('resetPasswordButton')}
            </button>
          </form>
        )}
      </div>
    </div>
  );
}
