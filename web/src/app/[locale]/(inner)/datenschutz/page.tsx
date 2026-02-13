import { useTranslations } from 'next-intl';
import styles from './datenschutz.module.css';

export default function DatenschutzPage() {
  const t = useTranslations('datenschutz');

  return (
    <div className={styles.page}>
      <div className={`kura-container ${styles.container}`}>
        <h1 className={styles.title}>{t('title')}</h1>

        <section className={styles.section}>
          <h2 className={styles.sectionTitle}>{t('controller.title')}</h2>
          <p className={styles.text}>{t('controller.text')}</p>
        </section>

        <section className={styles.section}>
          <h2 className={styles.sectionTitle}>{t('dataCollected.title')}</h2>
          <p className={styles.text}>{t('dataCollected.text')}</p>
        </section>

        <section className={styles.section}>
          <h2 className={styles.sectionTitle}>{t('purpose.title')}</h2>
          <p className={styles.text}>{t('purpose.text')}</p>
        </section>

        <section className={styles.section}>
          <h2 className={styles.sectionTitle}>{t('rights.title')}</h2>
          <p className={styles.text}>{t('rights.text')}</p>
        </section>

        <section className={styles.section}>
          <h2 className={styles.sectionTitle}>{t('contactSection.title')}</h2>
          <p className={styles.text}>{t('contactSection.text')}</p>
        </section>

        <p className={styles.placeholder}>{t('note')}</p>
      </div>
    </div>
  );
}
