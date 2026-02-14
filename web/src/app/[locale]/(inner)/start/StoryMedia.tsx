'use client';

import { useState, useRef, useCallback } from 'react';
import Image from 'next/image';
import styles from './start.module.css';

export default function StoryMedia() {
  const [active, setActive] = useState(0);
  const touchStart = useRef<number | null>(null);

  const toggle = useCallback(() => setActive((a) => (a === 0 ? 1 : 0)), []);

  const onTouchStart = useCallback((e: React.TouchEvent) => {
    touchStart.current = e.touches[0].clientX;
  }, []);

  const onTouchEnd = useCallback((e: React.TouchEvent) => {
    if (touchStart.current === null) return;
    const dx = e.changedTouches[0].clientX - touchStart.current;
    if (Math.abs(dx) > 30) {
      setActive(dx < 0 ? 1 : 0);
    }
    touchStart.current = null;
  }, []);

  return (
    <div
      className={styles.storyMediaWrapper}
      onClick={toggle}
      onTouchStart={onTouchStart}
      onTouchEnd={onTouchEnd}
      role="group"
      aria-label="Photo and video carousel"
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
      </div>
      <div className={styles.dots}>
        <button
          className={`${styles.dot} ${active === 0 ? styles.dotActive : ''}`}
          onClick={(e) => { e.stopPropagation(); setActive(0); }}
          aria-label="Show photo"
        />
        <button
          className={`${styles.dot} ${active === 1 ? styles.dotActive : ''}`}
          onClick={(e) => { e.stopPropagation(); setActive(1); }}
          aria-label="Show video"
        />
      </div>
    </div>
  );
}
