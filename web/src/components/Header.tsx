'use client';

import { useTranslations } from 'next-intl';
import { Link } from '@/i18n/routing';
import { useState, useEffect, useRef } from 'react';
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
  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useRef<HTMLElement>(null);

  // Close menu on outside click
  useEffect(() => {
    if (!menuOpen) return;
    const handler = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        setMenuOpen(false);
      }
    };
    document.addEventListener('click', handler);
    return () => document.removeEventListener('click', handler);
  }, [menuOpen]);

  // Close menu on route change (link click)
  const closeMenu = () => setMenuOpen(false);

  const navLinks = isLoggedIn ? (
    <>
      <Link href="/setup" className={styles.link} onClick={closeMenu}>{t('setup')}</Link>
      <Link href="/settings" className={styles.link} onClick={closeMenu}>{t('settings')}</Link>
      <button
        onClick={() => { auth?.logout(); closeMenu(); }}
        className={styles.link}
        style={{ background: 'none', border: 'none', cursor: 'pointer' }}
      >
        {t('logout')}
      </button>
    </>
  ) : (
    <>
      {variant !== 'landing' && (
        <Link href="/start" className={styles.link} onClick={closeMenu}>{t('home')}</Link>
      )}
      <Link href="/setup" className={styles.link} onClick={closeMenu}>{t('setup')}</Link>
      <Link href="/login" className={styles.link} onClick={closeMenu}>{t('login')}</Link>
      <Link href="/request-access" className={styles.link} onClick={closeMenu}>{t('requestAccess')}</Link>
    </>
  );

  return (
    <header className={`${styles.header} ${variant === 'landing' ? styles.landing : ''}`} ref={menuRef}>
      <div className={`${styles.inner} ${variant === 'landing' ? styles.innerLanding : ''}`}>
        {variant !== 'landing' && (
          <Link href="/" className={styles.logo}>KU<span style={{letterSpacing: '0.06em'}}>R</span>A</Link>
        )}
        <nav className={styles.nav}>
          {navLinks}
        </nav>
        <button
          className={styles.burger}
          onClick={() => setMenuOpen((v) => !v)}
          aria-label="Menu"
          aria-expanded={menuOpen}
        >
          <span className={`${styles.burgerLine} ${menuOpen ? styles.burgerOpen : ''}`} />
          <span className={`${styles.burgerLine} ${menuOpen ? styles.burgerOpen : ''}`} />
          <span className={`${styles.burgerLine} ${menuOpen ? styles.burgerOpen : ''}`} />
        </button>
      </div>
      {menuOpen && (
        <div className={styles.dropdown}>
          {navLinks}
        </div>
      )}
    </header>
  );
}
