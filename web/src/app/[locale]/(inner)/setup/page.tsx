'use client';

import { useState } from 'react';
import { useTranslations } from 'next-intl';
import styles from './setup.module.css';

type AI = 'claude' | 'chatgpt' | 'gemini';
type Protocol = 'cli' | 'mcp';

const CLI_COMMANDS: Record<AI, Record<string, string>> = {
  claude: {
    install: 'cargo install kura-cli',
    login: 'kura login',
    apiKey: 'kura admin create-key --name "claude"',
    configure: `# Add to your CLAUDE.md or project config:
# Tool: kura CLI at /path/to/kura
# API Key: kura_sk_...`,
  },
  chatgpt: {
    install: 'cargo install kura-cli',
    login: 'kura login',
    apiKey: 'kura admin create-key --name "chatgpt"',
    configure: `# In ChatGPT settings, add a custom tool:
# Name: Kura
# Command: kura
# API Key: kura_sk_...`,
  },
  gemini: {
    install: 'cargo install kura-cli',
    login: 'kura login',
    apiKey: 'kura admin create-key --name "gemini"',
    configure: `# In Gemini workspace, add Kura tool:
# Binary: /path/to/kura
# API Key: kura_sk_...`,
  },
};

const STEP_KEYS = ['install', 'login', 'apiKey', 'configure'] as const;

export default function SetupPage() {
  const t = useTranslations('setup');
  const [selectedAI, setSelectedAI] = useState<AI>('claude');
  const [selectedProtocol, setSelectedProtocol] = useState<Protocol>('cli');

  return (
    <div className={styles.setupPage}>
      <div className={`kura-container ${styles.container}`}>
        <div className={styles.header}>
          <h1 className={styles.title}>{t('title')}</h1>
          <p className={styles.subtitle}>{t('subtitle')}</p>
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
            <span className="kura-badge" style={{ marginLeft: '0.5rem' }}>
              {t('comingSoon')}
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
                    <pre className="kura-code">
                      <code>{CLI_COMMANDS[selectedAI][stepKey]}</code>
                    </pre>
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <div className={`kura-card ${styles.mcpCard}`}>
              <div className={styles.mcpIcon} aria-hidden="true">
                <svg width="40" height="40" viewBox="0 0 40 40" fill="none" stroke="currentColor" strokeWidth="1.5">
                  <path d="M20 5L35 20L20 35L5 20Z" />
                  <path d="M20 12L28 20L20 28L12 20Z" />
                </svg>
              </div>
              <h3 className={styles.mcpTitle}>{t('mcpComingSoon.title')}</h3>
              <p className={styles.mcpDescription}>{t('mcpComingSoon.description')}</p>
              <p className={styles.mcpStatus}>{t('mcpComingSoon.status')}</p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
