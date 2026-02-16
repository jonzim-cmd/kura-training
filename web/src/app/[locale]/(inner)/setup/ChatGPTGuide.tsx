'use client';

import { useState, useCallback, useEffect, useRef } from 'react';
import { useTranslations } from 'next-intl';
import styles from './ChatGPTGuide.module.css';

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

/* ── Tiny SVG icons ── */
function GitHubMini() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="white">
      <path d="M12 0C5.37 0 0 5.37 0 12c0 5.31 3.435 9.795 8.205 11.385.6.105.825-.255.825-.57 0-.285-.015-1.23-.015-2.235-3.015.555-3.795-.735-4.035-1.41-.135-.345-.72-1.41-1.23-1.695-.42-.225-1.02-.78-.015-.795.945-.015 1.62.87 1.845 1.23 1.08 1.815 2.805 1.305 3.495.99.105-.78.42-1.305.765-1.605-2.67-.3-5.46-1.335-5.46-5.925 0-1.305.465-2.385 1.23-3.225-.12-.3-.54-1.53.12-3.18 0 0 1.005-.315 3.3 1.23.96-.27 1.98-.405 3-.405s2.04.135 3 .405c2.295-1.56 3.3-1.23 3.3-1.23.66 1.65.24 2.88.12 3.18.765.84 1.23 1.905 1.23 3.225 0 4.605-2.805 5.625-5.475 5.925.435.375.81 1.095.81 2.22 0 1.605-.015 2.895-.015 3.3 0 .315.225.69.825.57A12.02 12.02 0 0024 12c0-6.63-5.37-12-12-12z" />
    </svg>
  );
}

function OpenAIFlower() {
  return (
    <span style={{ color: '#000', fontSize: 14, fontWeight: 700 }}>&#10047;</span>
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

function HighlightPulse({ small, style }: { small?: boolean; style?: React.CSSProperties }) {
  return <div className={small ? styles.highlightPulseSmall : styles.highlightPulse} style={style} />;
}

/* ── ChatGPT Sidebar ── */
function SidebarItems({ t }: { t: ReturnType<typeof useTranslations> }) {
  return (
    <>
      <div className={styles.uiSidebarLogo}>
        <div className={styles.openaiIcon}><OpenAIFlower /></div>
      </div>
      <div className={styles.uiSidebarItem}><span className={styles.sidebarIcon}>+</span> {t('mockup.newChat')}</div>
      <div className={styles.uiSidebarItem}><span className={styles.sidebarIcon}>&#x1F50D;</span> {t('mockup.searchChats')}</div>
      <div className={styles.uiSidebarItem}><span className={styles.sidebarIcon}>&#x1F4DA;</span> {t('mockup.library')}</div>
    </>
  );
}

function SidebarBottom({ t }: { t: ReturnType<typeof useTranslations> }) {
  return (
    <>
      <div className={styles.uiSidebarItem} style={{ color: '#666', fontSize: 12, marginBottom: 2 }}>{t('mockup.projects')}</div>
      <div className={styles.uiSidebarItem} style={{ color: '#777', fontSize: 12 }}>{t('mockup.newProject')}</div>
      <div style={{ flex: 1 }} />
      <div className={styles.uiProfile}>
        <div className={styles.avatar}>J</div>
        <div>
          <div className={styles.userName}>Jonas</div>
        </div>
      </div>
    </>
  );
}

function SettingsTabItems({ t, active }: { t: ReturnType<typeof useTranslations>; active?: string }) {
  const items = [
    { key: 'general', icon: '\u2699' },
    { key: 'notifications', icon: '\uD83D\uDD14' },
    { key: 'personalization', icon: '\u2728' },
    { key: 'apps', icon: '\uD83D\uDCE6' },
    { key: 'schedules', icon: '\u23F0' },
    { key: 'dataControls', icon: '\uD83D\uDCCA' },
    { key: 'security', icon: '\uD83D\uDD12' },
    { key: 'account', icon: '\uD83D\uDC64' },
  ] as const;
  return (
    <>
      <div className={styles.closeX}>&times;</div>
      {items.map((item) => {
        let cls = styles.stItem;
        if (item.key === active) cls += ` ${styles.stItemActive}`;
        return (
          <div key={item.key} className={cls}>
            <span className={styles.stIcon}>{item.icon}</span>
            {t(`mockup.settings_${item.key}`)}
          </div>
        );
      })}
    </>
  );
}

/* ── Step components ── */

function Step1({ t }: { t: ReturnType<typeof useTranslations> }) {
  return (
    <MockupChrome url="chatgpt.com">
      <div className={styles.mockupBody}>
        <div className={styles.uiSidebar}>
          <SidebarItems t={t} />
          <div className={`${styles.uiSidebarItem} ${styles.uiSidebarItemHl}`} style={{ position: 'relative' }}>
            <span className={styles.sidebarIcon}>&#x1F4E6;</span> Apps
            <HighlightPulse style={{ left: -4, top: -4, right: -4, bottom: -4, borderRadius: 8 }} />
            <ClickDot style={{ right: -24, top: 2 }} />
          </div>
          <div className={styles.uiSidebarItem}><span className={styles.sidebarIcon}>&#x1F9EA;</span> Deep Research</div>
          <div className={styles.uiSidebarItem}><span className={styles.sidebarIcon}>&#x2328;</span> Codex</div>
          <div className={styles.uiSidebarItem}><span className={styles.sidebarIcon}>&#x1F916;</span> GPTs</div>
          <div style={{ flex: 1 }} />
          <SidebarBottom t={t} />
        </div>
        <div className={styles.uiMain}>
          <div className={styles.mainTitle}>ChatGPT</div>
          <div className={styles.chatBox}>{t('mockup.askAnything')}</div>
        </div>
      </div>
    </MockupChrome>
  );
}

function Step2({ t }: { t: ReturnType<typeof useTranslations> }) {
  return (
    <MockupChrome url="chatgpt.com/apps">
      <div className={styles.mockupBody}>
        <div className={styles.uiSidebar}>
          <SidebarItems t={t} />
          <div className={`${styles.uiSidebarItem} ${styles.uiSidebarItemHl}`}>
            <span className={styles.sidebarIcon}>&#x1F4E6;</span> Apps
          </div>
          <div className={styles.uiSidebarItem}><span className={styles.sidebarIcon}>&#x1F9EA;</span> Deep Research</div>
          <div className={styles.uiSidebarItem}><span className={styles.sidebarIcon}>&#x2328;</span> Codex</div>
          <div className={styles.uiSidebarItem}><span className={styles.sidebarIcon}>&#x1F916;</span> GPTs</div>
          <div style={{ flex: 1 }} />
          <SidebarBottom t={t} />
        </div>
        <div className={styles.uiMain} style={{ alignItems: 'flex-start', paddingTop: 20 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', width: '100%', alignItems: 'flex-start' }}>
            <div>
              <div className={styles.appsHeader}>Apps <span className={styles.betaBadge}>BETA</span></div>
              <div className={styles.appsDesc}>{t('mockup.appsDesc')}</div>
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
              <div className={styles.searchAppsBtn}>&#x1F50D; {t('mockup.searchApps')}</div>
              <div className={styles.gearIcon} style={{ position: 'relative' }}>
                &#9881;
                <HighlightPulse style={{ left: -6, top: -6, right: -6, bottom: -6, borderRadius: 8 }} />
                <ClickDot style={{ right: -28, top: '50%', transform: 'translateY(-50%)' }} />
              </div>
            </div>
          </div>
          <div className={styles.featuredCard}>
            <div className={styles.featuredCardIcon}><GitHubMini /></div>
            <h3 className={styles.featuredCardTitle}>Work with GitHub</h3>
            <p className={styles.featuredCardDesc}>{t('mockup.githubDesc')}</p>
            <div className={styles.viewBtn}>View</div>
          </div>
          <div className={styles.filterTabs}>
            <div className={`${styles.filterTab} ${styles.filterTabActive}`}>Featured</div>
            <div className={styles.filterTab}>Productivity</div>
            <div className={styles.filterTab}>Lifestyle</div>
          </div>
        </div>
      </div>
    </MockupChrome>
  );
}

function Step3({ t }: { t: ReturnType<typeof useTranslations> }) {
  return (
    <MockupChrome url="chatgpt.com/apps#settings">
      <div className={styles.mockupBody} style={{ minHeight: 380, alignItems: 'center', justifyContent: 'center', background: 'rgba(0,0,0,0.3)' }}>
        <div className={styles.settingsModal}>
          <div className={styles.settingsTabs}>
            <SettingsTabItems t={t} active="apps" />
          </div>
          <div className={styles.settingsContent}>
            <div className={styles.settingsContentTitle}>Apps</div>
            <div className={styles.appsIcons}>
              <div className={styles.appsIconItem} style={{ background: '#10a37f' }}>&#9830;</div>
              <div className={styles.appsIconItem} style={{ background: '#1da1f2' }}>&#9834;</div>
              <div className={styles.appsIconItem} style={{ background: '#ea4335' }}>&#9830;</div>
              <div className={styles.appsIconItem} style={{ background: '#f06529' }}>&#x25B6;</div>
            </div>
            <div className={styles.appsTabDesc}>{t('mockup.appsTabDesc')}</div>
            <div style={{ textAlign: 'center', marginBottom: 20 }}>
              <div className={styles.exploreBtn}>{t('mockup.exploreApps')}</div>
            </div>
            <div className={styles.advancedRow} style={{ position: 'relative' }}>
              <div className={styles.advIcon}>&#9881;</div>
              <div className={styles.advText}>{t('mockup.advancedSettings')}</div>
              <div className={styles.advChevron}>&rsaquo;</div>
              <HighlightPulse style={{ left: -6, top: -6, right: -6, bottom: -6, borderRadius: 8 }} />
              <ClickDot style={{ right: -28, top: '50%', transform: 'translateY(-50%)' }} />
            </div>
          </div>
        </div>
      </div>
    </MockupChrome>
  );
}

function Step4({ t }: { t: ReturnType<typeof useTranslations> }) {
  return (
    <MockupChrome url="chatgpt.com/apps#settings/Advanced">
      <div className={styles.mockupBody} style={{ minHeight: 380, alignItems: 'center', justifyContent: 'center', background: 'rgba(0,0,0,0.3)' }}>
        <div className={styles.settingsModal}>
          <div className={styles.settingsTabs}>
            <SettingsTabItems t={t} active="apps" />
          </div>
          <div className={styles.settingsContent}>
            <div className={styles.backLink}>&larr; {t('mockup.back')}</div>
            <div className={styles.devModeRow}>
              <div style={{ flex: 1 }}>
                <div className={styles.devModeLabel}>
                  {t('mockup.developerMode')}
                  <span className={styles.riskBadge}>{t('mockup.elevatedRisk')}</span>
                </div>
                <div className={styles.devModeDesc}>{t('mockup.devModeDesc')}</div>
              </div>
              <div style={{ position: 'relative' }}>
                <div className={styles.toggleTrack}>
                  <div className={styles.toggleKnob} />
                </div>
                <HighlightPulse style={{ left: -6, top: -6, right: -6, bottom: -6, borderRadius: 14 }} />
                <ClickDot style={{ right: -28, top: '50%', transform: 'translateY(-50%)' }} />
              </div>
            </div>
          </div>
        </div>
      </div>
    </MockupChrome>
  );
}

function Step5({ t }: { t: ReturnType<typeof useTranslations> }) {
  return (
    <MockupChrome url="chatgpt.com/apps#settings/Advanced">
      <div className={styles.mockupBody} style={{ minHeight: 480, position: 'relative', background: 'rgba(0,0,0,0.3)' }}>
        <div className={styles.modalOverlay}>
          <div className={styles.modal}>
            <h3 className={styles.modalTitle}>
              {t('mockup.newApp')} <span className={styles.modalBadge}>BETA</span>
              <span className={styles.modalCloseX}>&times;</span>
            </h3>
            <div className={styles.modalFieldLabel}>Name</div>
            <div className={`${styles.modalInput} ${styles.modalInputFocus}`} style={{ position: 'relative' }}>
              <span>Kura Training</span>
              <div className={styles.highlightPulseSmall} />
            </div>
            <div className={styles.modalFieldLabel}>{t('mockup.mcpServerUrl')}</div>
            <div className={`${styles.modalInput} ${styles.modalInputFocus}`} style={{ position: 'relative' }}>
              <span>{MCP_URL}</span>
              <div className={styles.highlightPulseSmall} />
            </div>
            <div className={styles.modalFieldLabel}>{t('mockup.authentication')}</div>
            <div className={styles.modalSelect} style={{ position: 'relative' }}>
              <span>OAuth</span>
              <span className={styles.chevron}>&#x25BC;</span>
              <div className={styles.highlightPulseSmall} />
            </div>
            <div className={styles.modalFieldLabel}>OAuth Client ID</div>
            <div className={`${styles.modalInput} ${styles.modalInputFocus}`} style={{ position: 'relative' }}>
              <span className={styles.placeholderText}>{t('mockup.providedByKura')}</span>
              <div className={styles.highlightPulseSmall} />
            </div>
            <div className={styles.modalFieldLabel}>OAuth Client Secret</div>
            <div className={`${styles.modalInput} ${styles.modalInputFocus}`} style={{ position: 'relative' }}>
              <span className={styles.placeholderText}>{t('mockup.providedByKura')}</span>
              <div className={styles.highlightPulseSmall} />
            </div>
          </div>
        </div>
      </div>
    </MockupChrome>
  );
}

function Step6({ t }: { t: ReturnType<typeof useTranslations> }) {
  return (
    <MockupChrome url="chatgpt.com/apps#settings/Advanced">
      <div className={styles.mockupBody} style={{ minHeight: 480, position: 'relative', background: 'rgba(0,0,0,0.3)' }}>
        <div className={styles.modalOverlay}>
          <div className={styles.modal}>
            <h3 className={styles.modalTitle}>
              {t('mockup.newApp')} <span className={styles.modalBadge}>BETA</span>
              <span className={styles.modalCloseX}>&times;</span>
            </h3>
            <div className={styles.modalFieldLabel}>Name</div>
            <div className={styles.modalInput}><span>Kura Training</span></div>
            <div className={styles.modalFieldLabel}>{t('mockup.mcpServerUrl')}</div>
            <div className={styles.modalInput}><span>{MCP_URL}</span></div>
            <div className={styles.modalFieldLabel}>{t('mockup.authentication')}</div>
            <div className={styles.modalSelect}><span>OAuth</span> <span className={styles.chevron}>&#x25BC;</span></div>
            <div className={styles.warningBox}>
              <div className={styles.warningHeader}>
                &#x26A0; {t('mockup.warningTitle')}
                <span style={{ textDecoration: 'underline' }}>{t('mockup.learnMore')}</span>
              </div>
              <div className={styles.warningCheck} style={{ position: 'relative' }}>
                <div className={`${styles.checkbox} ${styles.checkboxChecked}`}>&#10003;</div>
                <div>
                  <div className={styles.warningCheckTitle}>{t('mockup.understandContinue')}</div>
                  <div className={styles.warningCheckDesc}>{t('mockup.warningDesc')}</div>
                </div>
                <HighlightPulse style={{ left: -8, top: -8, right: -8, bottom: -8, borderRadius: 10 }} />
              </div>
            </div>
            <div className={styles.modalFooter}>
              <div className={styles.modalGuideLink}>&#x1F4C4; {t('mockup.readGuide')}</div>
              <div className={styles.modalBtnCreate} style={{ position: 'relative' }}>
                {t('mockup.create')}
                <ClickDot style={{ right: -22, top: 2 }} />
              </div>
            </div>
          </div>
        </div>
      </div>
    </MockupChrome>
  );
}

const STEPS = [Step1, Step2, Step3, Step4, Step5, Step6];

/* ── Main component ── */

export function ChatGPTGuide() {
  const t = useTranslations('setup.chatgpt');
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
      {/* Direct link */}
      <div className={styles.directLinkWrap}>
        <a href="https://chatgpt.com" target="_blank" rel="noreferrer" className={styles.directLink}>
          chatgpt.com &rarr;
        </a>
      </div>

      {/* Progress dots */}
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

      {/* Controls */}
      <div className={styles.controls}>
        <button className={styles.ctrlBtn} onClick={() => go(-1)}>
          &larr; {t('back')}
        </button>
        <button className={styles.ctrlBtn} onClick={() => go(1)}>
          {isLast ? `${t('restart')} \u21BB` : `${t('next')} \u2192`}
        </button>
      </div>

      {/* MCP URL copy */}
      <div className={styles.mcpCopyWrap}>
        <div className={styles.mcpCopy}>
          <code className={styles.mcpUrl}>{MCP_URL}</code>
          <button type="button" className="kura-btn kura-btn--ghost" onClick={copyUrl}>
            {copied ? tc('copied') : tc('copy')}
          </button>
        </div>
        <div className={styles.mcpHint}>{t('mcpCopyHint')}</div>
      </div>

      {/* Step content — click or swipe to advance */}
      {STEPS.map((StepComp, i) => (
        <div
          key={i}
          className={`${styles.stepWrapper} ${i === current ? styles.stepWrapperActive : ''}`}
          onClick={() => go(1)}
          onTouchStart={handleTouchStart}
          onTouchEnd={handleTouchEnd}
          style={{ cursor: 'pointer' }}
        >
          <div className={styles.stepLabel}>
            <BoldQuoted text={stepLabels[i]} />
          </div>
          <StepComp t={t} />
        </div>
      ))}

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
