'use client';

import { useCallback, useEffect, useState } from 'react';
import { useTranslations } from 'next-intl';
import { useAuth } from '@/lib/auth-context';
import { apiAuth } from '@/lib/api';
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

const SECTIONS = ['account', 'apiKeys', 'privacy', 'security', 'support', 'danger'] as const;
type SectionId = typeof SECTIONS[number];
const SECURITY_2FA_ENABLED = process.env.NEXT_PUBLIC_KURA_SECURITY_2FA_ENABLED === 'true';

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
  const [deletePassword, setDeletePassword] = useState('');
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [activeSection, setActiveSection] = useState<SectionId>('account');

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
    if (!token || deleteConfirm !== user.email || !deletePassword) return;
    setDeleteError(null);
    setDeleting(true);
    try {
      const res = await apiAuth('/v1/account', token, {
        method: 'DELETE',
        body: JSON.stringify({
          password: deletePassword,
          confirm_email: user.email,
        }),
      });
      if (res.ok) {
        setDeletePassword('');
        logout();
        router.push('/login');
      } else {
        const body = await res.json().catch(() => null);
        setDeleteError(body?.message || body?.error || t('danger.deleteError'));
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

  const sidebarLabels: Record<SectionId, string> = {
    account: t('account.title'),
    apiKeys: t('apiKeys.title'),
    privacy: t('privacy.title'),
    security: t('security.title'),
    support: t('support.title'),
    danger: t('danger.title'),
  };

  return (
    <div className={styles.settingsPage}>
      <div className={styles.container}>
        <h1 className={styles.pageTitle}>{t('title')}</h1>

        <div className={styles.layout}>
          {/* Sidebar Navigation */}
          <nav className={styles.sidebar}>
            {SECTIONS.map((id) => (
              <button
                key={id}
                className={`${styles.sidebarLink} ${activeSection === id ? styles.sidebarLinkActive : ''}`}
                onClick={() => setActiveSection(id)}
                data-testid={`settings-nav-${id}`}
              >
                {sidebarLabels[id]}
              </button>
            ))}
          </nav>

          {/* Content — only active section is shown */}
          <div className={styles.content}>

            {/* === Account === */}
            {activeSection === 'account' && (
              <section className="kura-card">
                <h2 className={styles.sectionTitle}>{t('account.title')}</h2>
                <div className={styles.settingRow}>
                  <span className={styles.settingLabel}>{t('account.email')}</span>
                  <span className={styles.settingValue}>{user.email}</span>
                </div>
                <div className={styles.settingRow}>
                  <span className={styles.settingLabel}>{t('account.memberSince')}</span>
                  <span className={styles.settingValue}>{formatDate(user.created_at)}</span>
                </div>
                <div className={styles.settingRow}>
                  <span className={styles.settingLabel}>{t('account.plan')}</span>
                  <span className="kura-badge">{t('account.free')}</span>
                </div>
              </section>
            )}

            {/* === API Keys === */}
            {activeSection === 'apiKeys' && (
              <section className="kura-card">
                <div className={styles.sectionHeader}>
                  <div>
                    <h2 className={styles.sectionTitle}>{t('apiKeys.title')}</h2>
                    <p className={styles.sectionDescription}>{t('apiKeys.description')}</p>
                  </div>
                </div>

                {/* Newly created key alert */}
                {newlyCreatedKey && (
                  <div className={styles.keyAlert}>
                    <p className={styles.keyAlertTitle}>{t('apiKeys.copyWarning')}</p>
                    <div className={styles.keyAlertRow}>
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
                    <div className={styles.keyAlertActions}>
                      <button
                        className="kura-btn kura-btn--ghost"
                        onClick={() => setNewlyCreatedKey(null)}
                      >
                        {tc('close')}
                      </button>
                    </div>
                  </div>
                )}

                {/* Create form */}
                {showCreateForm && (
                  <div className={styles.createForm}>
                    <div className={styles.createFormField}>
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

                {/* Empty state */}
                {activeKeys.length === 0 && !showCreateForm && !newlyCreatedKey && (
                  <p className={styles.emptyState}>
                    {t('apiKeys.noKeys')}
                  </p>
                )}

                {/* Create button at bottom */}
                {!showCreateForm && !newlyCreatedKey && (
                  <div className={styles.divider}>
                    <button
                      className="kura-btn kura-btn--secondary"
                      onClick={() => setShowCreateForm(true)}
                    >
                      {t('apiKeys.create')}
                    </button>
                  </div>
                )}
              </section>
            )}

            {/* === Privacy & Data === */}
            {activeSection === 'privacy' && (
              <section className="kura-card">
                <h2 className={styles.sectionTitle}>{t('privacy.title')}</h2>

                <div className={styles.settingRow}>
                  <div className={styles.settingInfo}>
                    <span className={styles.settingName}>{t('privacy.consentLabel')}</span>
                    <p className={styles.settingHint}>{t('privacy.consentHint')}</p>
                  </div>
                  <button
                    className="kura-toggle"
                    role="switch"
                    aria-checked="true"
                    disabled
                  />
                </div>

                <div className={styles.settingRow}>
                  <div className={styles.settingInfo}>
                    <span className={styles.settingName}>{t('privacy.exportDescription')}</span>
                  </div>
                  <button className="kura-btn kura-btn--secondary" onClick={handleExportData}>
                    {t('privacy.exportButton')}
                  </button>
                </div>
              </section>
            )}

            {/* === Security === */}
            {activeSection === 'security' && (
              <section className="kura-card">
                <h2 className={styles.sectionTitle}>{t('security.title')}</h2>
                <div className={styles.settingRow}>
                  <div className={styles.settingInfo}>
                    <span className={styles.settingName}>{t('security.twoFactor')}</span>
                    <p className={styles.settingHint}>
                      {SECURITY_2FA_ENABLED ? t('security.enabledHint') : t('security.unavailableHint')}
                    </p>
                  </div>
                  <span
                    className={`kura-badge ${SECURITY_2FA_ENABLED ? 'kura-badge--success' : ''}`}
                    data-testid="settings-security-state"
                    data-security-state={SECURITY_2FA_ENABLED ? 'enabled' : 'unavailable'}
                  >
                    {SECURITY_2FA_ENABLED ? t('security.enabled') : t('security.comingSoon')}
                  </span>
                </div>
              </section>
            )}

            {/* === Support === */}
            {activeSection === 'support' && (
              <section className="kura-card">
                <h2 className={styles.sectionTitle}>{t('support.title')}</h2>
                <p className={styles.sectionDescription}>
                  {t('support.text')}
                </p>
              </section>
            )}

            {/* === Danger Zone === */}
            {activeSection === 'danger' && (
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
                  <div className={styles.field}>
                    <label htmlFor="deletePassword" className="kura-label">
                      {t('danger.deletePassword')}
                    </label>
                    <input
                      id="deletePassword"
                      type="password"
                      className="kura-input"
                      value={deletePassword}
                      onChange={(e) => setDeletePassword(e.target.value)}
                    />
                  </div>
                  {deleteError && <p className={styles.settingHint}>{deleteError}</p>}
                  <button
                    className="kura-btn kura-btn--danger"
                    disabled={deleteConfirm !== user.email || !deletePassword || deleting}
                    onClick={handleDeleteAccount}
                  >
                    {deleting ? tc('loading') : t('danger.deleteButton')}
                  </button>
                </div>
              </section>
            )}

          </div>
        </div>
      </div>
    </div>
  );
}
