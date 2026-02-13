'use client';

import { useTranslations } from 'next-intl';
import { Link } from '@/i18n/routing';
import styles from './Header.module.css';

type HeaderProps = {
  variant?: 'default' | 'landing';
};

export function Header({ variant = 'default' }: HeaderProps) {
  const t = useTranslations('nav');

  return (
    <header className={`${styles.header} ${variant === 'landing' ? styles.landing : ''}`}>
      <div className={`${styles.inner} ${variant === 'landing' ? styles.innerLanding : ''}`}>
        {variant !== 'landing' && (
          <Link href="/" className={styles.logo}>KU<span style={{letterSpacing: '0.06em'}}>R</span>A</Link>
        )}
        <nav className={styles.nav}>
          {variant !== 'landing' && (
            <Link href="/start" className={styles.link}>{t('home')}</Link>
          )}
          <Link href="/setup" className={styles.link}>{t('setup')}</Link>
          <Link href="/login" className={styles.link}>{t('login')}</Link>
          <Link href="/request-access" className={styles.link}>{t('requestAccess')}</Link>
        </nav>
      </div>
    </header>
  );
}
