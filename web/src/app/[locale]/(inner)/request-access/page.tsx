'use client';

import { useState } from 'react';
import { useTranslations } from 'next-intl';
import { Link } from '@/i18n/routing';
import { TurnstileWidget } from '@/components/TurnstileWidget';
import styles from './request-access.module.css';

const TURNSTILE_SITE_KEY = process.env.NEXT_PUBLIC_TURNSTILE_SITE_KEY?.trim() ?? '';

export default function RequestAccessPage() {
  const t = useTranslations('requestAccess');
  const tn = useTranslations('nav');
  const [submitted, setSubmitted] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [turnstileToken, setTurnstileToken] = useState<string | null>(null);
  const [turnstileResetNonce, setTurnstileResetNonce] = useState(0);

  const handleSubmit = async (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    setError(null);

    if (!TURNSTILE_SITE_KEY) {
      setError(t('captchaUnavailable'));
      return;
    }
    if (!turnstileToken) {
      setError(t('captchaRequired'));
      return;
    }

    setSubmitting(true);

    const form = e.currentTarget;
    const data = {
      email: (form.elements.namedItem('email') as HTMLInputElement).value,
      name: (form.elements.namedItem('name') as HTMLInputElement).value || undefined,
      context: (form.elements.namedItem('context') as HTMLTextAreaElement).value || undefined,
      turnstile_token: turnstileToken,
    };

    try {
      const apiUrl = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:3000';
      const res = await fetch(`${apiUrl}/v1/access/request`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data),
      });

      if (res.ok || res.status === 201) {
        setSubmitted(true);
        return;
      }

      const apiError = await res.json().catch(() => null);
      setError(apiError?.message || t('submitError'));
      setTurnstileToken(null);
      setTurnstileResetNonce((value) => value + 1);
    } catch {
      setError(t('networkError'));
      setTurnstileToken(null);
      setTurnstileResetNonce((value) => value + 1);
    } finally {
      setSubmitting(false);
    }
  };

  if (submitted) {
    return (
      <div className={styles.page}>
        <div className={styles.container}>
          <div className={styles.success}>
            <h1 className={styles.successTitle}>{t('successTitle')}</h1>
            <p className={styles.successText}>{t('successText')}</p>
          </div>
          <p className={styles.loginHint}>
            {t('alreadyHaveAccess')}{' '}
            <Link href="/login">{tn('login')}</Link>
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className={styles.page}>
      <div className={styles.container}>
        <div className={styles.header}>
          <h1 className={styles.title}>{t('title')}</h1>
          <p className={styles.subtitle}>{t('subtitle')}</p>
        </div>

        <div className={styles.pitch}>
          <div className={styles.pitchItem}>
            <span className={styles.pitchMarker}>//</span>
            <span>{t('pitch.free')}</span>
          </div>
          <div className={styles.pitchItem}>
            <span className={styles.pitchMarker}>//</span>
            <span>{t('pitch.full')}</span>
          </div>
          <div className={styles.pitchItem}>
            <span className={styles.pitchMarker}>//</span>
            <span>{t('pitch.help')}</span>
          </div>
        </div>

        <form onSubmit={handleSubmit} className={styles.form}>
          {error && <div className={styles.errorBanner}>{error}</div>}

          <div className={styles.field}>
            <label htmlFor="email" className="kura-label">{t('email')}</label>
            <input
              id="email"
              name="email"
              type="email"
              required
              autoComplete="email"
              className="kura-input"
            />
          </div>

          <div className={styles.field}>
            <label htmlFor="name" className="kura-label">
              {t('name')}<span className={styles.optional}>{t('optional')}</span>
            </label>
            <input
              id="name"
              name="name"
              type="text"
              autoComplete="name"
              className="kura-input"
            />
          </div>

          <div className={styles.field}>
            <label htmlFor="context" className="kura-label">
              {t('context')}<span className={styles.optional}>{t('optional')}</span>
            </label>
            <textarea
              id="context"
              name="context"
              className={styles.textarea}
              placeholder={t('contextPlaceholder')}
              rows={3}
            />
          </div>

          <div className={styles.captchaBlock}>
            <TurnstileWidget
              siteKey={TURNSTILE_SITE_KEY}
              action="access_request"
              resetNonce={turnstileResetNonce}
              onTokenChange={setTurnstileToken}
              onUnavailable={() => setError(t('captchaUnavailable'))}
            />
            <p className={styles.captchaHint}>{t('captchaNotice')}</p>
          </div>

          <button
            type="submit"
            className="kura-btn kura-btn--primary"
            style={{ width: '100%' }}
            disabled={submitting || !TURNSTILE_SITE_KEY}
          >
            {submitting ? t('submitting') : t('submitButton')}
          </button>
        </form>

        <p className={styles.loginHint}>
          {t('alreadyHaveAccess')}{' '}
          <Link href="/login">{tn('login')}</Link>
        </p>
      </div>
    </div>
  );
}
