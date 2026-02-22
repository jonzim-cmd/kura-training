'use client';

import { useState, useRef, useCallback } from 'react';
import Image from 'next/image';
import { useLocale } from 'next-intl';
import { SignalMockup, type ChatMessage } from '@/components/SignalMockup';
import styles from './start.module.css';

const SLIDES = 3;

function getMockupMessages(locale: string): ChatMessage[] {
  if (locale === 'de') {
    return [
      { from: 'user', text: 'Pull ups 3x 8 RIR 0', time: '17:43' },
      {
        from: 'agent',
        text: '✅ Pull-ups geloggt (3×8, RIR 0 – besser als die geplanten 3×6!)\n\nNoch Ring Hold 1×60s, dann bist du durch! 💪',
        time: '17:44',
      },
    ];
  }
  if (locale === 'ja') {
    return [
      { from: 'user', text: 'Pull ups 3x 8 RIR 0', time: '17:43' },
      {
        from: 'agent',
        text: '✅ プルアップ記録済み（3×8、RIR 0 – 予定の3×6より上！）\n\nあとはリングホールド1×60sで完了！💪',
        time: '17:44',
      },
    ];
  }
  // en, en-US
  return [
    { from: 'user', text: 'Pull ups 3x 8 RIR 0', time: '17:43' },
    {
      from: 'agent',
      text: '✅ Pull-ups logged (3×8, RIR 0 – better than the planned 3×6!)\n\nRing Hold 1×60s left, then you\'re done! 💪',
      time: '17:44',
    },
  ];
}

export default function StoryMedia() {
  const locale = useLocale();
  const [active, setActive] = useState(0);
  const touchStart = useRef<number | null>(null);
  const messages = getMockupMessages(locale);

  const next = useCallback(() => setActive((a) => (a + 1) % SLIDES), []);

  const onTouchStart = useCallback((e: React.TouchEvent) => {
    touchStart.current = e.touches[0].clientX;
  }, []);

  const onTouchEnd = useCallback((e: React.TouchEvent) => {
    if (touchStart.current === null) return;
    const dx = e.changedTouches[0].clientX - touchStart.current;
    if (Math.abs(dx) > 30) {
      setActive((a) => (dx < 0 ? (a + 1) % SLIDES : (a - 1 + SLIDES) % SLIDES));
    }
    touchStart.current = null;
  }, []);

  return (
    <div
      className={styles.storyMediaWrapper}
      onClick={next}
      onTouchStart={onTouchStart}
      onTouchEnd={onTouchEnd}
      role="group"
      aria-label="Photo, video and chat carousel"
    >
      <div className={styles.storyMedia}>
        <Image
          src="/images/founder.jpg"
          alt=""
          width={480}
          height={600}
          className={`${styles.photo} ${styles.mediaLayer} ${active === 0 ? styles.mediaVisible : ''}`}
        />
        <video
          autoPlay
          loop
          muted
          playsInline
          preload="metadata"
          className={`${styles.mediaLayer} ${active === 1 ? styles.mediaVisible : ''}`}
        >
          <source src="/videos/kura-demo.mp4" type="video/mp4" />
        </video>
        <div className={`${styles.mediaLayer} ${styles.mockupLayer} ${active === 2 ? styles.mediaVisible : ''}`}>
          <SignalMockup messages={messages} />
        </div>
      </div>
      <div className={styles.dots}>
        {Array.from({ length: SLIDES }, (_, i) => (
          <button
            key={i}
            className={`${styles.dot} ${active === i ? styles.dotActive : ''}`}
            onClick={(e) => { e.stopPropagation(); setActive(i); }}
            aria-label={`Slide ${i + 1}`}
          />
        ))}
      </div>
    </div>
  );
}
