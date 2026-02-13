import { useTranslations } from 'next-intl';
import styles from './nutzungsbedingungen.module.css';

export default function NutzungsbedingungenPage() {
  const t = useTranslations('nutzungsbedingungen');

  return (
    <div className={styles.page}>
      <div className={`kura-container ${styles.container}`}>
        <h1 className={styles.title}>{t('title')}</h1>

        <section className={styles.section}>
          <h2 className={styles.sectionTitle}>{t('earlyAccess.title')}</h2>
          <p className={styles.text}>{t('earlyAccess.text')}</p>
        </section>

        <section className={styles.section}>
          <h2 className={styles.sectionTitle}>{t('dataConsent.title')}</h2>
          <p className={styles.text}>{t('dataConsent.text')}</p>
        </section>

        <section className={styles.section}>
          <h2 className={styles.sectionTitle}>{t('liability.title')}</h2>
          <p className={styles.text}>{t('liability.text')}</p>
        </section>

        <section className={styles.section}>
          <h2 className={styles.sectionTitle}>{t('changes.title')}</h2>
          <p className={styles.text}>{t('changes.text')}</p>
        </section>

        <p className={styles.placeholder}>{t('note')}</p>
      </div>
    </div>
  );
}
