'use client';

import { useTranslations } from 'next-intl';
import { Link } from '@/i18n/routing';
import styles from '../../auth.module.css';

export default function SignupPage() {
  const t = useTranslations('auth');

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    // TODO: OAuth PKCE flow
  };

  return (
    <div className={styles.authPage}>
      <div className={styles.authContainer}>
        <div className={styles.authHeader}>
          <div className={styles.authKanji} aria-hidden="true">蔵</div>
          <h1 className={styles.authTitle}>{t('signupTitle')}</h1>
          <p className={styles.authSubtitle}>{t('signupSubtitle')}</p>
        </div>

        <form onSubmit={handleSubmit} className={styles.authForm}>
          <div className={styles.field}>
            <label htmlFor="email" className="kura-label">{t('email')}</label>
            <input
              id="email"
              type="email"
              required
              autoComplete="email"
              className="kura-input"
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
            />
          </div>

          <button type="submit" className="kura-btn kura-btn--primary" style={{ width: '100%' }}>
            {t('signupButton')}
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
