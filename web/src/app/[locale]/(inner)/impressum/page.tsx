import { useTranslations } from 'next-intl';
import styles from './impressum.module.css';

export default function ImpressumPage() {
  const t = useTranslations('impressum');

  return (
    <div className={styles.page}>
      <div className={`kura-container ${styles.container}`}>
        <h1 className={styles.title}>{t('title')}</h1>

        <section className={styles.section}>
          <h2 className={styles.sectionTitle}>{t('tmg')}</h2>
          <div className={styles.field}>
            <span className={styles.fieldLabel}>{t('name')}</span>
            <span className={styles.fieldValue}>{t('namePlaceholder')}</span>
          </div>
          <div className={styles.field}>
            <span className={styles.fieldLabel}>{t('address')}</span>
            <span className={styles.fieldValue}>{t('addressPlaceholder')}</span>
          </div>
          <div className={styles.field}>
            <span className={styles.fieldLabel}>{t('contact')}</span>
            <span className={styles.fieldValue}>{t('contactPlaceholder')}</span>
          </div>
        </section>

        <section className={styles.section}>
          <h2 className={styles.sectionTitle}>{t('responsible')}</h2>
          <p className={styles.fieldValue}>{t('responsiblePlaceholder')}</p>
        </section>

        <p className={styles.placeholder}>{t('note')}</p>
      </div>
    </div>
  );
}
