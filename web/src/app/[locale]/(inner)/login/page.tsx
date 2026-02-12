'use client';

import { useTranslations } from 'next-intl';
import { Link } from '@/i18n/routing';
import styles from '../../auth.module.css';

export default function LoginPage() {
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
          <h1 className={styles.authTitle}>{t('loginTitle')}</h1>
          <p className={styles.authSubtitle}>{t('loginSubtitle')}</p>
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
              autoComplete="current-password"
              className="kura-input"
            />
          </div>

          <div className={styles.formFooter}>
            <Link href="#" className={styles.forgotLink}>{t('forgotPassword')}</Link>
          </div>

          <button type="submit" className="kura-btn kura-btn--primary" style={{ width: '100%' }}>
            {t('loginButton')}
          </button>
        </form>

        <p className={styles.authSwitch}>
          {t('noAccount')}{' '}
          <Link href="/signup">{t('signupButton')}</Link>
        </p>
      </div>
    </div>
  );
}
