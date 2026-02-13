'use client';

import { useTranslations } from 'next-intl';
import { Link } from '@/i18n/routing';
import styles from './Header.module.css';

type HeaderProps = {
  variant?: 'default' | 'landing';
};

// Try to use auth context, but don't fail if we're outside the provider
// (e.g., on landing page which doesn't have AuthProvider)
function useOptionalAuth() {
  try {
    // Dynamic import to avoid circular deps at module level
    const { useAuth } = require('@/lib/auth-context');
    return useAuth();
  } catch {
    return null;
  }
}

export function Header({ variant = 'default' }: HeaderProps) {
  const t = useTranslations('nav');
  const auth = useOptionalAuth();
  const isLoggedIn = auth?.user != null;

  return (
    <header className={`${styles.header} ${variant === 'landing' ? styles.landing : ''}`}>
      <div className={`${styles.inner} ${variant === 'landing' ? styles.innerLanding : ''}`}>
        {variant !== 'landing' && (
          <Link href="/" className={styles.logo}>KU<span style={{letterSpacing: '0.06em'}}>R</span>A</Link>
        )}
        <nav className={styles.nav}>
          {isLoggedIn ? (
            <>
              <Link href="/start" className={styles.link}>{t('home')}</Link>
              <Link href="/setup" className={styles.link}>{t('setup')}</Link>
              <Link href="/settings" className={styles.link}>{t('settings')}</Link>
              <button
                onClick={() => auth?.logout()}
                className={styles.link}
                style={{ background: 'none', border: 'none', cursor: 'pointer' }}
              >
                {t('logout')}
              </button>
            </>
          ) : (
            <>
              {variant !== 'landing' && (
                <Link href="/start" className={styles.link}>{t('home')}</Link>
              )}
              <Link href="/login" className={styles.link}>{t('login')}</Link>
              <Link href="/request-access" className={styles.link}>{t('requestAccess')}</Link>
            </>
          )}
        </nav>
      </div>
    </header>
  );
}
