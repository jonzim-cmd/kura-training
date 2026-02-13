'use client';

import { useEffect, useState } from 'react';
import { useSearchParams } from 'next/navigation';
import { useTranslations } from 'next-intl';
import { Link, useRouter } from '@/i18n/routing';
import { useAuth } from '@/lib/auth-context';
import styles from '../../auth.module.css';

export default function SignupPage() {
  const t = useTranslations('auth');
  const searchParams = useSearchParams();
  const router = useRouter();
  const { register, user, loading } = useAuth();
  const inviteToken = searchParams.get('invite');

  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [consent, setConsent] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [consentError, setConsentError] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  // No invite token -> redirect to request-access
  useEffect(() => {
    if (!inviteToken) {
      router.replace('/request-access');
    }
  }, [inviteToken, router]);

  // Redirect if already logged in
  if (!loading && user) {
    router.replace('/settings');
    return null;
  }

  if (!inviteToken) {
    return null;
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setConsentError(false);

    if (!consent) {
      setConsentError(true);
      return;
    }
    if (password !== confirmPassword) {
      setError(t('passwordMismatch'));
      return;
    }
    if (password.length < 8) {
      setError(t('passwordTooShort'));
      return;
    }

    setSubmitting(true);
    try {
      await register(email, password, inviteToken);
      router.push('/setup');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Registration failed');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className={styles.authPage}>
      <div className={styles.authContainer}>
        <div className={styles.authHeader}>
          <h1 className={styles.authTitle}>{t('signupTitle')}</h1>
          <p className={styles.authSubtitle}>{t('signupSubtitle')}</p>
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

          <label className={`${styles.consent} ${consentError ? styles.consentError : ''}`}>
            <input
              type="checkbox"
              checked={consent}
              onChange={(e) => setConsent(e.target.checked)}
              className={styles.consentCheckbox}
            />
            <span className={styles.consentText}>{t('consentLabel')}</span>
          </label>

          <button
            type="submit"
            className="kura-btn kura-btn--primary"
            style={{ width: '100%' }}
            disabled={submitting}
          >
            {submitting ? t('creating') : t('signupButton')}
          </button>
        </form>

        <p className={styles.authSwitch}>
          {t('hasAccount')}{' '}
          <Link href="/login">{t('loginButton')}</Link>
        </p>
      </div>
    </div>
  );
}
