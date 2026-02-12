'use client';

import { useTranslations } from 'next-intl';
import { Link } from '@/i18n/routing';
import styles from './Header.module.css';

export function Header() {
  const t = useTranslations('nav');

  return (
    <header className={styles.header}>
      <div className={styles.inner}>
        <Link href="/" className={styles.logo}>KURA</Link>
        <nav className={styles.nav}>
          <Link href="/setup" className={styles.link}>{t('setup')}</Link>
          <Link href="/login" className={styles.link}>{t('login')}</Link>
          <Link href="/signup" className={styles.link}>{t('signup')}</Link>
        </nav>
      </div>
    </header>
  );
}
