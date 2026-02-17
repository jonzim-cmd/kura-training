'use client';

import Script from 'next/script';
import { useEffect, useRef, useState } from 'react';

type TurnstileRenderOptions = {
  sitekey: string;
  action?: string;
  retry?: 'auto' | 'never';
  'refresh-expired'?: 'auto' | 'manual' | 'never';
  'refresh-timeout'?: 'auto' | 'manual' | 'never';
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
  const onTokenChangeRef = useRef(onTokenChange);
  const onUnavailableRef = useRef(onUnavailable);
  const [scriptReady, setScriptReady] = useState(() => (
    typeof window !== 'undefined' && Boolean(window.turnstile)
  ));

  useEffect(() => {
    onTokenChangeRef.current = onTokenChange;
  }, [onTokenChange]);

  useEffect(() => {
    onUnavailableRef.current = onUnavailable;
  }, [onUnavailable]);

  useEffect(() => {
    onTokenChangeRef.current(null);
  }, [action]);

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
        retry: 'never',
        'refresh-expired': 'manual',
        'refresh-timeout': 'manual',
        callback: (token: string) => onTokenChangeRef.current(token),
        'expired-callback': () => onTokenChangeRef.current(null),
        'timeout-callback': () => onTokenChangeRef.current(null),
        'error-callback': () => {
          onTokenChangeRef.current(null);
          onUnavailableRef.current?.();
        },
      });
    } catch {
      onTokenChangeRef.current(null);
      onUnavailableRef.current?.();
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
  }, [action, scriptReady, siteKey]);

  useEffect(() => {
    if (resetNonce === undefined) {
      return;
    }
    if (!window.turnstile || !widgetIdRef.current) {
      return;
    }
    try {
      window.turnstile.reset(widgetIdRef.current);
      onTokenChangeRef.current(null);
    } catch {
      onTokenChangeRef.current(null);
      onUnavailableRef.current?.();
    }
  }, [resetNonce]);

  return (
    <>
      <Script
        src="https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit"
        strategy="afterInteractive"
        onLoad={() => setScriptReady(true)}
        onError={() => {
          onTokenChangeRef.current(null);
          onUnavailableRef.current?.();
        }}
      />
      <div ref={containerRef} className={className} />
    </>
  );
}
