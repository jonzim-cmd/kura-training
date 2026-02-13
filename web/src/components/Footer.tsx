import { useTranslations } from 'next-intl';
import { Link } from '@/i18n/routing';
import styles from './Footer.module.css';

export function Footer() {
  const t = useTranslations('footer');

  return (
    <footer className={styles.footer}>
      <div className={styles.inner}>
        <nav className={styles.links}>
          <Link href="/impressum" className={styles.link}>{t('impressum')}</Link>
          <Link href="/datenschutz" className={styles.link}>{t('datenschutz')}</Link>
          <Link href="/nutzungsbedingungen" className={styles.link}>{t('nutzungsbedingungen')}</Link>
        </nav>
        <span className={styles.copyright}>{t('copyright')}</span>
      </div>
    </footer>
  );
}
