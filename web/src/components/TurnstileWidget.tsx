'use client';

import Script from 'next/script';
import { useEffect, useRef, useState } from 'react';

type TurnstileRenderOptions = {
  sitekey: string;
  action?: string;
  callback?: (token: string) => void;
  'expired-callback'?: () => void;
  'timeout-callback'?: () => void;
  'error-callback'?: () => void;
};

type TurnstileApi = {
  render: (container: HTMLElement, options: TurnstileRenderOptions) => string;
  reset: (widgetId: string) => void;
  remove: (widgetId: string) => void;
};

declare global {
  interface Window {
    turnstile?: TurnstileApi;
  }
}

interface TurnstileWidgetProps {
  siteKey: string;
  action: string;
  resetNonce?: number;
  className?: string;
  onTokenChange: (token: string | null) => void;
  onUnavailable?: () => void;
}

export function TurnstileWidget({
  siteKey,
  action,
  resetNonce,
  className,
  onTokenChange,
  onUnavailable,
}: TurnstileWidgetProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const widgetIdRef = useRef<string | null>(null);
  const [scriptReady, setScriptReady] = useState(false);

  useEffect(() => {
    onTokenChange(null);
  }, [action, onTokenChange]);

  useEffect(() => {
    if (!scriptReady || !siteKey || !containerRef.current || !window.turnstile) {
      return;
    }

    try {
      if (widgetIdRef.current) {
        window.turnstile.remove(widgetIdRef.current);
      }

      widgetIdRef.current = window.turnstile.render(containerRef.current, {
        sitekey: siteKey,
        action,
        callback: (token: string) => onTokenChange(token),
        'expired-callback': () => onTokenChange(null),
        'timeout-callback': () => onTokenChange(null),
        'error-callback': () => onTokenChange(null),
      });
    } catch {
      onTokenChange(null);
      onUnavailable?.();
    }

    return () => {
      if (window.turnstile && widgetIdRef.current) {
        try {
          window.turnstile.remove(widgetIdRef.current);
        } catch {
          // no-op
        }
        widgetIdRef.current = null;
      }
    };
  }, [action, onTokenChange, onUnavailable, scriptReady, siteKey]);

  useEffect(() => {
    if (resetNonce === undefined) {
      return;
    }
    if (!window.turnstile || !widgetIdRef.current) {
      return;
    }
    try {
      window.turnstile.reset(widgetIdRef.current);
      onTokenChange(null);
    } catch {
      onTokenChange(null);
      onUnavailable?.();
    }
  }, [onTokenChange, onUnavailable, resetNonce]);

  return (
    <>
      <Script
        src="https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit"
        strategy="afterInteractive"
        onLoad={() => setScriptReady(true)}
        onError={() => {
          onTokenChange(null);
          onUnavailable?.();
        }}
      />
      <div ref={containerRef} className={className} />
    </>
  );
}
