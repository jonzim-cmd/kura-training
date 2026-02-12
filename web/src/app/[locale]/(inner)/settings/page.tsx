'use client';

import { useState } from 'react';
import { useTranslations } from 'next-intl';
import styles from './settings.module.css';

export default function SettingsPage() {
  const t = useTranslations('settings');
  const [deleteConfirm, setDeleteConfirm] = useState('');

  return (
    <div className={styles.settingsPage}>
      <div className={`kura-container ${styles.container}`}>
        <div className={styles.header}>
          <h1 className={styles.title}>{t('title')}</h1>
          <p className={styles.subtitle}>{t('subtitle')}</p>
        </div>

        {/* Account */}
        <section className="kura-card">
          <h2 className={styles.sectionTitle}>{t('account.title')}</h2>
          <div className={styles.infoGrid}>
            <div className={styles.infoRow}>
              <span className={styles.infoLabel}>{t('account.email')}</span>
              <span className={styles.infoValue}>user@example.com</span>
            </div>
            <div className={styles.infoRow}>
              <span className={styles.infoLabel}>{t('account.memberSince')}</span>
              <span className={styles.infoValue}>2026-02-01</span>
            </div>
            <div className={styles.infoRow}>
              <span className={styles.infoLabel}>{t('account.plan')}</span>
              <span className="kura-badge">{t('account.free')}</span>
            </div>
          </div>
        </section>

        {/* API Keys */}
        <section className="kura-card">
          <div className={styles.sectionHeader}>
            <div>
              <h2 className={styles.sectionTitle}>{t('apiKeys.title')}</h2>
              <p className={styles.sectionDescription}>{t('apiKeys.description')}</p>
            </div>
            <button className="kura-btn kura-btn--primary">
              {t('apiKeys.create')}
            </button>
          </div>

          <div className={styles.keyTable}>
            <div className={styles.keyHeader}>
              <span>{t('apiKeys.name')}</span>
              <span>{t('apiKeys.key')}</span>
              <span>{t('apiKeys.created')}</span>
              <span>{t('apiKeys.lastUsed')}</span>
              <span></span>
            </div>
            {/* Example row */}
            <div className={styles.keyRow}>
              <span className={styles.keyName}>Claude Desktop</span>
              <span className={styles.keyValue}>
                <code className="kura-code-inline">kura_sk_...a3f8</code>
              </span>
              <span className={styles.keyDate}>2026-02-10</span>
              <span className={styles.keyDate}>2026-02-12</span>
              <button className="kura-btn kura-btn--ghost">{t('apiKeys.revoke')}</button>
            </div>
          </div>
        </section>

        {/* Danger Zone */}
        <section className="kura-card kura-card--danger">
          <h2 className={styles.sectionTitle}>{t('danger.title')}</h2>
          <p className={styles.dangerDescription}>{t('danger.deleteDescription')}</p>
          <div className={styles.dangerAction}>
            <div className={styles.field}>
              <label htmlFor="deleteConfirm" className="kura-label">
                {t('danger.deleteConfirm')}
              </label>
              <input
                id="deleteConfirm"
                type="email"
                className="kura-input"
                value={deleteConfirm}
                onChange={(e) => setDeleteConfirm(e.target.value)}
              />
            </div>
            <button
              className="kura-btn kura-btn--danger"
              disabled={deleteConfirm !== 'user@example.com'}
            >
              {t('danger.deleteButton')}
            </button>
          </div>
        </section>
      </div>
    </div>
  );
}
