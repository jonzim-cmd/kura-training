'use client';

import { useState, useCallback, useEffect } from 'react';
import { useTranslations, useLocale } from 'next-intl';
import { SignalMockup } from '@/components/SignalMockup';
import { getConversations } from './conversations';
import styles from './page.module.css';

export default function HowIUseItPage() {
  const t = useTranslations('howIUseIt');
  const locale = useLocale();
  const conversations = getConversations(locale);
  const [current, setCurrent] = useState(0);
  const total = conversations.length;

  const prev = useCallback(() => setCurrent((c) => Math.max(0, c - 1)), []);
  const next = useCallback(() => setCurrent((c) => Math.min(total - 1, c + 1)), [total]);

  /* Keyboard navigation */
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'ArrowLeft') prev();
      if (e.key === 'ArrowRight') next();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [prev, next]);

  /* Touch/swipe */
  useEffect(() => {
    let startX = 0;
    const onStart = (e: TouchEvent) => { startX = e.touches[0].clientX; };
    const onEnd = (e: TouchEvent) => {
      const dx = e.changedTouches[0].clientX - startX;
      if (dx > 50) prev();
      if (dx < -50) next();
    };
    window.addEventListener('touchstart', onStart, { passive: true });
    window.addEventListener('touchend', onEnd, { passive: true });
    return () => {
      window.removeEventListener('touchstart', onStart);
      window.removeEventListener('touchend', onEnd);
    };
  }, [prev, next]);

  return (
    <div className={styles.page}>
      <div className={`kura-container ${styles.container}`}>
        <div className={styles.header}>
          <h1 className={styles.title}>{t('title')}</h1>
          <p className={styles.subtitle}>{t('subtitle')}</p>
        </div>

        <div className={styles.carousel}>
          <div className={styles.slide}>
            <SignalMockup messages={conversations[current]} />
          </div>

          {total > 1 && (
            <div className={styles.nav}>
              <button className={styles.arrow} onClick={prev} disabled={current === 0} aria-label="Previous">
                ‹
              </button>
              <div className={styles.dots}>
                {conversations.map((_, i) => (
                  <button
                    key={i}
                    className={`${styles.dot} ${i === current ? styles.dotActive : ''}`}
                    onClick={() => setCurrent(i)}
                    aria-label={`Slide ${i + 1}`}
                  />
                ))}
              </div>
              <button className={styles.arrow} onClick={next} disabled={current === total - 1} aria-label="Next">
                ›
              </button>
            </div>
          )}
        </div>

        <div className={styles.tips}>
          <h2 className={styles.tipsTitle}>{t('tipsTitle')}</h2>
          <ol className={styles.tipsList}>
            {(t.raw('tips') as string[]).map((tip, i) => (
              <li key={i} className={styles.tip}>{tip}</li>
            ))}
          </ol>
        </div>
      </div>
    </div>
  );
}
