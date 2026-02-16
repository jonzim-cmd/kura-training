'use client';

import { useEffect, useState } from 'react';
import { useTranslations } from 'next-intl';
import { Link } from '@/i18n/routing';
import { useAuth } from '@/lib/auth-context';
import { API_URL } from '@/lib/api';
import { SETUP_SEEN_STORAGE_KEY } from '@/lib/onboarding';
import styles from './setup.module.css';

type AI = 'claude' | 'chatgpt' | 'gemini';
type Protocol = 'cli' | 'mcp';
const API_KEY_SETTINGS_PATH = '/settings#api-keys';
const MCP_SERVER_NAME = 'Kura';
const CONFIGURED_MCP_URL = process.env.NEXT_PUBLIC_KURA_MCP_URL?.trim() ?? '';
const FALLBACK_MCP_URL = 'https://api.withkura.com/mcp';
const OAUTH_CONNECT_URL = `${API_URL}/v1/auth/device/verify`;
const CLI_API_KEY_FALLBACK = 'export KURA_API_KEY=<kura_sk_...>';

const STEP_KEYS = ['install', 'login', 'configure'] as const;
type CliStep = (typeof STEP_KEYS)[number];

const CLI_COMMANDS: Record<AI, Record<CliStep, string>> = {
  claude: {
    install: 'cargo install kura-cli',
    login: 'kura login',
    configure: `# Add to your CLAUDE.md or project config:
# Tool: kura CLI at /path/to/kura
# Auth: handled by kura login (OAuth)`,
  },
  chatgpt: {
    install: 'cargo install kura-cli',
    login: 'kura login',
    configure: `# In ChatGPT settings, add a custom tool:
# Name: Kura
# Command: kura
# Auth: reuse your local kura login session`,
  },
  gemini: {
    install: 'cargo install kura-cli',
    login: 'kura login',
    configure: `# In Gemini workspace, add Kura tool:
# Binary: /path/to/kura
# Auth: reuse your local kura login session`,
  },
};

function getMcpOauthSnippet(ai: AI, mcpUrl: string, oauthConnectUrl: string) {
  if (ai === 'chatgpt') {
    return `# ChatGPT MCP setup (OAuth)
Name: Kura
URL: ${mcpUrl}
Auth: OAuth (Connect with Kura)
If prompted for device verification, open: ${oauthConnectUrl}`;
  }
  if (ai === 'claude') {
    return `# Claude MCP setup (OAuth)
Name: Kura
URL: ${mcpUrl}
Auth: OAuth (Connect with Kura)
If prompted for device verification, open: ${oauthConnectUrl}`;
  }
  return `# Gemini / Other MCP setup (OAuth)
Name: Kura
URL: ${mcpUrl}
Auth: OAuth (Connect with Kura)
If prompted for device verification, open: ${oauthConnectUrl}`;
}

function getMcpApiKeyFallbackSnippet(mcpUrl: string) {
  return `{
  "mcpServers": {
    "kura": {
      "url": "${mcpUrl}",
      "headers": {
        "Authorization": "Bearer <KURA_API_KEY>"
      }
    }
  }
}`;
}

export default function SetupPage() {
  const t = useTranslations('setup');
  const tn = useTranslations('nav');
  const tc = useTranslations('common');
  const [selectedAI, setSelectedAI] = useState<AI>('claude');
  const [selectedProtocol, setSelectedProtocol] = useState<Protocol>('cli');
  const [copiedId, setCopiedId] = useState<string | null>(null);
  const { user, loading } = useAuth();
  const isLoggedIn = !loading && Boolean(user);
  const isMcpLive = CONFIGURED_MCP_URL.length > 0;
  const mcpUrl = isMcpLive ? CONFIGURED_MCP_URL : FALLBACK_MCP_URL;
  const mcpOauthSnippet = getMcpOauthSnippet(selectedAI, mcpUrl, OAUTH_CONNECT_URL);
  const mcpApiKeyFallbackSnippet = getMcpApiKeyFallbackSnippet(mcpUrl);

  const markCopied = (id: string) => {
    setCopiedId(id);
    window.setTimeout(() => setCopiedId(null), 1800);
  };

  const copyText = async (value: string, id: string) => {
    try {
      await navigator.clipboard.writeText(value);
      markCopied(id);
    } catch {
      const textarea = document.createElement('textarea');
      textarea.value = value;
      textarea.style.position = 'fixed';
      textarea.style.opacity = '0';
      document.body.appendChild(textarea);
      textarea.focus();
      textarea.select();
      document.execCommand('copy');
      document.body.removeChild(textarea);
      markCopied(id);
    }
  };

  const copyLabel = (id: string) => (copiedId === id ? tc('copied') : tc('copy'));

  useEffect(() => {
    if (!loading && user) {
      localStorage.setItem(SETUP_SEEN_STORAGE_KEY, '1');
    }
  }, [loading, user]);

  const renderSetupActions = () => {
    if (isLoggedIn) {
      return (
        <>
          <a
            href={OAUTH_CONNECT_URL}
            target="_blank"
            rel="noreferrer"
            className="kura-btn kura-btn--primary"
          >
            {t('accountGate.connectButton')}
          </a>
          <Link href={API_KEY_SETTINGS_PATH} className="kura-btn kura-btn--ghost">
            {tn('settings')}
          </Link>
        </>
      );
    }

    return (
      <>
        <Link href="/login" className="kura-btn kura-btn--primary">
          {tn('login')}
        </Link>
        <Link href="/request-access" className="kura-btn kura-btn--ghost">
          {tn('requestAccess')}
        </Link>
        <Link href={API_KEY_SETTINGS_PATH} className="kura-btn kura-btn--ghost">
          {tn('settings')}
        </Link>
      </>
    );
  };

  return (
    <div className={styles.setupPage}>
      <div className={`kura-container ${styles.container}`}>
        <div className={styles.header}>
          <h1 className={styles.title}>{t('title')}</h1>
          <p className={styles.subtitle}>{t('subtitle')}</p>
        </div>

        <div className={`kura-card ${styles.accountCard}`}>
          <h2 className={styles.accountTitle}>{t('accountGate.title')}</h2>
          <p
            className={styles.accountDescription}
            data-testid="setup-account-description"
            data-auth-state={isLoggedIn ? 'logged-in' : 'logged-out'}
          >
            {isLoggedIn ? t('accountGate.loggedInDescription') : t('accountGate.loggedOutDescription')}
          </p>
          <div className={styles.accountActions}>{renderSetupActions()}</div>
        </div>

        {/* AI Selector */}
        <div className={styles.aiSelector}>
          {(['claude', 'chatgpt', 'gemini'] as const).map((ai) => (
            <button
              key={ai}
              className={`${styles.aiTab} ${selectedAI === ai ? styles.aiTabActive : ''}`}
              onClick={() => setSelectedAI(ai)}
            >
              {t(`${ai}.title`)}
            </button>
          ))}
        </div>

        {/* Protocol Tabs */}
        <div className={styles.protocolTabs}>
          <button
            className={`${styles.protocolTab} ${selectedProtocol === 'cli' ? styles.protocolTabActive : ''}`}
            onClick={() => setSelectedProtocol('cli')}
          >
            {t('cliTab')}
            <span className="kura-badge kura-badge--success" style={{ marginLeft: '0.5rem' }}>
              {t('ready')}
            </span>
          </button>
          <button
            className={`${styles.protocolTab} ${selectedProtocol === 'mcp' ? styles.protocolTabActive : ''}`}
            onClick={() => setSelectedProtocol('mcp')}
          >
            {t('mcpTab')}
            <span
              className={`kura-badge ${isMcpLive ? 'kura-badge--success' : ''}`}
              style={{ marginLeft: '0.5rem' }}
            >
              {isMcpLive ? t('ready') : t('comingSoon')}
            </span>
          </button>
        </div>

        {/* Content */}
        <div className={styles.content}>
          {selectedProtocol === 'cli' ? (
            <div className={styles.steps}>
              {STEP_KEYS.map((stepKey, i) => (
                <div key={stepKey} className={styles.step}>
                  <div className={styles.stepIndicator}>
                    <div className={styles.stepDot}>{i + 1}</div>
                    {i < STEP_KEYS.length - 1 && <div className={styles.stepLine} />}
                  </div>
                  <div className={styles.stepContent}>
                    <h3 className={styles.stepTitle}>
                      {t(`${selectedAI}.cliSteps.${stepKey}.title`)}
                    </h3>
                    <p className={styles.stepDescription}>
                      {t(`${selectedAI}.cliSteps.${stepKey}.description`)}
                    </p>
                    <div className={styles.codeBlock}>
                      <pre className="kura-code">
                        <code>{CLI_COMMANDS[selectedAI][stepKey]}</code>
                      </pre>
                      <button
                        type="button"
                        className="kura-btn kura-btn--ghost"
                        onClick={() => copyText(CLI_COMMANDS[selectedAI][stepKey], `cli-${selectedAI}-${stepKey}`)}
                      >
                        {copyLabel(`cli-${selectedAI}-${stepKey}`)}
                      </button>
                    </div>
                  </div>
                </div>
              ))}
              <div className={`kura-card ${styles.advancedCard}`}>
                <h3 className={styles.stepTitle}>{t('advanced.title')}</h3>
                <p className={styles.stepDescription}>{t('advanced.description')}</p>
                <div className={styles.codeBlock}>
                  <pre className="kura-code">
                    <code>{CLI_API_KEY_FALLBACK}</code>
                  </pre>
                  <button
                    type="button"
                    className="kura-btn kura-btn--ghost"
                    onClick={() => copyText(CLI_API_KEY_FALLBACK, `cli-apikey-fallback-${selectedAI}`)}
                  >
                    {copyLabel(`cli-apikey-fallback-${selectedAI}`)}
                  </button>
                </div>
                <p className={styles.stepHint}>
                  {isLoggedIn ? t('accountGate.keyHintLoggedIn') : t('accountGate.keyHintLoggedOut')}
                </p>
                <div className={styles.stepActions}>
                  <Link href={API_KEY_SETTINGS_PATH} className="kura-btn kura-btn--ghost">
                    {tn('settings')}
                  </Link>
                </div>
              </div>
            </div>
          ) : (
            <div className={`kura-card ${styles.mcpCard}`}>
              <h3 className={styles.mcpTitle}>{isMcpLive ? t('mcpReady.title') : t('mcpComingSoon.title')}</h3>
              <p className={styles.mcpDescription}>
                {isMcpLive ? t('mcpReady.description') : t('mcpComingSoon.description')}
              </p>
              <p
                className={styles.mcpStatus}
                data-testid="setup-mcp-status"
                data-mcp-live={isMcpLive ? 'true' : 'false'}
              >
                {isMcpLive ? t('mcpReady.status') : t('mcpComingSoon.status')}
              </p>
              <p className={styles.stepHint}>
                {isLoggedIn ? t('accountGate.mcpHintLoggedIn') : t('accountGate.mcpHintLoggedOut')}
              </p>

              <div className={styles.mcpFields}>
                <div className={styles.mcpField}>
                  <div>
                    <p className={styles.mcpLabel}>{t('mcpFields.name')}</p>
                    <code className="kura-code-inline">{MCP_SERVER_NAME}</code>
                  </div>
                  <button
                    type="button"
                    className="kura-btn kura-btn--ghost"
                    onClick={() => copyText(MCP_SERVER_NAME, 'mcp-name')}
                  >
                    {copyLabel('mcp-name')}
                  </button>
                </div>

                <div className={styles.mcpField}>
                  <div>
                    <p className={styles.mcpLabel}>{t('mcpFields.url')}</p>
                    <code className="kura-code-inline">{mcpUrl}</code>
                  </div>
                  <button
                    type="button"
                    className="kura-btn kura-btn--ghost"
                    onClick={() => copyText(mcpUrl, 'mcp-url')}
                  >
                    {copyLabel('mcp-url')}
                  </button>
                </div>

                <div className={styles.mcpField}>
                  <div>
                    <p className={styles.mcpLabel}>{t('mcpFields.auth')}</p>
                    <code className="kura-code-inline">{t('mcpFields.authValue')}</code>
                  </div>
                  <button
                    type="button"
                    className="kura-btn kura-btn--ghost"
                    onClick={() => copyText(t('mcpFields.authValue'), 'mcp-auth')}
                  >
                    {copyLabel('mcp-auth')}
                  </button>
                </div>
              </div>

              <div className={styles.stepActions}>{renderSetupActions()}</div>

              <div className={styles.codeBlock}>
                <pre className="kura-code">
                  <code>{mcpOauthSnippet}</code>
                </pre>
                <button
                  type="button"
                  className="kura-btn kura-btn--ghost"
                  onClick={() => copyText(mcpOauthSnippet, `mcp-snippet-${selectedAI}`)}
                >
                  {copyLabel(`mcp-snippet-${selectedAI}`)}
                </button>
              </div>

              <div className={`kura-card ${styles.advancedCard}`}>
                <h3 className={styles.stepTitle}>{t('advanced.title')}</h3>
                <p className={styles.stepDescription}>{t('advanced.mcpDescription')}</p>
                <div className={styles.codeBlock}>
                  <pre className="kura-code">
                    <code>{mcpApiKeyFallbackSnippet}</code>
                  </pre>
                  <button
                    type="button"
                    className="kura-btn kura-btn--ghost"
                    onClick={() => copyText(mcpApiKeyFallbackSnippet, `mcp-apikey-fallback-${selectedAI}`)}
                  >
                    {copyLabel(`mcp-apikey-fallback-${selectedAI}`)}
                  </button>
                </div>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
