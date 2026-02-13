'use client';

import { useCallback, useEffect, useState } from 'react';
import { useTranslations } from 'next-intl';
import { useAuth } from '@/lib/auth-context';
import { apiAuth, apiFetch } from '@/lib/api';
import { useRouter } from '@/i18n/routing';
import styles from './settings.module.css';

interface ApiKeyInfo {
  id: string;
  label: string;
  key_prefix: string;
  scopes: string[];
  created_at: string;
  last_used_at: string | null;
  is_revoked: boolean;
}

export default function SettingsPage() {
  const t = useTranslations('settings');
  const tc = useTranslations('common');
  const { user, token, loading, logout } = useAuth();
  const router = useRouter();

  const [keys, setKeys] = useState<ApiKeyInfo[]>([]);
  const [newKeyLabel, setNewKeyLabel] = useState('');
  const [showCreateForm, setShowCreateForm] = useState(false);
  const [newlyCreatedKey, setNewlyCreatedKey] = useState<string | null>(null);
  const [deleteConfirm, setDeleteConfirm] = useState('');
  const [deleting, setDeleting] = useState(false);
  const [copied, setCopied] = useState(false);

  // Redirect if not logged in
  useEffect(() => {
    if (!loading && !user) {
      router.replace('/login');
    }
  }, [loading, user, router]);

  // Fetch API keys
  const fetchKeys = useCallback(async () => {
    if (!token) return;
    try {
      const res = await apiAuth('/v1/account/api-keys', token);
      if (res.ok) {
        const data = await res.json();
        setKeys(data.keys);
      }
    } catch { /* silently fail */ }
  }, [token]);

  useEffect(() => { fetchKeys(); }, [fetchKeys]);

  if (loading || !user) return null;

  const handleCreateKey = async () => {
    if (!token || !newKeyLabel.trim()) return;
    const res = await apiAuth('/v1/account/api-keys', token, {
      method: 'POST',
      body: JSON.stringify({ label: newKeyLabel.trim() }),
    });
    if (res.ok) {
      const data = await res.json();
      setNewlyCreatedKey(data.key);
      setNewKeyLabel('');
      setShowCreateForm(false);
      await fetchKeys();
    }
  };

  const handleRevokeKey = async (keyId: string) => {
    if (!token) return;
    const res = await apiAuth(`/v1/account/api-keys/${keyId}`, token, {
      method: 'DELETE',
    });
    if (res.ok) await fetchKeys();
  };

  const handleCopyKey = async (key: string) => {
    await navigator.clipboard.writeText(key);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const handleDeleteAccount = async () => {
    if (!token || deleteConfirm !== user.email) return;
    setDeleting(true);
    try {
      const res = await apiAuth('/v1/account', token, { method: 'DELETE' });
      if (res.ok) {
        logout();
        router.push('/');
      }
    } finally {
      setDeleting(false);
    }
  };

  const handleExportData = async () => {
    if (!token) return;
    try {
      const [eventsRes, projectionsRes] = await Promise.all([
        apiAuth('/v1/events?limit=10000', token),
        apiAuth('/v1/projections', token),
      ]);
      const events = eventsRes.ok ? await eventsRes.json() : [];
      const projections = projectionsRes.ok ? await projectionsRes.json() : [];
      const exportData = {
        exported_at: new Date().toISOString(),
        events: events.events ?? events,
        projections,
      };
      const blob = new Blob([JSON.stringify(exportData, null, 2)], { type: 'application/json' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `kura-export-${new Date().toISOString().split('T')[0]}.json`;
      a.click();
      URL.revokeObjectURL(url);
    } catch { /* silently fail */ }
  };

  const formatDate = (iso: string) => new Date(iso).toLocaleDateString();
  const activeKeys = keys.filter(k => !k.is_revoked);
  const revokedKeys = keys.filter(k => k.is_revoked);

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
              <span className={styles.infoValue}>{user.email}</span>
            </div>
            <div className={styles.infoRow}>
              <span className={styles.infoLabel}>{t('account.memberSince')}</span>
              <span className={styles.infoValue}>{formatDate(user.created_at)}</span>
            </div>
            <div className={styles.infoRow}>
              <span className={styles.infoLabel}>{t('account.plan')}</span>
              <span className="kura-badge">{t('account.free')}</span>
            </div>
          </div>
        </section>

        {/* API Keys */}
        <section className="kura-card" id="api-keys">
          <div className={styles.sectionHeader}>
            <div>
              <h2 className={styles.sectionTitle}>{t('apiKeys.title')}</h2>
              <p className={styles.sectionDescription}>{t('apiKeys.description')}</p>
            </div>
            {!showCreateForm && !newlyCreatedKey && (
              <button
                className="kura-btn kura-btn--primary"
                onClick={() => setShowCreateForm(true)}
              >
                {t('apiKeys.create')}
              </button>
            )}
          </div>

          {/* Newly created key (one-time display) */}
          {newlyCreatedKey && (
            <div className="kura-card" style={{ background: 'var(--surface)', marginBottom: '1rem' }}>
              <p style={{ fontWeight: 500, marginBottom: '0.5rem' }}>{t('apiKeys.copyWarning')}</p>
              <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                <code className="kura-code-inline" style={{ flex: 1, wordBreak: 'break-all' }}>
                  {newlyCreatedKey}
                </code>
                <button
                  className="kura-btn kura-btn--ghost"
                  onClick={() => handleCopyKey(newlyCreatedKey)}
                >
                  {copied ? tc('copied') : 'Copy'}
                </button>
              </div>
              <button
                className="kura-btn kura-btn--ghost"
                style={{ marginTop: '0.5rem' }}
                onClick={() => setNewlyCreatedKey(null)}
              >
                {tc('close')}
              </button>
            </div>
          )}

          {/* Create form */}
          {showCreateForm && (
            <div style={{ display: 'flex', gap: '0.5rem', marginBottom: '1rem', alignItems: 'flex-end' }}>
              <div className={styles.field} style={{ flex: 1 }}>
                <label className="kura-label">{t('apiKeys.name')}</label>
                <input
                  className="kura-input"
                  placeholder="Claude Desktop"
                  value={newKeyLabel}
                  onChange={(e) => setNewKeyLabel(e.target.value)}
                  onKeyDown={(e) => e.key === 'Enter' && handleCreateKey()}
                />
              </div>
              <button className="kura-btn kura-btn--primary" onClick={handleCreateKey}>
                {t('apiKeys.create')}
              </button>
              <button className="kura-btn kura-btn--ghost" onClick={() => setShowCreateForm(false)}>
                {tc('cancel')}
              </button>
            </div>
          )}

          {/* Key table */}
          {activeKeys.length > 0 && (
            <div className={styles.keyTable}>
              <div className={styles.keyHeader}>
                <span>{t('apiKeys.name')}</span>
                <span>{t('apiKeys.key')}</span>
                <span>{t('apiKeys.created')}</span>
                <span>{t('apiKeys.lastUsed')}</span>
                <span></span>
              </div>
              {activeKeys.map((key) => (
                <div key={key.id} className={styles.keyRow}>
                  <span className={styles.keyName}>{key.label}</span>
                  <span className={styles.keyValue}>
                    <code className="kura-code-inline">{key.key_prefix}</code>
                  </span>
                  <span className={styles.keyDate}>{formatDate(key.created_at)}</span>
                  <span className={styles.keyDate}>
                    {key.last_used_at ? formatDate(key.last_used_at) : t('apiKeys.never')}
                  </span>
                  <button
                    className="kura-btn kura-btn--ghost"
                    onClick={() => handleRevokeKey(key.id)}
                  >
                    {t('apiKeys.revoke')}
                  </button>
                </div>
              ))}
            </div>
          )}

          {activeKeys.length === 0 && !showCreateForm && !newlyCreatedKey && (
            <p style={{ color: 'var(--ink-faint)', fontSize: '0.875rem' }}>
              {t('apiKeys.noKeys')}
            </p>
          )}
        </section>

        {/* Privacy & Data */}
        <section className="kura-card">
          <h2 className={styles.sectionTitle}>{t('privacy.title')}</h2>

          {/* Consent toggle (disabled during EA) */}
          <div className={styles.infoRow} style={{ marginBottom: '0.75rem' }}>
            <div>
              <span style={{ fontWeight: 500, fontSize: '0.875rem' }}>{t('privacy.consentLabel')}</span>
              <p style={{ color: 'var(--ink-faint)', fontSize: '0.8125rem', marginTop: '0.25rem' }}>
                {t('privacy.consentHint')}
              </p>
            </div>
            <input type="checkbox" checked disabled style={{ opacity: 0.5 }} />
          </div>

          {/* Data export */}
          <div style={{ borderTop: '1px solid var(--thread)', paddingTop: '0.75rem' }}>
            <p style={{ fontSize: '0.875rem', marginBottom: '0.5rem' }}>{t('privacy.exportDescription')}</p>
            <button className="kura-btn kura-btn--ghost" onClick={handleExportData}>
              {t('privacy.exportButton')}
            </button>
          </div>
        </section>

        {/* Security (placeholder) */}
        <section className="kura-card">
          <h2 className={styles.sectionTitle}>{t('security.title')}</h2>
          <div className={styles.infoRow}>
            <div>
              <span style={{ fontWeight: 500, fontSize: '0.875rem' }}>{t('security.twoFactor')}</span>
            </div>
            <span className="kura-badge">{t('security.comingSoon')}</span>
          </div>
        </section>

        {/* Support */}
        <section className="kura-card">
          <h2 className={styles.sectionTitle}>{t('support.title')}</h2>
          <p style={{ fontSize: '0.875rem', color: 'var(--ink-faint)' }}>
            {t('support.text')}
          </p>
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
              disabled={deleteConfirm !== user.email || deleting}
              onClick={handleDeleteAccount}
            >
              {deleting ? tc('loading') : t('danger.deleteButton')}
            </button>
          </div>
        </section>
      </div>
    </div>
  );
}
