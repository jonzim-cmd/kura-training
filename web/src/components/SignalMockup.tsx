'use client';

import { type ReactNode } from 'react';
import styles from './SignalMockup.module.css';

/* ── Types ── */

export interface ChatMessage {
  from: 'user' | 'agent';
  text?: string;
  time?: string;
  /** Structured card (agent only) */
  card?: {
    emoji: string;
    title: string;
    body: ReactNode;
  };
}

/* ── Component ── */

export function SignalMockup({ messages }: { messages: ChatMessage[] }) {
  return (
    <div className={styles.mockup}>
      {/* Header */}
      <div className={styles.header}>
        <div className={styles.avatar}>F</div>
        <div className={styles.headerText}>
          <span className={styles.headerName}>Fred</span>
          <span className={styles.headerStatus}>OpenClaw</span>
        </div>
      </div>

      {/* Chat */}
      <div className={styles.chat}>
        {messages.map((msg, i) => {
          if (msg.from === 'user') {
            return (
              <div key={i} className={`${styles.bubbleRow} ${styles.bubbleRowOut}`}>
                <div className={`${styles.bubble} ${styles.bubbleOut}`}>
                  {msg.text}
                  {msg.time && (
                    <div className={styles.meta}>
                      <span className={styles.time}>{msg.time}</span>
                      <span className={styles.check}>✓✓</span>
                    </div>
                  )}
                </div>
              </div>
            );
          }

          /* Agent — card or plain bubble */
          if (msg.card) {
            return (
              <div key={i} className={`${styles.bubbleRow} ${styles.bubbleRowIn}`}>
                <div className={styles.card}>
                  <div className={styles.cardHeader}>
                    <span className={styles.cardEmoji}>{msg.card.emoji}</span>
                    {msg.card.title}
                  </div>
                  <div className={styles.cardBody}>{msg.card.body}</div>
                  <div className={styles.cardMeta}>
                    <span className={styles.cardReaction}>♡</span>
                    <span className={styles.cardReaction}>↩</span>
                    <span className={styles.cardReaction}>⋯</span>
                  </div>
                </div>
              </div>
            );
          }

          return (
            <div key={i} className={`${styles.bubbleRow} ${styles.bubbleRowIn}`}>
              <div className={`${styles.bubble} ${styles.bubbleIn}`}>
                {msg.text}
                {msg.time && (
                  <div className={styles.meta}>
                    <span className={styles.time}>{msg.time}</span>
                  </div>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
