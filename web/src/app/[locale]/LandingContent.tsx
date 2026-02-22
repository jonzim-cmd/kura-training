'use client';

import { useTranslations } from 'next-intl';
import { Link } from '@/i18n/routing';
import { useEffect, useRef, useState } from 'react';
import styles from './page.module.css';
import { Header } from '@/components/Header';

const SPORTS = ['strength', 'running', 'cycling'] as const;
type Sport = (typeof SPORTS)[number];

export default function LandingContent() {
  const t = useTranslations('landing');
  const tn = useTranslations('nav');
  const tf = useTranslations('footer');
  const [activeSport, setActiveSport] = useState<Sport>('strength');

  return (
    <div className={styles.descent}>
      <Header variant="landing" />

      {/* SURFACE */}
      <section className={styles.surface}>
        <div className={styles.surfaceInner}>
          <h1 className={styles.surfaceTitle}>KU<span style={{letterSpacing: '0.06em'}}>R</span>A</h1>
          <p className={styles.surfaceSubtitle}>{t('subtitle')}</p>
        </div>
      </section>

      {/* PHONE — User message */}
      <PhoneScene t={t} activeSport={activeSport} onSportClick={setActiveSport} />

      {/* Bridge — dots descending */}
      <Bridge t={t} />

      {/* THRESHOLD — Decomposition */}
      <Threshold t={t} activeSport={activeSport} />

      {/* INSIDE — Event + Projections */}
      <Inside t={t} activeSport={activeSport} />

      {/* DEEP — Inference */}
      <Deep t={t} activeSport={activeSport} />

      {/* CORE — Agent response */}
      <Core t={t} activeSport={activeSport} />

      {/* CTA */}
      <section className={styles.cta}>
        <Link href="/request-access" className={styles.ctaButton}>{t('getStarted')}</Link>
        <Link href="/start" className={styles.ctaWhyLink}>{tn('home')}</Link>
        <Link href="/how-i-use-it" className={styles.ctaWhyLink}>{tn('howIUseIt')}</Link>
      </section>

      {/* BOTTOM */}
      <section className={styles.bottom}>
        <div className={styles.bottomInner}>
          <div className={styles.bottomMark}>KU<span style={{letterSpacing: '0.06em'}}>R</span>A</div>
          <nav className={styles.bottomNav}>
            <Link href="/datenschutz" className={styles.bottomLink}>{tf('privacy')}</Link>
            <Link href="/nutzungsbedingungen" className={styles.bottomLink}>{tf('terms')}</Link>
            <Link href="/impressum" className={styles.bottomLink}>{tf('impressum')}</Link>
          </nav>
        </div>
      </section>
    </div>
  );
}

/* === PHONE SCENE === */
function PhoneScene({ t, activeSport, onSportClick }: { t: any; activeSport: Sport; onSportClick: (s: Sport) => void }) {
  const ref = useRef<HTMLElement>(null);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const obs = new IntersectionObserver(
      (entries) => {
        entries.forEach((e) => {
          if (e.isIntersecting) {
            const msg = el.querySelector('[data-msg]') as HTMLElement;
            const typing = el.querySelector('[data-typing]') as HTMLElement;
            if (msg) setTimeout(() => msg.classList.add(styles.visible), 300);
            if (typing) setTimeout(() => typing.classList.add(styles.visible), 1200);
            obs.unobserve(e.target);
          }
        });
      },
      { threshold: 0.4 }
    );
    obs.observe(el);
    return () => obs.disconnect();
  }, []);

  return (
    <section className={styles.phoneScene} ref={ref}>
      <div className={styles.sportTabs}>
        {SPORTS.map((sport) => (
          <button
            key={sport}
            className={`${styles.sportTab} ${activeSport === sport ? styles.sportTabActive : ''}`}
            onClick={() => onSportClick(sport)}
          >
            {t(`sportTabs.${sport}`)}
          </button>
        ))}
      </div>
      <div className={styles.phone}>
        <div className={styles.phoneNotch} />
        <div className={styles.chat}>
          <div className={`${styles.msg} ${styles.msgUser}`} data-msg>
            <span key={activeSport} className={styles.msgFade}>
              {t(`sports.${activeSport}.userMessage`)}
            </span>
          </div>
          <div className={styles.typing} data-typing>
            <span className={styles.typingDot} />
            <span className={styles.typingDot} />
            <span className={styles.typingDot} />
          </div>
        </div>
      </div>
    </section>
  );
}

/* === BRIDGE === */
function Bridge({ t }: { t: any }) {
  return (
    <div className={styles.bridge}>
      <div className={styles.dotsTrail}>
        {[0, 1, 2].map((i) => (
          <div key={i} className={styles.dotGroup}>
            <span className={styles.dot} />
            <span className={styles.dot} />
            <span className={styles.dot} />
          </div>
        ))}
        <div className={styles.bridgeLabel}>{t('bridgeLabel')}</div>
      </div>
    </div>
  );
}

/* === THRESHOLD === */
function Threshold({ t, activeSport }: { t: any; activeSport: Sport }) {
  const ref = useRef<HTMLElement>(null);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const obs = new IntersectionObserver(
      (entries) => {
        entries.forEach((e) => {
          if (e.isIntersecting) {
            el.querySelectorAll('[data-strata]').forEach((l) => l.classList.add(styles.strataVisible));
            const rows = el.querySelectorAll('[data-reveal]');
            rows.forEach((r, i) => {
              setTimeout(() => r.classList.add(styles.visible), i * 120);
            });
            obs.unobserve(e.target);
          }
        });
      },
      { threshold: 0.2 }
    );
    obs.observe(el);
    return () => obs.disconnect();
  }, []);

  const fieldsBySport: Record<Sport, Array<{ key: string; val: string; mono: boolean }>> = {
    strength: [
      { key: 'exercise_id', val: 'barbell_back_squat', mono: true },
      { key: 'sets_reps', val: '5 × 5', mono: false },
      { key: 'weight_kg', val: '100', mono: false },
      { key: 'rpe', val: '9 — 9.5', mono: false },
      { key: 'notes', val: t('decompNotes.strength'), mono: true },
      { key: 'also', val: t('decompAlso.strength'), mono: true },
    ],
    running: [
      { key: 'exercise_id', val: 'interval_run', mono: true },
      { key: 'intervals', val: '10 × 2min', mono: false },
      { key: 'pace_per_km', val: '4:00', mono: false },
      { key: 'rpe', val: '8', mono: false },
      { key: 'notes', val: t('decompNotes.running'), mono: true },
      { key: 'also', val: t('decompAlso.running'), mono: true },
    ],
    cycling: [
      { key: 'exercise_id', val: 'threshold_intervals', mono: true },
      { key: 'intervals', val: '3 × 10min', mono: false },
      { key: 'power_watts', val: '300', mono: false },
      { key: 'rpe', val: '~8.5', mono: false },
      { key: 'notes', val: t('decompNotes.cycling'), mono: true },
      { key: 'also', val: t('decompAlso.cycling'), mono: true },
    ],
  };
  const fields = fieldsBySport[activeSport];

  return (
    <section className={styles.threshold} ref={ref}>
      <div className={styles.strataLines}>
        {[0, 1, 2, 3, 4].map((i) => (
          <div key={i} className={styles.strataLine} data-strata style={{ '--idx': i } as React.CSSProperties} />
        ))}
      </div>
      <div className={styles.receiving}>
        <span className={styles.receivingLabel} data-reveal>{t('receiving')}</span>
        <div className={styles.decomposition}>
          {fields.map((f) => (
            <div key={f.key} className={styles.decompRow} data-reveal>
              <span className={styles.fieldName}>{t(`field.${f.key}`)}</span>
              <span className={f.mono ? styles.fieldValueMono : styles.fieldValue}>{f.val}</span>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}

/* === INSIDE === */
function Inside({ t, activeSport }: { t: any; activeSport: Sport }) {
  const ref = useRef<HTMLElement>(null);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;

    // Event lines
    const elObs = new IntersectionObserver(
      (entries) => {
        entries.forEach((e) => {
          if (e.isIntersecting) {
            el.querySelectorAll('[data-eline]').forEach((l) => l.classList.add(styles.visible));
            const svg = el.querySelector('[data-threads]') as HTMLElement;
            if (svg) svg.classList.add(styles.visible);
            elObs.unobserve(e.target);
          }
        });
      },
      { threshold: 0.1 }
    );
    elObs.observe(el);

    // JSON lines
    const jsonEl = el.querySelector('[data-json]');
    if (jsonEl) {
      const jsonObs = new IntersectionObserver(
        (entries) => {
          entries.forEach((e) => {
            if (e.isIntersecting) {
              const label = el.querySelector('[data-elabel]') as HTMLElement;
              if (label) label.classList.add(styles.visible);
              const lines = el.querySelectorAll('[data-jline]');
              lines.forEach((l, i) => {
                setTimeout(() => l.classList.add(styles.jlineTyped), i * 100);
              });
              jsonObs.unobserve(e.target);
            }
          });
        },
        { threshold: 0.25 }
      );
      jsonObs.observe(jsonEl);
    }

    // Projections
    el.querySelectorAll('[data-proj]').forEach((p, i) => {
      const pObs = new IntersectionObserver(
        (entries) => {
          entries.forEach((e) => {
            if (e.isIntersecting) {
              setTimeout(() => e.target.classList.add(styles.visible), i * 300);
              pObs.unobserve(e.target);
            }
          });
        },
        { threshold: 0.2 }
      );
      pObs.observe(p);
    });

    return () => elObs.disconnect();
  }, []);

  const eventDots = [
    [12, 28, 45, 63, 78],
    [8, 22, 38, 55, 72, 88],
    [15, 35, 52, 70, 90],
    [10, 30, 48, 65, 82],
    [18, 40, 58, 75],
    [25, 50, 80],
  ];
  const linePositions = [12, 28, 45, 62, 78, 91];

  return (
    <section className={styles.inside} ref={ref}>
      <div className={styles.eventLines}>
        {linePositions.map((pos, li) => (
          <div key={li} className={styles.eventLine} data-eline style={{ left: `${pos}%`, transitionDelay: `${li * 0.15}s` }}>
            {eventDots[li].map((top, di) => (
              <span key={di} className={styles.eventDot} style={{ top: `${top}%` }} />
            ))}
          </div>
        ))}
      </div>

      {[18, 38, 60, 78].map((top, i) => (
        <div key={i} className={styles.projBand} style={{ top: `${top}%`, height: `${[6, 8, 5, 7][i]}%` }} />
      ))}

      <svg data-threads className={styles.threadsSvg} viewBox="0 0 1440 2200" preserveAspectRatio="none">
        <line x1="173" y1="264" x2="403" y2="616" stroke="rgba(255,229,102,0.14)" strokeWidth="0.6" />
        <line x1="403" y1="484" x2="648" y2="836" stroke="rgba(255,229,102,0.09)" strokeWidth="0.6" />
        <line x1="648" y1="770" x2="893" y2="1144" stroke="rgba(255,229,102,0.12)" strokeWidth="0.6" />
        <line x1="893" y1="660" x2="1123" y2="990" stroke="rgba(255,229,102,0.07)" strokeWidth="0.6" />
        <line x1="173" y1="990" x2="648" y2="1430" stroke="rgba(255,229,102,0.09)" strokeWidth="0.6" />
        <line x1="1123" y1="1100" x2="893" y2="1650" stroke="rgba(255,229,102,0.12)" strokeWidth="0.6" />
      </svg>

      <div className={styles.eventWrite} data-json>
        <div className={styles.eventLabel} data-elabel>{t('persistingEvent')}</div>
        <div className={styles.eventJson}>
          {(activeSport === 'strength' ? [
            '{',
            <>&nbsp;&nbsp;<span className={styles.hl}>event_type</span>: <span className={styles.val}>&quot;set.logged&quot;</span>,</>,
            <>&nbsp;&nbsp;<span className={styles.hl}>data</span>: {'{'}</>,
            <>&nbsp;&nbsp;&nbsp;&nbsp;exercise_id: <span className={styles.val}>&quot;barbell_back_squat&quot;</span>,</>,
            <>&nbsp;&nbsp;&nbsp;&nbsp;sets: <span className={styles.val}>5</span>,</>,
            <>&nbsp;&nbsp;&nbsp;&nbsp;reps: <span className={styles.val}>5</span>,</>,
            <>&nbsp;&nbsp;&nbsp;&nbsp;weight_kg: <span className={styles.val}>100</span>,</>,
            <>&nbsp;&nbsp;&nbsp;&nbsp;rpe: <span className={styles.val}>9.5</span></>,
            <>&nbsp;&nbsp;{'}'},</>,
            <>&nbsp;&nbsp;<span className={styles.hl}>metadata</span>: {'{'}</>,
            <>&nbsp;&nbsp;&nbsp;&nbsp;session_id: <span className={styles.val}>&quot;019503a1-...&quot;</span>,</>,
            <>&nbsp;&nbsp;&nbsp;&nbsp;source: <span className={styles.val}>&quot;agent&quot;</span></>,
            <>&nbsp;&nbsp;{'}'}</>,
            '}',
          ] : activeSport === 'running' ? [
            '{',
            <>&nbsp;&nbsp;<span className={styles.hl}>event_type</span>: <span className={styles.val}>&quot;run.logged&quot;</span>,</>,
            <>&nbsp;&nbsp;<span className={styles.hl}>data</span>: {'{'}</>,
            <>&nbsp;&nbsp;&nbsp;&nbsp;exercise_id: <span className={styles.val}>&quot;interval_run&quot;</span>,</>,
            <>&nbsp;&nbsp;&nbsp;&nbsp;intervals: <span className={styles.val}>10</span>,</>,
            <>&nbsp;&nbsp;&nbsp;&nbsp;duration_sec: <span className={styles.val}>120</span>,</>,
            <>&nbsp;&nbsp;&nbsp;&nbsp;pace_per_km: <span className={styles.val}>&quot;4:00&quot;</span>,</>,
            <>&nbsp;&nbsp;&nbsp;&nbsp;rpe: <span className={styles.val}>8</span></>,
            <>&nbsp;&nbsp;{'}'},</>,
            <>&nbsp;&nbsp;<span className={styles.hl}>metadata</span>: {'{'}</>,
            <>&nbsp;&nbsp;&nbsp;&nbsp;session_id: <span className={styles.val}>&quot;019503a1-...&quot;</span>,</>,
            <>&nbsp;&nbsp;&nbsp;&nbsp;source: <span className={styles.val}>&quot;agent&quot;</span></>,
            <>&nbsp;&nbsp;{'}'}</>,
            '}',
          ] : [
            '{',
            <>&nbsp;&nbsp;<span className={styles.hl}>event_type</span>: <span className={styles.val}>&quot;ride.logged&quot;</span>,</>,
            <>&nbsp;&nbsp;<span className={styles.hl}>data</span>: {'{'}</>,
            <>&nbsp;&nbsp;&nbsp;&nbsp;exercise_id: <span className={styles.val}>&quot;threshold_intervals&quot;</span>,</>,
            <>&nbsp;&nbsp;&nbsp;&nbsp;intervals: <span className={styles.val}>3</span>,</>,
            <>&nbsp;&nbsp;&nbsp;&nbsp;duration_min: <span className={styles.val}>10</span>,</>,
            <>&nbsp;&nbsp;&nbsp;&nbsp;power_watts: <span className={styles.val}>300</span>,</>,
            <>&nbsp;&nbsp;&nbsp;&nbsp;rpe: <span className={styles.val}>8.5</span></>,
            <>&nbsp;&nbsp;{'}'},</>,
            <>&nbsp;&nbsp;<span className={styles.hl}>metadata</span>: {'{'}</>,
            <>&nbsp;&nbsp;&nbsp;&nbsp;session_id: <span className={styles.val}>&quot;019503a1-...&quot;</span>,</>,
            <>&nbsp;&nbsp;&nbsp;&nbsp;source: <span className={styles.val}>&quot;agent&quot;</span></>,
            <>&nbsp;&nbsp;{'}'}</>,
            '}',
          ]).map((line, i) => (
            <div key={i} className={styles.jline} data-jline>{line}</div>
          ))}
        </div>
      </div>

      <div className={styles.projCompute}>
        {activeSport === 'strength' ? (<>
          <div className={styles.projBlock} data-proj>
            <span className={styles.projLabel}>exercise_progression / barbell_back_squat</span>
            <div className={styles.projData}>
              estimated_1rm: <span className={styles.val}>116.7 kg</span> &nbsp;← <span className={styles.hl}>Epley</span><br />
              previous_1rm: <span className={styles.val}>116.7 kg</span><br />
              pr: <span className={styles.val}>false</span><br />
              trend: <span className={styles.alert}>plateau · 3 weeks</span><br />
              total_volume: <span className={styles.val}>2500 kg</span><br />
              session_count: <span className={styles.val}>14</span>
            </div>
          </div>
          <div className={styles.projBlock} data-proj>
            <span className={styles.projLabel}>training_timeline / overview</span>
            <div className={styles.projData}>
              week: <span className={styles.val}>2026-W07</span><br />
              sessions_this_week: <span className={styles.val}>3</span><br />
              frequency: <span className={styles.val}>3.2 /wk</span> (26w avg)<br />
              streak: <span className={styles.val}>12 days</span><br />
              top_set: <span className={styles.val}>barbell_back_squat 100kg × 5 → 116.7 e1rm</span>
            </div>
          </div>
          <div className={styles.projBlock} data-proj>
            <span className={styles.projLabel}>recovery / overview</span>
            <div className={styles.projData}>
              last_sleep: <span className={styles.val}>6.5h</span> &nbsp;(<span className={styles.alert}>{t('belowTarget')}</span>)<br />
              soreness: <span className={styles.val}>lower_back · 3/10</span><br />
              energy_trend: <span className={styles.alert}>declining · 5d</span>
            </div>
          </div>
        </>) : activeSport === 'running' ? (<>
          <div className={styles.projBlock} data-proj>
            <span className={styles.projLabel}>exercise_progression / interval_run</span>
            <div className={styles.projData}>
              interval_pace: <span className={styles.val}>4:00/km</span> &nbsp;← <span className={styles.hl}>current</span><br />
              previous_pace: <span className={styles.val}>4:12/km</span><br />
              pr: <span className={styles.val}>true</span> (pace)<br />
              trend: <span className={styles.val}>improving · 4 weeks</span><br />
              total_sessions: <span className={styles.val}>18</span><br />
              total_distance_km: <span className={styles.val}>142</span>
            </div>
          </div>
          <div className={styles.projBlock} data-proj>
            <span className={styles.projLabel}>training_timeline / overview</span>
            <div className={styles.projData}>
              week: <span className={styles.val}>2026-W07</span><br />
              sessions_this_week: <span className={styles.val}>4</span><br />
              frequency: <span className={styles.val}>3.8 /wk</span> (26w avg)<br />
              streak: <span className={styles.val}>9 days</span><br />
              top_set: <span className={styles.val}>interval_run 4:00/km × 2min</span>
            </div>
          </div>
          <div className={styles.projBlock} data-proj>
            <span className={styles.projLabel}>recovery / overview</span>
            <div className={styles.projData}>
              last_sleep: <span className={styles.val}>6.5h</span> &nbsp;(<span className={styles.alert}>{t('belowTarget')}</span>)<br />
              soreness: <span className={styles.val}>calves · 2/10</span><br />
              energy_trend: <span className={styles.alert}>declining · 5d</span>
            </div>
          </div>
        </>) : (<>
          <div className={styles.projBlock} data-proj>
            <span className={styles.projLabel}>exercise_progression / threshold_intervals</span>
            <div className={styles.projData}>
              avg_power: <span className={styles.val}>300 W</span> &nbsp;← <span className={styles.hl}>current</span><br />
              previous_avg: <span className={styles.val}>295 W</span><br />
              pr: <span className={styles.val}>true</span> (power)<br />
              trend: <span className={styles.val}>progressing · 2 weeks</span><br />
              ftp_estimate: <span className={styles.val}>295 W</span><br />
              session_count: <span className={styles.val}>12</span>
            </div>
          </div>
          <div className={styles.projBlock} data-proj>
            <span className={styles.projLabel}>training_timeline / overview</span>
            <div className={styles.projData}>
              week: <span className={styles.val}>2026-W07</span><br />
              sessions_this_week: <span className={styles.val}>3</span><br />
              frequency: <span className={styles.val}>3.0 /wk</span> (26w avg)<br />
              streak: <span className={styles.val}>8 days</span><br />
              top_set: <span className={styles.val}>threshold_intervals 300W × 10min</span>
            </div>
          </div>
          <div className={styles.projBlock} data-proj>
            <span className={styles.projLabel}>recovery / overview</span>
            <div className={styles.projData}>
              last_sleep: <span className={styles.val}>5.5h</span> &nbsp;(<span className={styles.alert}>{t('belowTarget')}</span>)<br />
              soreness: <span className={styles.val}>quads · 2/10</span><br />
              energy_trend: <span className={styles.alert}>declining · 3d</span>
            </div>
          </div>
        </>)}
      </div>
    </section>
  );
}

/* === DEEP === */
function Deep({ t, activeSport }: { t: any; activeSport: Sport }) {
  const ref = useRef<HTMLElement>(null);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const obs = new IntersectionObserver(
      (entries) => {
        entries.forEach((e) => {
          if (e.isIntersecting) {
            el.querySelectorAll('[data-fade]').forEach((f) => f.classList.add(styles.visible));
            const steps = el.querySelectorAll('[data-reveal]');
            steps.forEach((s, i) => setTimeout(() => s.classList.add(styles.visible), i * 200));
            obs.unobserve(e.target);
          }
        });
      },
      { threshold: 0.1 }
    );
    obs.observe(el);
    return () => obs.disconnect();
  }, []);

  return (
    <section className={styles.deep} ref={ref}>
      <div className={styles.mesh} data-fade>
        <svg viewBox="0 0 1440 1000" fill="none" preserveAspectRatio="xMidYMid slice">
          {[80, 180, 300, 420, 540, 680, 820].map((y, i) => (
            <path
              key={i}
              d={`M-20,${y} C${80 + i * 5},${y - 18} ${200 + i * 3},${y - 35} ${320 + i * 10},${y - 30} C${440 + i * 5},${y - 25} ${520 + i * 8},${y - 2} ${640 + i * 10},${y + 8} C${760 + i * 5},${y + 18} ${870 - i * 3},${y - 8} ${990 - i * 5},${y - 20} C${1100 + i * 5},${y - 28} ${1210 - i * 3},${y - 8} ${1330 + i * 5},${y + 2} C${1410 + i},${y + 8} 1460,${y - 2} 1460,${y - 5}`}
              stroke={`rgba(255,255,255,${0.04 + (i % 3) * 0.02})`}
              strokeWidth="0.8"
            />
          ))}
          {[173, 403, 648, 893, 1123].map((x, i) => (
            <line key={i} x1={x} y1="0" x2={x} y2="1000" stroke={`rgba(255,255,255,${0.03 + (i % 2) * 0.02})`} strokeWidth="0.5" />
          ))}
          <line x1="173" y1="180" x2="403" y2="300" stroke="rgba(255,229,102,0.07)" strokeWidth="0.5" />
          <line x1="648" y1="300" x2="893" y2="420" stroke="rgba(255,229,102,0.08)" strokeWidth="0.5" />
          <line x1="893" y1="540" x2="1123" y2="680" stroke="rgba(255,229,102,0.07)" strokeWidth="0.5" />
          <circle cx="173" cy="180" r="2" fill="rgba(255,229,102,0.25)" />
          <circle cx="403" cy="300" r="2.5" fill="rgba(255,229,102,0.3)" />
          <circle cx="648" cy="300" r="2" fill="rgba(255,229,102,0.2)" />
          <circle cx="893" cy="540" r="3" fill="rgba(255,229,102,0.35)" />
        </svg>
      </div>

      {[
        { w: 400, h: 400, top: '8%', left: '5%' },
        { w: 300, h: 300, top: '38%', right: '10%' },
        { w: 350, h: 350, bottom: '12%', left: '28%' },
      ].map((c, i) => (
        <div
          key={i}
          className={styles.inferenceCloud}
          data-fade
          style={{ width: c.w, height: c.h, top: c.top, left: c.left, right: (c as any).right, bottom: (c as any).bottom, animationDelay: `${-i * 2}s` }}
        />
      ))}

      <div className={styles.inferenceText}>
        {activeSport === 'strength' ? (<>
          <div className={styles.inferenceStep} data-reveal>
            <div className={styles.inferenceStepLabel}>{t('inference.signalDynamics')}</div>
            <div className={styles.inferenceStepContent}>
              squat_1rm.<span className={styles.hl}>velocity</span> = <span className={styles.val}>+0.0 kg/week</span><br />
              squat_1rm.<span className={styles.hl}>acceleration</span> = <span className={styles.val}>−2.1 kg/week²</span><br />
              squat_1rm.<span className={styles.hl}>trajectory</span> = <span className={styles.alert}>stagnating</span>
            </div>
          </div>
          <div className={styles.inferenceStep} data-reveal style={{ marginLeft: '15%' }}>
            <div className={styles.inferenceStepLabel}>{t('inference.crossDimension')}</div>
            <div className={styles.inferenceStepContent}>
              sleep.<span className={styles.hl}>deficit</span> = <span className={styles.alert}>−1h avg over 5d</span><br />
              energy.<span className={styles.hl}>trend</span> = <span className={styles.alert}>declining</span><br />
              volume.<span className={styles.hl}>load</span> = <span className={styles.val}>stable</span><br />
              <span className={styles.hl}>correlation</span>: recovery ↔ stagnation = <span className={styles.val}>0.72</span>
            </div>
          </div>
          <div className={styles.inferenceStep} data-reveal style={{ marginLeft: '8%' }}>
            <div className={styles.inferenceStepLabel}>{t('inference.recommendation')}</div>
            <div className={styles.inferenceStepContent}>
              <span className={styles.hl}>hypothesis</span>: accumulated fatigue → performance ceiling<br />
              <span className={styles.hl}>confidence</span>: <span className={styles.val}>0.78</span><br />
              <span className={styles.hl}>suggested</span>: deload_week | sleep_priority | volume_reduction
            </div>
          </div>
        </>) : activeSport === 'running' ? (<>
          <div className={styles.inferenceStep} data-reveal>
            <div className={styles.inferenceStepLabel}>{t('inference.signalDynamics')}</div>
            <div className={styles.inferenceStepContent}>
              interval_pace.<span className={styles.hl}>velocity</span> = <span className={styles.val}>−3 sec/km/week</span><br />
              interval_pace.<span className={styles.hl}>acceleration</span> = <span className={styles.val}>+1 sec/km/week²</span><br />
              interval_pace.<span className={styles.hl}>trajectory</span> = <span className={styles.val}>improving</span>
            </div>
          </div>
          <div className={styles.inferenceStep} data-reveal style={{ marginLeft: '15%' }}>
            <div className={styles.inferenceStepLabel}>{t('inference.crossDimension')}</div>
            <div className={styles.inferenceStepContent}>
              sleep.<span className={styles.hl}>deficit</span> = <span className={styles.alert}>−0.5h avg over 5d</span><br />
              rpe.<span className={styles.hl}>trend</span> = <span className={styles.alert}>rising</span><br />
              mileage.<span className={styles.hl}>load</span> = <span className={styles.val}>stable</span><br />
              <span className={styles.hl}>correlation</span>: sleep ↔ rpe_drift = <span className={styles.val}>0.68</span>
            </div>
          </div>
          <div className={styles.inferenceStep} data-reveal style={{ marginLeft: '8%' }}>
            <div className={styles.inferenceStepLabel}>{t('inference.recommendation')}</div>
            <div className={styles.inferenceStepContent}>
              <span className={styles.hl}>hypothesis</span>: fatigue accumulation → rpe drift<br />
              <span className={styles.hl}>confidence</span>: <span className={styles.val}>0.71</span><br />
              <span className={styles.hl}>suggested</span>: recovery_day | sleep_priority | reduce_intensity
            </div>
          </div>
        </>) : (<>
          <div className={styles.inferenceStep} data-reveal>
            <div className={styles.inferenceStepLabel}>{t('inference.signalDynamics')}</div>
            <div className={styles.inferenceStepContent}>
              threshold_power.<span className={styles.hl}>velocity</span> = <span className={styles.val}>+1.2 W/week</span><br />
              threshold_power.<span className={styles.hl}>acceleration</span> = <span className={styles.val}>−0.5 W/week²</span><br />
              threshold_power.<span className={styles.hl}>trajectory</span> = <span className={styles.val}>progressing</span>
            </div>
          </div>
          <div className={styles.inferenceStep} data-reveal style={{ marginLeft: '15%' }}>
            <div className={styles.inferenceStepLabel}>{t('inference.crossDimension')}</div>
            <div className={styles.inferenceStepContent}>
              sleep.<span className={styles.hl}>deficit</span> = <span className={styles.alert}>−1.5h last night</span><br />
              perceived_effort.<span className={styles.hl}>trend</span> = <span className={styles.alert}>elevated</span><br />
              training_load.<span className={styles.hl}>level</span> = <span className={styles.val}>stable</span><br />
              <span className={styles.hl}>correlation</span>: sleep ↔ perceived_effort = <span className={styles.val}>0.74</span>
            </div>
          </div>
          <div className={styles.inferenceStep} data-reveal style={{ marginLeft: '8%' }}>
            <div className={styles.inferenceStepLabel}>{t('inference.recommendation')}</div>
            <div className={styles.inferenceStepContent}>
              <span className={styles.hl}>hypothesis</span>: acute sleep impact → elevated RPE<br />
              <span className={styles.hl}>confidence</span>: <span className={styles.val}>0.82</span><br />
              <span className={styles.hl}>suggested</span>: sleep_priority | keep_plan | monitor_next
            </div>
          </div>
        </>)}
      </div>
    </section>
  );
}

/* === CORE — Agent Response === */
function Core({ t, activeSport }: { t: any; activeSport: Sport }) {
  const ref = useRef<HTMLElement>(null);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const obs = new IntersectionObserver(
      (entries) => {
        entries.forEach((e) => {
          if (e.isIntersecting) {
            const msg = el.querySelector('[data-amsg]') as HTMLElement;
            if (msg) setTimeout(() => msg.classList.add(styles.visible), 400);
            obs.unobserve(e.target);
          }
        });
      },
      { threshold: 0.3 }
    );
    obs.observe(el);
    return () => obs.disconnect();
  }, []);

  return (
    <section className={styles.core} ref={ref}>
      <div className={styles.phoneDark}>
        <div className={styles.phoneNotchDark} />
        <div className={styles.chatDark}>
          <div className={styles.msgUserEcho}>
            <span key={`echo-${activeSport}`} className={styles.msgFade}>
              {t(`sports.${activeSport}.userMessageShort`)}
            </span>
          </div>
          <div className={styles.msgAgent} data-amsg>
            <span key={`agent-${activeSport}`} className={styles.msgFade}>
              {t(`sports.${activeSport}.agentResponse`)}
              <br /><br />
              <strong>{t(`sports.${activeSport}.agentQuestion`)}</strong>
            </span>
          </div>
        </div>
      </div>
    </section>
  );
}
