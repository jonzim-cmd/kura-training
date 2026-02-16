'use client';

import { useEffect, useState } from 'react';
import { useTranslations } from 'next-intl';
import styles from './impressum.module.css';

// Bot protection: personal data assembled client-side from char codes.
// Not in SSR output, not in translation files, not plain-text in source.
const _c = [
  [74,111,110,97,115],
  [90,105,109,109,101,114,109,97,110,110],
  [70,228,117,115,116,108,101,115,116,114,46,32,51],
  [56,48,51,51,57,32,77,252,110,99,104,101,110],
  [106,122,105,109,109,101,114,109,97,110,110,46,100,101,118],
  [103,109,97,105,108,46,99,111,109],
];
function _d(i: number): string {
  return String.fromCharCode(..._c[i]);
}

interface ContactInfo {
  name: string;
  street: string;
  city: string;
  email: string;
}

export default function ImpressumPage() {
  const t = useTranslations('impressum');
  const [info, setInfo] = useState<ContactInfo | null>(null);

  useEffect(() => {
    setInfo({
      name: `${_d(0)} ${_d(1)}`,
      street: _d(2),
      city: _d(3),
      email: `${_d(4)}@${_d(5)}`,
    });
  }, []);

  return (
    <div className={styles.page}>
      <div className={`kura-container ${styles.container}`}>
        <h1 className={styles.title}>{t('title')}</h1>

        <section className={styles.section}>
          <h2 className={styles.sectionTitle}>{t('tmg')}</h2>
          {info ? (
            <>
              <div className={styles.field}>
                <span className={styles.fieldLabel}>{t('name')}</span>
                <span className={styles.fieldValue}>{info.name}</span>
              </div>
              <div className={styles.field}>
                <span className={styles.fieldLabel}>{t('address')}</span>
                <span className={styles.fieldValue}>
                  {info.street}<br />
                  {info.city}
                </span>
              </div>
              <div className={styles.field}>
                <span className={styles.fieldLabel}>{t('contact')}</span>
                <span className={styles.fieldValue}>{t('contactPrefix')}{info.email}</span>
              </div>
            </>
          ) : (
            <span className={styles.fieldValue} aria-hidden="true">&hellip;</span>
          )}
        </section>

        <section className={styles.section}>
          <h2 className={styles.sectionTitle}>{t('responsible')}</h2>
          <p className={styles.fieldValue}>{info ? info.name : '\u2026'}</p>
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
