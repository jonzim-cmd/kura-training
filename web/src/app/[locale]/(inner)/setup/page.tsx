'use client';

import { useEffect, useState } from 'react';
import { useTranslations } from 'next-intl';
import { useAuth } from '@/lib/auth-context';
import { SETUP_SEEN_STORAGE_KEY } from '@/lib/onboarding';
import { ClaudeGuide } from './ClaudeGuide';
import styles from './setup.module.css';

type AI = 'claude' | 'chatgpt' | 'openclaw';
const MCP_URL = 'https://api.withkura.com/mcp';

export default function SetupPage() {
  const t = useTranslations('setup');
  const tc = useTranslations('common');
  const [selectedAI, setSelectedAI] = useState<AI | null>(null);
  const [expertOpen, setExpertOpen] = useState(false);
  const [copiedId, setCopiedId] = useState<string | null>(null);
  const { user, loading } = useAuth();

  useEffect(() => {
    if (!loading && user) {
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
        {/* Header */}
        <div className={styles.header}>
          <h1 className={styles.title}>{t('title')}</h1>
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

            {selectedAI === 'chatgpt' && (
              <div className={styles.chatgptPlaceholder}>
                <div className={styles.chatgptTitle}>{t('chatgpt.comingSoon')}</div>
                <p className={styles.chatgptHint}>{t('chatgpt.comingSoonHint')}</p>
                <div className={styles.mcpUrlField}>
                  <code className={styles.mcpUrlValue}>{MCP_URL}</code>
                  <button
                    type="button"
                    className="kura-btn kura-btn--ghost"
                    onClick={() => copyText(MCP_URL, 'chatgpt-mcp-url')}
                  >
                    {copyLabel('chatgpt-mcp-url')}
                  </button>
                </div>
              </div>
            )}

            {selectedAI === 'openclaw' && (
              <div className={styles.openclawGuide}>
                {/* Step 1: Install */}
                <div className={styles.openclawStep}>
                  <div className={styles.openclawStepTitle}>
                    <span className={styles.openclawStepNumber}>1</span>
                    {t('openclaw.step1Title')}
                  </div>
                  <p className={styles.openclawHint}>{t('openclaw.step1Hint')}</p>
                  <div className={styles.codeBlock}>
                    <code className={styles.codeValue}>curl -fsSL https://openclaw.ai/install.sh | bash</code>
                    <button
                      type="button"
                      className="kura-btn kura-btn--ghost"
                      onClick={() => copyText('curl -fsSL https://openclaw.ai/install.sh | bash', 'oc-install')}
                    >
                      {copyLabel('oc-install')}
                    </button>
                  </div>
                  <a
                    href="https://openclaw.ai"
                    target="_blank"
                    rel="noreferrer"
                    className={styles.openclawLink}
                  >
                    openclaw.ai &rarr;
                  </a>
                </div>

                {/* Step 2: Prompt */}
                <div className={styles.openclawStep}>
                  <div className={styles.openclawStepTitle}>
                    <span className={styles.openclawStepNumber}>2</span>
                    {t('openclaw.step2Title')}
                  </div>
                  <p className={styles.openclawHint}>{t('openclaw.step2Hint')}</p>
                  <div className={styles.codeBlock}>
                    <code className={styles.codeValue}>{t('openclaw.step2Prompt')}</code>
                    <button
                      type="button"
                      className="kura-btn kura-btn--ghost"
                      onClick={() => copyText(t('openclaw.step2Prompt'), 'oc-prompt')}
                    >
                      {copyLabel('oc-prompt')}
                    </button>
                  </div>
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
              <p className={styles.expertDescription}>{t('expert.description')}</p>
              <div className={styles.codeBlock} style={{ paddingLeft: 0 }}>
                <code className={styles.codeValue}>{t('expert.prompt')}</code>
                <button
                  type="button"
                  className="kura-btn kura-btn--ghost"
                  onClick={() => copyText(t('expert.prompt'), 'expert-prompt')}
                >
                  {copyLabel('expert-prompt')}
                </button>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
