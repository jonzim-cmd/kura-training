import { useTranslations } from 'next-intl';
import styles from './impressum.module.css';

const CONTACT_INFO = {
  name: 'Jonas Zimmermann',
  street: 'Faeustlestr. 3',
  city: '80339 Muenchen',
  email: 'jonas.zimmermann@withkura.com',
};

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
            <span className={styles.fieldValue}>{CONTACT_INFO.name}</span>
          </div>
          <div className={styles.field}>
            <span className={styles.fieldLabel}>{t('address')}</span>
            <span className={styles.fieldValue}>
              {CONTACT_INFO.street}<br />
              {CONTACT_INFO.city}
            </span>
          </div>
          <div className={styles.field}>
            <span className={styles.fieldLabel}>{t('contact')}</span>
            <span className={styles.fieldValue}>
              {t('contactPrefix')}
              <a href={`mailto:${CONTACT_INFO.email}`}>{CONTACT_INFO.email}</a>
            </span>
          </div>
        </section>

        <section className={styles.section}>
          <h2 className={styles.sectionTitle}>{t('responsible')}</h2>
          <p className={styles.fieldValue}>{CONTACT_INFO.name}</p>
        </section>

        <section className={styles.section}>
          <h2 className={styles.sectionTitle}>{t('liability.title')}</h2>
          <p className={styles.text}>{t('liability.text')}</p>
        </section>

        <section className={styles.section}>
          <h2 className={styles.sectionTitle}>{t('liabilityLinks.title')}</h2>
          <p className={styles.text}>{t('liabilityLinks.text')}</p>
        </section>

        <section className={styles.section}>
          <h2 className={styles.sectionTitle}>{t('copyright.title')}</h2>
          <p className={styles.text}>{t('copyright.text')}</p>
        </section>
      </div>
    </div>
  );
}
