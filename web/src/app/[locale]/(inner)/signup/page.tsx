'use client';

import { useEffect, useState } from 'react';
import { useSearchParams } from 'next/navigation';
import { useTranslations } from 'next-intl';
import { Link, useRouter } from '@/i18n/routing';
import { useAuth } from '@/lib/auth-context';
import {
  SOCIAL_AUTH_ENABLED,
  socialAuthorizeUrl,
  type SocialProvider,
} from '@/lib/social-auth';
import styles from '../../auth.module.css';

const SOCIAL_SIGNUP_CONSENTS_KEY = 'kura_social_signup_consents_v1';

type StoredConsents = {
  consent_health: boolean;
  consent_anonymized: boolean;
};

function loadStoredConsents(): StoredConsents | null {
  if (typeof window === 'undefined') return null;
  const raw = sessionStorage.getItem(SOCIAL_SIGNUP_CONSENTS_KEY);
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw) as StoredConsents;
    if (
      typeof parsed.consent_health === 'boolean'
      && typeof parsed.consent_anonymized === 'boolean'
    ) {
      return parsed;
    }
  } catch {
    return null;
  }
  return null;
}

function storeConsents(consentHealth: boolean, consentAnonymized: boolean) {
  if (typeof window === 'undefined') return;
  sessionStorage.setItem(
    SOCIAL_SIGNUP_CONSENTS_KEY,
    JSON.stringify({
      consent_health: consentHealth,
      consent_anonymized: consentAnonymized,
    }),
  );
}

function clearStoredConsents() {
  if (typeof window === 'undefined') return;
  sessionStorage.removeItem(SOCIAL_SIGNUP_CONSENTS_KEY);
}

export default function SignupPage() {
  const t = useTranslations('auth');
  const searchParams = useSearchParams();
  const router = useRouter();
  const {
    register,
    registerWithSupabaseToken,
    user,
    loading,
  } = useAuth();
  const inviteToken = searchParams.get('invite');

  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [consentAnonymized, setConsentAnonymized] = useState(false);
  const [consentHealth, setConsentHealth] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [consentAnonymizedError, setConsentAnonymizedError] = useState(false);
  const [consentHealthError, setConsentHealthError] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const socialEnabled = SOCIAL_AUTH_ENABLED;
  const socialConsentReady = consentHealth && consentAnonymized;

  // No invite token -> redirect to request-access
  useEffect(() => {
    if (!inviteToken) {
      router.replace('/request-access');
    }
  }, [inviteToken, router]);

  useEffect(() => {
    const persisted = loadStoredConsents();
    if (!persisted) return;
    setConsentHealth(persisted.consent_health);
    setConsentAnonymized(persisted.consent_anonymized);
  }, []);

  // Handle callback from social provider on signup page.
  useEffect(() => {
    if (!inviteToken || typeof window === 'undefined') return;
    const hash = window.location.hash.startsWith('#')
      ? window.location.hash.slice(1)
      : window.location.hash;
    if (!hash) return;

    const params = new URLSearchParams(hash);
    const providerError = params.get('error_description') || params.get('error');
    const socialAccessToken = params.get('access_token');
    if (!providerError && !socialAccessToken) return;

    const cleanUrl = `${window.location.pathname}${window.location.search}`;
    const clearHash = () => window.history.replaceState({}, document.title, cleanUrl);

    if (providerError) {
      setError(providerError);
      clearHash();
      return;
    }
    if (!socialAccessToken) {
      clearHash();
      return;
    }

    const persisted = loadStoredConsents();
    const effectiveHealth = consentHealth || persisted?.consent_health === true;
    const effectiveAnonymized = consentAnonymized || persisted?.consent_anonymized === true;

    setConsentHealth(effectiveHealth);
    setConsentAnonymized(effectiveAnonymized);
    setConsentHealthError(false);
    setConsentAnonymizedError(false);
    setError(null);

    if (!effectiveHealth) {
      setConsentHealthError(true);
      setError(t('healthConsentRequired'));
      clearHash();
      return;
    }
    if (!effectiveAnonymized) {
      setConsentAnonymizedError(true);
      setError(t('anonymizedConsentRequired'));
      clearHash();
      return;
    }

    let cancelled = false;
    setSubmitting(true);
    registerWithSupabaseToken(
      socialAccessToken,
      inviteToken,
      effectiveHealth,
      effectiveAnonymized,
    )
      .then(() => {
        if (cancelled) return;
        clearStoredConsents();
        clearHash();
        router.push('/setup');
      })
      .catch((err) => {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : t('socialLoginFailed'));
      })
      .finally(() => {
        if (!cancelled) {
          setSubmitting(false);
        }
      });

    return () => {
      cancelled = true;
    };
  }, [
    consentAnonymized,
    consentHealth,
    inviteToken,
    registerWithSupabaseToken,
    router,
    t,
  ]);

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
    setConsentAnonymizedError(false);
    setConsentHealthError(false);

    if (!consentHealth) {
      setConsentHealthError(true);
      setError(t('healthConsentRequired'));
      return;
    }
    if (!consentAnonymized) {
      setConsentAnonymizedError(true);
      setError(t('anonymizedConsentRequired'));
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
      await register(
        email,
        password,
        inviteToken,
        consentHealth,
        consentAnonymized,
      );
      router.push('/setup');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Registration failed');
    } finally {
      setSubmitting(false);
    }
  };

  const handleSocialSignup = (provider: SocialProvider) => {
    setError(null);
    setConsentHealthError(false);
    setConsentAnonymizedError(false);

    if (!socialEnabled) {
      setError(t('socialLoginNotConfigured'));
      return;
    }
    if (!consentHealth) {
      setConsentHealthError(true);
      setError(t('healthConsentRequired'));
      return;
    }
    if (!consentAnonymized) {
      setConsentAnonymizedError(true);
      setError(t('anonymizedConsentRequired'));
      return;
    }

    storeConsents(consentHealth, consentAnonymized);
    const redirectTo = `${window.location.origin}${window.location.pathname}${window.location.search}`;
    window.location.assign(socialAuthorizeUrl(provider, redirectTo));
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

          <label className={`${styles.consent} ${consentHealthError ? styles.consentError : ''}`}>
            <input
              type="checkbox"
              checked={consentHealth}
              onChange={(e) => setConsentHealth(e.target.checked)}
              className={styles.consentCheckbox}
            />
            <span className={styles.consentText}>{t('healthConsentLabel')}</span>
          </label>

          <label className={`${styles.consent} ${consentAnonymizedError ? styles.consentError : ''}`}>
            <input
              type="checkbox"
              checked={consentAnonymized}
              onChange={(e) => setConsentAnonymized(e.target.checked)}
              className={styles.consentCheckbox}
            />
            <span className={styles.consentText}>{t('consentLabel')}</span>
          </label>

          {socialEnabled && (
            <div className={styles.socialProviders}>
              <div className={styles.divider}>
                <span className={styles.dividerText}>{t('orContinueWith')}</span>
              </div>
              <p className={styles.socialHint}>{t('socialSignupConsentHint')}</p>
              <button
                type="button"
                className={styles.socialBtn}
                onClick={() => handleSocialSignup('google')}
                disabled={submitting || !socialConsentReady}
                aria-disabled={submitting || !socialConsentReady}
              >
                {t('continueWithGoogle')}
              </button>
              <button
                type="button"
                className={styles.socialBtn}
                onClick={() => handleSocialSignup('github')}
                disabled={submitting || !socialConsentReady}
                aria-disabled={submitting || !socialConsentReady}
              >
                {t('continueWithGithub')}
              </button>
              <button
                type="button"
                className={styles.socialBtn}
                onClick={() => handleSocialSignup('apple')}
                disabled={submitting || !socialConsentReady}
                aria-disabled={submitting || !socialConsentReady}
              >
                {t('continueWithApple')}
              </button>
            </div>
          )}

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
