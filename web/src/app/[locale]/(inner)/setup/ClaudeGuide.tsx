'use client';

import { useState, useCallback, useEffect, useRef } from 'react';
import { useTranslations } from 'next-intl';
import styles from './ClaudeGuide.module.css';

const TOTAL_STEPS = 6;
const MCP_URL = 'https://api.withkura.com/mcp';

/** Render text with quoted segments as <strong> */
function BoldQuoted({ text }: { text: string }) {
  const parts = text.split(/("[^"]+")/);
  return (
    <>
      {parts.map((part, i) =>
        part.startsWith('"') ? <strong key={i}>{part}</strong> : <span key={i}>{part}</span>
      )}
    </>
  );
}

/* ── Tiny SVG icons used inside mockup ── */
function GitHubMini() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="white">
      <path d="M12 0C5.37 0 0 5.37 0 12c0 5.31 3.435 9.795 8.205 11.385.6.105.825-.255.825-.57 0-.285-.015-1.23-.015-2.235-3.015.555-3.795-.735-4.035-1.41-.135-.345-.72-1.41-1.23-1.695-.42-.225-1.02-.78-.015-.795.945-.015 1.62.87 1.845 1.23 1.08 1.815 2.805 1.305 3.495.99.105-.78.42-1.305.765-1.605-2.67-.3-5.46-1.335-5.46-5.925 0-1.305.465-2.385 1.23-3.225-.12-.3-.54-1.53.12-3.18 0 0 1.005-.315 3.3 1.23.96-.27 1.98-.405 3-.405s2.04.135 3 .405c2.295-1.56 3.3-1.23 3.3-1.23.66 1.65.24 2.88.12 3.18.765.84 1.23 1.905 1.23 3.225 0 4.605-2.805 5.625-5.475 5.925.435.375.81 1.095.81 2.22 0 1.605-.015 2.895-.015 3.3 0 .315.225.69.825.57A12.02 12.02 0 0024 12c0-6.63-5.37-12-12-12z" />
    </svg>
  );
}

/* ── Reusable sub-components ── */

function MockupChrome({ url, children }: { url: string; children: React.ReactNode }) {
  return (
    <div className={styles.mockup}>
      <div className={styles.mockupBar}>
        <div className={`${styles.dot} ${styles.dotR}`} />
        <div className={`${styles.dot} ${styles.dotY}`} />
        <div className={`${styles.dot} ${styles.dotG}`} />
        <div className={styles.mockupUrl}>{url}</div>
      </div>
      {children}
    </div>
  );
}

function ClickDot({ style }: { style: React.CSSProperties }) {
  return <div className={styles.clickDot} style={style} />;
}

function HighlightPulse({ small }: { small?: boolean }) {
  return <div className={small ? styles.highlightPulseSmall : styles.highlightPulse} />;
}

function SidebarItems({ t }: { t: ReturnType<typeof useTranslations> }) {
  return (
    <>
      <div className={styles.uiSidebarLogo}>Claude</div>
      <div className={styles.uiSidebarItem}><span className={styles.sidebarIcon}>+</span> {t('mockup.newChat')}</div>
      <div className={styles.uiSidebarItem}><span className={styles.sidebarIcon}>&#x1F50D;</span> {t('mockup.search')}</div>
      <div className={styles.uiSidebarItem}><span className={styles.sidebarIcon}>&#x1F4AC;</span> {t('mockup.chats')}</div>
      <div className={styles.uiSidebarItem}><span className={styles.sidebarIcon}>&#x1F4C1;</span> {t('mockup.projects')}</div>
      <div className={styles.uiSidebarItem}><span className={styles.sidebarIcon}>&#x1F9E9;</span> {t('mockup.artifacts')}</div>
      <div className={styles.uiSidebarItem}><span className={styles.sidebarIcon}>&lt;/&gt;</span> Code</div>
    </>
  );
}

function SettingsSidebarItems({ t, active, highlight }: { t: ReturnType<typeof useTranslations>; active?: string; highlight?: string }) {
  const items = ['general', 'account', 'privacy', 'billing', 'usage', 'capabilities', 'connectors'] as const;
  return (
    <>
      <div className={styles.settingsTitle}>{t('mockup.settings')}</div>
      {items.map((item) => {
        let cls = styles.ssItem;
        if (item === active) cls += ` ${styles.ssItemActive}`;
        if (item === highlight) cls += ` ${styles.ssItemHighlight}`;
        return (
          <div key={item} className={cls} style={{ position: item === highlight ? 'relative' : undefined }}>
            {t(`mockup.${item}`)}
            {item === highlight && <ClickDot style={{ right: -24, top: 4 }} />}
          </div>
        );
      })}
      <div className={styles.ssItem}>Claude Code</div>
    </>
  );
}

function ConnectorRows({ t, showKura }: { t: ReturnType<typeof useTranslations>; showKura?: boolean }) {
  return (
    <>
      <div className={styles.connRow}>
        <div className={styles.connIcon} style={{ background: '#24292e' }}><GitHubMini /></div>
        <div className={styles.connName}>GitHub</div>
        <span className={`${styles.connBtn} ${styles.connBtnGreen}`}>{t('mockup.connected')}</span>
        <span className={styles.connBtnDots}>&middot;&middot;&middot;</span>
      </div>
      <div className={styles.connRow}>
        <div className={styles.connIcon} style={{ background: '#a259ff' }}>F</div>
        <div className={styles.connName}>Figma<span className={styles.connSub}>{t('mockup.interactive')}</span></div>
        <span className={styles.connBtn}>{t('mockup.configure')}</span>
        <span className={styles.connBtnDots}>&middot;&middot;&middot;</span>
      </div>
      <div className={styles.connRow}>
        <div className={styles.connIcon} style={{ background: '#326599' }}>P</div>
        <div className={styles.connName}>PubMed</div>
        <span className={styles.connBtn}>{t('mockup.configure')}</span>
        <span className={styles.connBtnDots}>&middot;&middot;&middot;</span>
      </div>
      {showKura && (
        <div className={styles.kuraConnRow}>
          <div className={styles.connIcon} style={{ background: '#d97757', fontSize: 12, fontWeight: 700 }}>K</div>
          <div className={styles.connName}>
            Kura Training
            <span className={styles.connSubAccent}>{t('mockup.custom')}</span>
            <span className={styles.connSub}>{MCP_URL}</span>
          </div>
          <span className={styles.kuraConnBtn} style={{ position: 'relative' }}>
            {t('mockup.connect')}
            <HighlightPulse />
            <ClickDot style={{ left: '50%', top: '50%', transform: 'translate(-50%, -50%)' }} />
          </span>
          <span className={styles.connBtnDots}>&middot;&middot;&middot;</span>
        </div>
      )}
      {!showKura && (
        <div className={styles.connRow} style={{ borderBottom: 'none' }}>
          <div className={styles.connIcon} style={{ background: '#1a73e8' }}>G</div>
          <div className={styles.connName} style={{ color: '#888' }}>Google Drive</div>
          <span className={styles.connBtn}>{t('mockup.connect')}</span>
        </div>
      )}
    </>
  );
}

/* ── Step components ── */

function Step1({ t }: { t: ReturnType<typeof useTranslations> }) {
  return (
    <MockupChrome url="claude.ai">
      <div className={styles.mockupBody}>
        <div className={styles.uiSidebar}>
          <SidebarItems t={t} />
          <div style={{ flex: 1 }} />
          <div className={styles.uiSidebarItem} style={{ color: '#666', fontSize: 12, marginBottom: 2 }}>{t('mockup.recents')}</div>
          <div className={styles.uiSidebarItem} style={{ color: '#777', fontSize: 12 }}>My first project...</div>
          <div style={{ flex: 1 }} />
          <div className={styles.uiProfile}>
            <div className={styles.avatar}>J</div>
            <div>
              <div className={styles.userName}>Jonas</div>
              <div className={styles.userPlan}>Max Plan</div>
            </div>
            <HighlightPulse />
            <ClickDot style={{ right: -20, top: '50%', transform: 'translateY(-50%)' }} />
          </div>
        </div>
        <div className={styles.uiMain}>
          <div className={styles.greeting}><span className={styles.greetingSpark}>&#10035;</span> {t('mockup.greeting', { name: 'Jonas' })}</div>
          <div className={styles.chatBox}>{t('mockup.chatPlaceholder')}</div>
        </div>
      </div>
    </MockupChrome>
  );
}

function Step2({ t }: { t: ReturnType<typeof useTranslations> }) {
  return (
    <MockupChrome url="claude.ai">
      <div className={styles.mockupBody}>
        <div className={styles.uiSidebar} style={{ position: 'relative' }}>
          <SidebarItems t={t} />
          <div style={{ flex: 1 }} />
          <div className={styles.popup}>
            <div className={styles.popupEmail}>user@example.com</div>
            <div className={`${styles.popupItem} ${styles.popupItemHighlight}`} style={{ position: 'relative' }}>
              <span className={styles.popupIcon}>&#9881;</span> {t('mockup.settings')}
              <span className={styles.popupRight}>&#8679;&#8984;,</span>
              <ClickDot style={{ right: -20, top: 4 }} />
            </div>
            <div className={styles.popupItem}><span className={styles.popupIcon}>&#x1F310;</span> {t('mockup.language')} <span className={styles.popupRight}>&rsaquo;</span></div>
            <div className={styles.popupItem}><span className={styles.popupIcon}>&#10067;</span> {t('mockup.getHelp')}</div>
            <div className={styles.popupSep} />
            <div className={styles.popupItem}><span className={styles.popupIcon}>&#x2B06;</span> {t('mockup.upgradePlan')}</div>
            <div className={styles.popupItem}><span className={styles.popupIcon}>&#x1F4F1;</span> {t('mockup.getApps')}</div>
            <div className={styles.popupItem}><span className={styles.popupIcon}>&#x1F381;</span> {t('mockup.giftClaude')}</div>
            <div className={styles.popupItem}><span className={styles.popupIcon}>&#x2139;</span> {t('mockup.learnMore')} <span className={styles.popupRight}>&rsaquo;</span></div>
            <div className={styles.popupSep} />
            <div className={styles.popupItem}><span className={styles.popupIcon}>&#x1F6AA;</span> {t('mockup.logOut')}</div>
          </div>
          <div className={styles.uiProfile} style={{ marginTop: 8 }}>
            <div className={styles.avatar}>J</div>
            <div>
              <div className={styles.userName}>Jonas</div>
              <div className={styles.userPlan}>Max Plan</div>
            </div>
          </div>
        </div>
        <div className={styles.uiMain}>
          <div className={styles.greeting}><span className={styles.greetingSpark}>&#10035;</span> {t('mockup.greeting', { name: 'Jonas' })}</div>
          <div className={styles.chatBox}>{t('mockup.chatPlaceholder')}</div>
        </div>
      </div>
    </MockupChrome>
  );
}

function Step3({ t }: { t: ReturnType<typeof useTranslations> }) {
  return (
    <MockupChrome url="claude.ai/settings">
      <div className={styles.mockupBody}>
        <div className={styles.settingsSidebar}>
          <SettingsSidebarItems t={t} highlight="connectors" />
        </div>
        <div className={styles.settingsContent}>
          <div style={{ fontSize: 18, color: '#ccc', marginBottom: 12, fontWeight: 600 }}>{t('mockup.profile')}</div>
          <div style={{ display: 'flex', gap: 20, marginBottom: 14 }}>
            <div style={{ flex: 1 }}>
              <div className={styles.profileLabel}>{t('mockup.fullName')}</div>
              <div className={styles.profileValue}>Jonas</div>
            </div>
            <div style={{ flex: 1 }}>
              <div className={styles.profileLabel}>{t('mockup.whatShouldCall')}</div>
              <div className={styles.profileValue}>Jonas</div>
            </div>
          </div>
          <div className={styles.profileLabel}>{t('mockup.whatDescribes')}</div>
          <div className={styles.profileValue} style={{ width: 200 }}>{t('mockup.other')}</div>
        </div>
      </div>
    </MockupChrome>
  );
}

function Step4({ t }: { t: ReturnType<typeof useTranslations> }) {
  return (
    <MockupChrome url="claude.ai/settings/connectors">
      <div className={styles.mockupBody} style={{ minHeight: 400 }}>
        <div className={styles.settingsSidebar}>
          <SettingsSidebarItems t={t} active="connectors" />
        </div>
        <div className={styles.settingsContent}>
          <div className={styles.connHeader}>
            <div className={styles.connTitle}>{t('mockup.connectors')}</div>
            <div className={styles.browseBtn}>{t('mockup.browseConnectors')}</div>
          </div>
          <div className={styles.connDesc}>{t('mockup.connectorsDesc')}</div>
          <ConnectorRows t={t} />
          <div style={{ position: 'relative', display: 'inline-block' }}>
            <div className={styles.addConnectorBtn}>{t('mockup.addCustomConnector')}</div>
            <HighlightPulse />
            <ClickDot style={{ right: -28, top: '50%', transform: 'translateY(-50%)' }} />
          </div>
        </div>
      </div>
    </MockupChrome>
  );
}

function Step5({ t }: { t: ReturnType<typeof useTranslations> }) {
  return (
    <MockupChrome url="claude.ai/settings/connectors">
      <div className={styles.mockupBody} style={{ minHeight: 400 }}>
        <div className={`${styles.settingsSidebar} ${styles.sidebarDimmed}`}>
          <SettingsSidebarItems t={t} active="connectors" />
        </div>
        <div className={`${styles.settingsContent} ${styles.dimmed}`}>
          <div className={styles.connTitle}>{t('mockup.connectors')}</div>
        </div>
        <div className={styles.modalOverlay}>
          <div className={styles.modal}>
            <h3 className={styles.modalTitle}>
              {t('mockup.addConnectorTitle')} <span className={styles.modalBadge}>BETA</span>
            </h3>
            <div className={styles.modalDesc}>{t('mockup.addConnectorDesc')}</div>
            <div className={`${styles.modalInput} ${styles.modalInputFocus}`} style={{ position: 'relative' }}>
              <span>Kura Training</span>
              <div className={styles.highlightPulseSmall} />
            </div>
            <div className={styles.modalInput} style={{ position: 'relative' }}>
              <span>{MCP_URL}</span>
              <div className={styles.highlightPulseSmall} />
            </div>
            <div className={styles.modalAdvanced}>&#9656; {t('mockup.advancedSettings')}</div>
            <div className={styles.modalDisclaimer}>{t('mockup.disclaimer')}</div>
            <div className={styles.modalActions}>
              <div className={`${styles.modalBtn} ${styles.modalBtnCancel}`}>{t('mockup.cancel')}</div>
              <div className={`${styles.modalBtn} ${styles.modalBtnAdd}`} style={{ position: 'relative' }}>
                {t('mockup.add')}
                <ClickDot style={{ right: -22, top: 2 }} />
              </div>
            </div>
          </div>
        </div>
      </div>
    </MockupChrome>
  );
}

function Step6({ t }: { t: ReturnType<typeof useTranslations> }) {
  return (
    <MockupChrome url="claude.ai/settings/connectors">
      <div className={styles.mockupBody} style={{ minHeight: 400 }}>
        <div className={styles.settingsSidebar}>
          <SettingsSidebarItems t={t} active="connectors" />
        </div>
        <div className={styles.settingsContent}>
          <div className={styles.connHeader}>
            <div className={styles.connTitle}>{t('mockup.connectors')}</div>
            <div className={styles.browseBtn}>{t('mockup.browseConnectors')}</div>
          </div>
          <div className={styles.connDesc}>{t('mockup.connectorsDesc')}</div>
          <ConnectorRows t={t} showKura />
          <div className={styles.addConnectorBtn} style={{ opacity: 0.5 }}>{t('mockup.addCustomConnector')}</div>
        </div>
      </div>
    </MockupChrome>
  );
}

const STEPS = [Step1, Step2, Step3, Step4, Step5, Step6];

/* ── Main component ── */

export function ClaudeGuide() {
  const t = useTranslations('setup.claude');
  const tc = useTranslations('common');
  const [current, setCurrent] = useState(0);
  const [copied, setCopied] = useState(false);
  const isLast = current >= TOTAL_STEPS - 1;

  const copyUrl = async () => {
    try {
      await navigator.clipboard.writeText(MCP_URL);
    } catch {
      const ta = document.createElement('textarea');
      ta.value = MCP_URL;
      ta.style.position = 'fixed';
      ta.style.opacity = '0';
      document.body.appendChild(ta);
      ta.select();
      document.execCommand('copy');
      document.body.removeChild(ta);
    }
    setCopied(true);
    setTimeout(() => setCopied(false), 1800);
  };

  const stepLabels = [
    t('step1Label'), t('step2Label'), t('step3Label'),
    t('step4Label'), t('step5Label'), t('step6Label'),
  ];

  const go = useCallback((dir: 1 | -1) => {
    setCurrent((prev) => {
      if (dir > 0 && prev >= TOTAL_STEPS - 1) return 0;
      if (dir > 0) return prev + 1;
      if (prev > 0) return prev - 1;
      return prev;
    });
  }, []);

  // Keyboard navigation
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'ArrowRight') { e.preventDefault(); go(1); }
      if (e.key === 'ArrowLeft') { e.preventDefault(); go(-1); }
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [go]);

  // Swipe navigation
  const touchStartX = useRef<number | null>(null);
  const handleTouchStart = useCallback((e: React.TouchEvent) => {
    touchStartX.current = e.touches[0].clientX;
  }, []);
  const handleTouchEnd = useCallback((e: React.TouchEvent) => {
    if (touchStartX.current === null) return;
    const dx = e.changedTouches[0].clientX - touchStartX.current;
    if (Math.abs(dx) > 50) go(dx < 0 ? 1 : -1);
    touchStartX.current = null;
  }, [go]);

  return (
    <div className={styles.guide}>
      {/* Intro with link */}
      <p className={styles.intro}>
        {t.rich('intro', {
          link: (chunks) => (
            <a href="https://claude.ai" target="_blank" rel="noreferrer" className={styles.introLink}>
              {chunks}
            </a>
          ),
        })}
      </p>

      {/* Step description */}
      <div className={styles.stepLabel}>
        <span className={styles.stepNumber}>{current + 1}/{TOTAL_STEPS}</span>{' '}
        <BoldQuoted text={stepLabels[current]} />
      </div>

      {/* MCP URL copy — only on step 5 */}
      {current === 4 && (
        <div className={styles.mcpCopyWrap}>
          <span className={styles.mcpLabel}>MCP-Server-URL:</span>
          <div className={styles.mcpCopy}>
            <code className={styles.mcpUrl}>{MCP_URL}</code>
            <button type="button" className="kura-btn kura-btn--ghost" onClick={copyUrl}>
              {copied ? tc('copied') : tc('copy')}
            </button>
          </div>
        </div>
      )}

      {/* Progress dots — directly above mockup */}
      <div className={styles.progressBar}>
        {Array.from({ length: TOTAL_STEPS }).map((_, i) => (
          <div key={i} style={{ display: 'contents' }}>
            <button
              className={`${styles.progressDot} ${
                i === current ? styles.progressDotActive :
                i < current ? styles.progressDotDone : ''
              }`}
              onClick={() => setCurrent(i)}
              aria-label={`Step ${i + 1}`}
            />
            {i < TOTAL_STEPS - 1 && (
              <div className={`${styles.progressLine} ${i < current ? styles.progressLineDone : ''}`} />
            )}
          </div>
        ))}
      </div>

      {/* Mockup — click or swipe to advance */}
      {STEPS.map((StepComp, i) => (
        <div
          key={i}
          className={`${styles.stepWrapper} ${i === current ? styles.stepWrapperActive : ''}`}
          onClick={() => go(1)}
          onTouchStart={handleTouchStart}
          onTouchEnd={handleTouchEnd}
          style={{ cursor: 'pointer' }}
        >
          <StepComp t={t} />
        </div>
      ))}

      {/* Controls — below mockup */}
      <div className={styles.controls}>
        <button className={styles.ctrlBtn} onClick={() => go(-1)}>
          &larr; {t('back')}
        </button>
        <button className={styles.ctrlBtn} onClick={() => go(1)}>
          {isLast ? `${t('restart')} \u21BB` : `${t('next')} \u2192`}
        </button>
      </div>

      {/* Done message on last step */}
      {isLast && (
        <div className={styles.doneMsg}>
          <div className={styles.doneText}>{t('done')}</div>
          <div className={styles.doneHint}>{t('doneHint')}</div>
        </div>
      )}
    </div>
  );
}
