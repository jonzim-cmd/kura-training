'use client';

import { useEffect, useState } from 'react';
import { useTranslations } from 'next-intl';
import { useAuth } from '@/lib/auth-context';
import { SETUP_SEEN_STORAGE_KEY } from '@/lib/onboarding';
import { ClaudeGuide } from './ClaudeGuide';
import { ChatGPTGuide } from './ChatGPTGuide';
import styles from './setup.module.css';

type AI = 'claude' | 'chatgpt' | 'openclaw';
const MCP_URL = 'https://api.withkura.com/mcp';

export default function SetupPage() {
  const t = useTranslations('setup');
  const tc = useTranslations('common');
  const [selectedAI, setSelectedAI] = useState<AI | null>(null);
  const [expertOpen, setExpertOpen] = useState(false);
  const [ocMethod, setOcMethod] = useState<'mcp' | 'cli'>('cli');
  const [copiedId, setCopiedId] = useState<string | null>(null);
  const [isFirstVisit, setIsFirstVisit] = useState(false);
  const { user, loading } = useAuth();

  useEffect(() => {
    if (!loading && user) {
      if (localStorage.getItem(SETUP_SEEN_STORAGE_KEY) !== '1') {
        setIsFirstVisit(true);
      }
      localStorage.setItem(SETUP_SEEN_STORAGE_KEY, '1');
    }
  }, [loading, user]);

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

  return (
    <div className={styles.setupPage}>
      <div className={`kura-container ${styles.container}`}>
        {/* Welcome + Header */}
        <div className={styles.header}>
          {isFirstVisit && (
            <div className={styles.welcome}>
              <h1 className={styles.welcomeTitle}>{t('welcome')}</h1>
              <p className={styles.welcomeSubtitle}>{t('welcomeSubtitle')}</p>
            </div>
          )}
          <h2 className={styles.title}>{t('title')}</h2>
          <p className={styles.subtitle}>{t('subtitle')}</p>
        </div>

        {/* AI Cards */}
        <div className={styles.aiCards}>
          {(['claude', 'chatgpt', 'openclaw'] as const).map((ai) => (
            <button
              key={ai}
              className={`${styles.aiCard} ${selectedAI === ai ? styles.aiCardActive : ''}`}
              onClick={() => setSelectedAI(selectedAI === ai ? null : ai)}
            >
              <span className={styles.aiCardName}>{t(`cards.${ai}`)}</span>
              <span className={styles.aiCardSub}>{t(`cards.${ai}Sub`)}</span>
            </button>
          ))}
        </div>

        {/* Guide Content */}
        {selectedAI && (
          <div className={styles.guideContent}>
            {selectedAI === 'claude' && <ClaudeGuide />}

            {selectedAI === 'chatgpt' && <ChatGPTGuide />}

            {selectedAI === 'openclaw' && (
              <div className={styles.openclawGuide}>
                <a
                  href="https://openclaw.ai"
                  target="_blank"
                  rel="noreferrer"
                  className={styles.openclawTitle}
                >
                  {t('openclaw.getOpenClaw')}
                </a>
                <p className={styles.openclawIntro}>{t('openclaw.intro')}</p>

                <div className={styles.methodTabs}>
                  <button
                    className={`${styles.methodTab} ${ocMethod === 'cli' ? styles.methodTabActive : ''}`}
                    onClick={() => setOcMethod('cli')}
                  >
                    {t('openclaw.methodCli')}
                  </button>
                  <button
                    className={`${styles.methodTab} ${ocMethod === 'mcp' ? styles.methodTabActive : ''}`}
                    onClick={() => setOcMethod('mcp')}
                  >
                    {t('openclaw.methodMcp')}
                  </button>
                </div>

                <p className={styles.openclawHint}>
                  {t(ocMethod === 'mcp' ? 'openclaw.mcpHint' : 'openclaw.cliHint')}
                </p>
                <div className={styles.codeBlock}>
                  <code className={styles.codeValue}>
                    {t(ocMethod === 'mcp' ? 'openclaw.mcpPrompt' : 'openclaw.cliPrompt')}
                  </code>
                  <button
                    type="button"
                    className="kura-btn kura-btn--ghost"
                    onClick={() => copyText(t(ocMethod === 'mcp' ? 'openclaw.mcpPrompt' : 'openclaw.cliPrompt'), `oc-${ocMethod}`)}
                  >
                    {copyLabel(`oc-${ocMethod}`)}
                  </button>
                </div>
              </div>
            )}
          </div>
        )}

        {/* Expert Section */}
        <div className={styles.expertSection}>
          <button
            className={styles.expertToggle}
            onClick={() => setExpertOpen(!expertOpen)}
          >
            <span className={`${styles.expertArrow} ${expertOpen ? styles.expertArrowOpen : ''}`}>&#9656;</span>
            {t('expert.toggle')}
          </button>

          {expertOpen && (
            <div className={styles.expertContent}>
              {/* MCP */}
              <div className={styles.expertBlock}>
                <div className={styles.expertBlockTitle}>{t('expert.mcpTitle')}</div>
                <p className={styles.expertDescription}>{t('expert.mcpDescription')}</p>
                <div className={styles.codeBlock} style={{ paddingLeft: 0 }}>
                  <code className={styles.codeValue}>{t('expert.mcpPrompt')}</code>
                  <button
                    type="button"
                    className="kura-btn kura-btn--ghost"
                    onClick={() => copyText(t('expert.mcpPrompt'), 'expert-mcp')}
                  >
                    {copyLabel('expert-mcp')}
                  </button>
                </div>
              </div>

              {/* CLI */}
              <div className={styles.expertBlock}>
                <div className={styles.expertBlockTitle}>{t('expert.cliTitle')}</div>
                <p className={styles.expertDescription}>{t('expert.cliDescription')}</p>
                <div className={styles.codeBlock} style={{ paddingLeft: 0 }}>
                  <code className={styles.codeValue}>{t('expert.cliPrompt')}</code>
                  <button
                    type="button"
                    className="kura-btn kura-btn--ghost"
                    onClick={() => copyText(t('expert.cliPrompt'), 'expert-cli')}
                  >
                    {copyLabel('expert-cli')}
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
