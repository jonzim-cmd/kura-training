'use client';

import { useState } from 'react';
import { useTranslations } from 'next-intl';
import { Link } from '@/i18n/routing';
import styles from './request-access.module.css';

export default function RequestAccessPage() {
  const t = useTranslations('requestAccess');
  const tn = useTranslations('nav');
  const [submitted, setSubmitted] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  const handleSubmit = async (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    setSubmitting(true);

    const form = e.currentTarget;
    const data = {
      email: (form.elements.namedItem('email') as HTMLInputElement).value,
      name: (form.elements.namedItem('name') as HTMLInputElement).value || undefined,
      context: (form.elements.namedItem('context') as HTMLTextAreaElement).value || undefined,
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
      } else {
        // Always show success — don't leak whether email exists
        setSubmitted(true);
      }
    } catch {
      // Network error — show success anyway (don't leak info, request may have gone through)
      setSubmitted(true);
    }

    setSubmitting(false);
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

          <button
            type="submit"
            className="kura-btn kura-btn--primary"
            style={{ width: '100%' }}
            disabled={submitting}
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
