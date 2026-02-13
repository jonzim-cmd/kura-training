'use client';

import { useState } from 'react';
import { useTranslations } from 'next-intl';
import { Link, useRouter } from '@/i18n/routing';
import { useAuth } from '@/lib/auth-context';
import styles from '../../auth.module.css';

export default function LoginPage() {
  const t = useTranslations('auth');
  const { login, user, loading } = useAuth();
  const router = useRouter();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  // Redirect if already logged in
  if (!loading && user) {
    router.replace('/settings');
    return null;
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      await login(email, password);
      router.push('/settings');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Login failed');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className={styles.authPage}>
      <div className={styles.authContainer}>
        <div className={styles.authHeader}>
          <h1 className={styles.authTitle}>{t('loginTitle')}</h1>
          <p className={styles.authSubtitle}>{t('loginSubtitle')}</p>
        </div>

        {error && <div className={styles.errorBanner}>{error}</div>}

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

          <div className={styles.field}>
            <label htmlFor="password" className="kura-label">{t('password')}</label>
            <input
              id="password"
              type="password"
              required
              autoComplete="current-password"
              className="kura-input"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
          </div>

          <button
            type="submit"
            className="kura-btn kura-btn--primary"
            style={{ width: '100%' }}
            disabled={submitting}
          >
            {submitting ? t('loggingIn') : t('loginButton')}
          </button>
        </form>

        <p className={styles.authSwitch}>
          {t('noAccount')}{' '}
          <Link href="/request-access">{t('requestAccessLink')}</Link>
        </p>
      </div>
    </div>
  );
}
