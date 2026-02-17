'use client';

import { useEffect, useState } from 'react';
import { useSearchParams } from 'next/navigation';
import { useTranslations } from 'next-intl';
import { Link, useRouter } from '@/i18n/routing';
import { TurnstileWidget } from '@/components/TurnstileWidget';
import { useAuth } from '@/lib/auth-context';
import {
  SOCIAL_AUTH_ENABLED,
  socialAuthorizeUrl,
  type SocialProvider,
} from '@/lib/social-auth';
import { SETUP_SEEN_STORAGE_KEY } from '@/lib/onboarding';
import styles from '../../auth.module.css';

function GoogleIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path fill="#EA4335" d="M12 10.2v3.96h5.5c-.24 1.27-.96 2.35-2.03 3.08l3.28 2.54c1.92-1.76 3.02-4.36 3.02-7.45 0-.7-.06-1.38-.18-2.03H12z" />
      <path fill="#34A853" d="M12 22c2.73 0 5.02-.9 6.69-2.44l-3.28-2.54c-.91.61-2.07.97-3.41.97-2.62 0-4.84-1.77-5.64-4.14H3v2.6A10 10 0 0012 22z" />
      <path fill="#4A90E2" d="M6.36 13.85A6 6 0 016 12c0-.64.12-1.25.36-1.85V7.55H3A10 10 0 002 12c0 1.61.38 3.13 1 4.45l3.36-2.6z" />
      <path fill="#FBBC05" d="M12 5.96c1.49 0 2.83.51 3.88 1.5l2.91-2.9A9.96 9.96 0 0012 2 10 10 0 003 7.55l3.36 2.6C7.16 7.73 9.38 5.96 12 5.96z" />
    </svg>
  );
}

function GitHubIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
      <path d="M12 2C6.48 2 2 6.58 2 12.22c0 4.5 2.87 8.32 6.84 9.66.5.1.68-.22.68-.5 0-.24-.01-1.03-.02-1.87-2.78.62-3.37-1.21-3.37-1.21-.45-1.2-1.11-1.52-1.11-1.52-.9-.64.07-.63.07-.63 1 .08 1.52 1.04 1.52 1.04.88 1.55 2.3 1.1 2.87.84.09-.66.34-1.1.62-1.35-2.22-.26-4.56-1.14-4.56-5.05 0-1.12.39-2.03 1.03-2.75-.1-.26-.45-1.31.1-2.73 0 0 .84-.28 2.75 1.05a9.3 9.3 0 015 0c1.9-1.33 2.74-1.05 2.74-1.05.56 1.42.21 2.47.11 2.73.64.72 1.03 1.63 1.03 2.75 0 3.92-2.34 4.79-4.57 5.04.35.31.67.92.67 1.86 0 1.35-.01 2.43-.01 2.76 0 .27.18.6.69.5A10.25 10.25 0 0022 12.22C22 6.58 17.52 2 12 2z" />
    </svg>
  );
}

function AppleIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
      <path d="M15.26 3.08c.04 1.24-.45 2.21-1.17 3.05-.77.88-2.04 1.55-3.23 1.46-.15-1.17.44-2.39 1.15-3.18.78-.88 2.14-1.55 3.25-1.33zm4.04 15.22c-.54 1.23-.8 1.78-1.5 2.9-.97 1.55-2.33 3.48-4.01 3.49-1.5.01-1.89-.97-3.92-.96-2.03.01-2.47.98-3.96.97-1.69-.01-2.98-1.76-3.95-3.31C-.74 16.9-.72 9.93 1.84 6.36c1.82-2.54 4.7-2.86 6.46-2.86 1.79 0 2.91.98 4.39.98 1.43 0 2.31-.98 4.37-.98.64 0 2.94.18 4.33 2.21-3.57 1.96-2.99 7.06.91 8.59z" />
    </svg>
  );
}

const SOCIAL_SIGNUP_CONSENTS_KEY = 'kura_social_signup_consents_v1';
const TURNSTILE_SITE_KEY = process.env.NEXT_PUBLIC_TURNSTILE_SITE_KEY?.trim() ?? '';

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
  const [pendingSocialAccessToken, setPendingSocialAccessToken] = useState<string | null>(null);
  const [turnstileToken, setTurnstileToken] = useState<string | null>(null);
  const [turnstileResetNonce, setTurnstileResetNonce] = useState(0);
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

    setPendingSocialAccessToken(socialAccessToken);
    setError(t('completeCaptchaToContinue'));
    clearHash();
  }, [
    consentAnonymized,
    consentHealth,
    inviteToken,
    t,
  ]);

  useEffect(() => {
    if (!pendingSocialAccessToken || !inviteToken) return;
    if (!TURNSTILE_SITE_KEY) {
      setError(t('captchaUnavailable'));
      return;
    }
    if (!turnstileToken) return;

    let cancelled = false;
    setSubmitting(true);
    setError(null);

    registerWithSupabaseToken(
      pendingSocialAccessToken,
      inviteToken,
      consentHealth,
      consentAnonymized,
      turnstileToken,
    )
      .then(() => {
        if (cancelled) return;
        clearStoredConsents();
        setPendingSocialAccessToken(null);
        router.push('/setup');
      })
      .catch((err) => {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : t('socialLoginFailed'));
        setTurnstileToken(null);
        setTurnstileResetNonce((value) => value + 1);
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
    pendingSocialAccessToken,
    registerWithSupabaseToken,
    router,
    t,
    turnstileToken,
  ]);

  // Redirect if already logged in
  if (!loading && user) {
    const dest = localStorage.getItem(SETUP_SEEN_STORAGE_KEY) === '1' ? '/settings' : '/setup';
    router.replace(dest);
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
    if (!TURNSTILE_SITE_KEY) {
      setError(t('captchaUnavailable'));
      return;
    }
    if (!turnstileToken) {
      setError(t('captchaRequired'));
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
        turnstileToken,
      );
      router.push('/setup');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Registration failed');
      setTurnstileToken(null);
      setTurnstileResetNonce((value) => value + 1);
    } finally {
      setSubmitting(false);
    }
  };

  const handleSocialSignup = (provider: SocialProvider) => {
    setError(null);
    setConsentHealthError(false);
    setConsentAnonymizedError(false);
    setPendingSocialAccessToken(null);

    if (!socialEnabled) {
      setError(t('socialLoginNotConfigured'));
      return;
    }
    if (!TURNSTILE_SITE_KEY) {
      setError(t('captchaUnavailable'));
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

          <div className={styles.captchaBlock}>
            <TurnstileWidget
              siteKey={TURNSTILE_SITE_KEY}
              action="signup"
              resetNonce={turnstileResetNonce}
              onTokenChange={setTurnstileToken}
              onUnavailable={() => setError(t('captchaUnavailable'))}
            />
            <p className={styles.captchaHint}>{t('captchaNotice')}</p>
          </div>

          {pendingSocialAccessToken && !turnstileToken && (
            <p className={styles.socialHint}>{t('completeCaptchaToContinue')}</p>
          )}

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
                disabled={submitting || !socialConsentReady || !TURNSTILE_SITE_KEY}
                aria-disabled={submitting || !socialConsentReady || !TURNSTILE_SITE_KEY}
              >
                <GoogleIcon />
                {t('continueWithGoogle')}
              </button>
              <button
                type="button"
                className={styles.socialBtn}
                onClick={() => handleSocialSignup('github')}
                disabled={submitting || !socialConsentReady || !TURNSTILE_SITE_KEY}
                aria-disabled={submitting || !socialConsentReady || !TURNSTILE_SITE_KEY}
              >
                <GitHubIcon />
                {t('continueWithGithub')}
              </button>
              <button
                type="button"
                className={styles.socialBtn}
                onClick={() => handleSocialSignup('apple')}
                disabled={submitting || !socialConsentReady || !TURNSTILE_SITE_KEY}
                aria-disabled={submitting || !socialConsentReady || !TURNSTILE_SITE_KEY}
              >
                <AppleIcon />
                {t('continueWithApple')}
              </button>
            </div>
          )}

          <button
            type="submit"
            className="kura-btn kura-btn--primary"
            style={{ width: '100%' }}
            disabled={submitting || !TURNSTILE_SITE_KEY}
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
