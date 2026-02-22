/**
 * Conversation data for the "How I Use It" carousel.
 *
 * Add new conversations here — each one becomes a carousel slide.
 * The SignalMockup component renders them as Signal-style chat bubbles.
 *
 * These are real conversations, translated 1:1 — content is not altered.
 */

import type { ChatMessage } from '@/components/SignalMockup';
import s from '@/components/SignalMockup.module.css';

type LocaleConversations = ChatMessage[][];

/* ═══════════════════════════════════════════════════════════
   DEUTSCH (Original)
   ═══════════════════════════════════════════════════════════ */

const de: LocaleConversations = [

  /* ── 1. Morgen-Check-in ── */
  [
    {
      from: 'user',
      text: 'Schlaf: 7,5h Note 5\nSoreness: 3',
      time: '7:20',
    },
    {
      from: 'agent',
      card: {
        emoji: '✅',
        title: 'Geloggt!',
        body: (
          <>
            <div className={s.sectionTitle}>Heute morgen (12.02):</div>
            <ul className={s.bulletList}>
              <li>Sleep: 7,5h, Quality 5/10 (mittelmäßig)</li>
              <li>Soreness: 3/10 (leicht - wahrscheinlich von Session 1)</li>
            </ul>
            <div className={s.sectionTitle}>Quick Check:</div>
            <ul className={s.bulletList}>
              <li>Sleep Duration okay (+0,5h vs gestern)</li>
              <li>Quality leicht schlechter (7 → 5)</li>
              <li>Soreness ist da, aber moderat</li>
            </ul>
            <p>Noch <strong>Energy-Level</strong> für heute morgen? (1-10)</p>
            <p>Dann kann ich dir die <strong>Readiness für Session 2</strong> ausrechnen! 💪</p>
          </>
        ),
      },
    },
  ],

  /* ── 2. Trainingsplan abrufen ── */
  [
    {
      from: 'user',
      text: 'Wie sieht mein Training am Freitag, brauchst nicht ins Detail gehen?',
      time: '14:22',
    },
    {
      from: 'agent',
      card: {
        emoji: '🦊',
        title: 'Session: Heavy Power (Freitag)',
        body: (
          <>
            <div className={s.sectionTitle}>1. Box Jumps</div>
            <ul className={s.bulletList}>
              <li>Sätze x Reps: 3 x 3–5</li>
              <li>Pause: 2 Min.</li>
              <li>Fokus: Maximale Höhe, Primer fürs ZNS.</li>
            </ul>
            <div className={s.sectionTitle}>2. Hang Power Clean</div>
            <ul className={s.bulletList}>
              <li>Sätze x Reps: 4–5 x 2–3</li>
              <li>Pause: 3 Min.</li>
              <li>Fokus: 70–75 kg. &quot;Power&quot; Catch (hoch fangen).</li>
            </ul>
            <div className={s.sectionTitle}>3. Front Squat</div>
            <ul className={s.bulletList}>
              <li>Sätze x Reps: 3–4 x 5</li>
              <li>Pause: 3 Min.</li>
              <li>Fokus: 80 kg. Aufrechte Brust, Ellbogen hoch.</li>
            </ul>
            <div className={s.sectionTitle}>4. Klimmzüge</div>
            <ul className={s.bulletList}>
              <li>Sätze x Reps: 3 x RIR 1–2</li>
              <li>Pause: 2 Min.</li>
              <li>Fokus: Volle ROM (Aushängen bis Kinn über Stange).</li>
            </ul>
          </>
        ),
      },
    },
  ],

  /* ── 3. Progression erkennen ── */
  [
    {
      from: 'user',
      text: 'Ist schon irgendwo Progression zu sehen oder ob das Training wirkt?',
      time: '16:03',
    },
    {
      from: 'agent',
      card: {
        emoji: '📈',
        title: 'Progression',
        body: (
          <>
            <p>Ja, es gibt eine sehr klare Progression, wenn wir uns die letzten zwei Wochen (Woche 07 und Woche 08) anschauen. 🦊📈</p>
            <p>Da du nach &quot;Wirkung&quot; fragst, hier die Highlights aus deinen Daten:</p>
            <div className={s.sectionTitle}>1. Die &quot;Dunk-Power&quot; (Plyometric Load)</div>
            <ul className={s.bulletList}>
              <li><strong>Volumen-Shift:</strong> In Woche 07 (letzte Woche) hast du 64 Sätze geloggt, diese Woche sind wir bei einem Trend zu höherer Intensität (weniger Sätze, aber spezifischer).</li>
              <li><strong>Plyo-Output:</strong> Dein plyometric Load-Score ist von fast Null auf einen stabilen Trend gestiegen. Das zeigt, dass du jetzt die &quot;Spezialisierung&quot; auf Sprungkraft wirklich durchziehst.</li>
              <li><strong>Voraussetzung:</strong> Deine Readiness ist heute bei 0.74 (High). Das Training wirkt also bisher nicht überlastend, sondern du bist in einem guten &quot;Superkompensations-Fenster&quot;.</li>
            </ul>
            <div className={s.sectionTitle}>2. Kraft-Entwicklung (e1RM-Trends)</div>
            <p>Kura sieht bei zwei Übungen einen deutlichen Aufwärtstrend in der Strength Inference:</p>
            <ul className={s.bulletList}>
              <li><strong>Front Squat:</strong> Du hast dich von einer Baseline stabil bei 80 kg etabliert. Das e1RM ist jetzt auf 93.3 kg gesprungen. Das System sieht hier eine positive Beschleunigung (&quot;trajectory_code: improving&quot;).</li>
              <li><strong>Hang Power Clean:</strong> Hier zeigt die Kurve von ca. 70 kg auf ein e1RM von 82.2 kg. Das ist eine Steigerung der explosiven Kraftbasis um ca. 15% in nur 10 Tagen.</li>
            </ul>
          </>
        ),
      },
    },
  ],

  /* ── 4. Live-Coaching: Technik + Anpassung ── */
  [
    {
      from: 'user',
      text: 'ich bin im Keller. Ich würde bei approach jumps zwei schritte Anlauf machen und dann gestreckte Beine auf 60-65 cm plyo box',
      time: '15:49',
    },
    {
      from: 'agent',
      card: {
        emoji: '🦊',
        title: 'Dunk-Training Pattern',
        body: (
          <>
            <p>Perfekt! <strong>Das ist genau das richtige Pattern</strong> für Dunk-Training:</p>
            <p>✅ <strong>2 Schritte Anlauf</strong> - trainiert den Approach wie beim echten Dunk<br />
            ✅ <strong>Gestreckte Beine</strong> - realistisches Pattern (nicht Tuck wie bei normalen Box Jumps)<br />
            ✅ <strong>60-65cm</strong> - passt zu deinem aktuellen CMJ-Level</p>
            <p><strong>Das ist besser als die geplanten &quot;Approach Jumps 4×3&quot;</strong> - du kombinierst beides (Approach + Box Jump Straight Legs) in einer Übung!</p>
            <p><strong>Wie viele Sets/Reps planst du?</strong> 4×3 wie im Plan oder anpassen?</p>
            <p>Viel Erfolg! 🦊💪</p>
          </>
        ),
      },
    },
    {
      from: 'user',
      text: '4x 3 passen glaub. ich werde sie im wechsel mit hang power snatch machen. ein satz snatch, ein satz jumps usw',
      time: '15:51',
    },
  ],

  /* ── 5. Übungen umbenennen (Aliase) ── */
  [
    {
      from: 'user',
      text: 'ich glaube es wäre besser die übung auf bss safety umzubennen, oder? und BSS smith machine? und BSS barbell? das ist ja sonst schwer vergleichbar, was meinst du?',
      time: '4 Min.',
    },
    {
      from: 'agent',
      card: {
        emoji: '🦊',
        title: 'Exercise Aliase',
        body: (
          <>
            <p>Das ist ein absolut richtiger Gedanke. Wenn du alles unter <strong>bulgarian_split_squat</strong> (BSS) loggst, &quot;vermischt&quot; Kura die Kraftkurven. Ein 1RM an der Smith Machine ist biomechanisch nicht direkt mit der freien Langhantel oder der Safety Bar vergleichbar (Stabilität, Pfad der Hantel, Hebelarm).</p>
            <p>Für eine saubere <strong>Strength Inference</strong> (Kraftentwicklung über Zeit) sollten wir das trennen.</p>
            <div className={s.sectionTitle}>Mein Vorschlag für die neuen Namen (Aliase):</div>
            <p>1. <strong>bulgarian_split_squat_ssb</strong> (Safety Bar)<br />
            2. <strong>bulgarian_split_squat_smith</strong> (Smith Machine)<br />
            3. <strong>bulgarian_split_squat_barbell</strong> (Klassische Langhantel)<br />
            4. <strong>bulgarian_split_squat_db</strong> (Kurzhanteln)</p>
            <div className={s.sectionTitle}>Warum das für dich (und Kura) besser ist:</div>
            <ul className={s.bulletList}>
              <li><strong>Präzision:</strong> Kura berechnet für jede Übung ein eigenes e1RM. Du siehst dann genau: &quot;An der Smith Machine schaffe ich X, mit der SSB aber Y&quot;.</li>
            </ul>
          </>
        ),
      },
    },
  ],

  /* ── 6. Training-Muster (bestehend) ── */
  [
    {
      from: 'user',
      text: 'Zeigen meine Log Daten schon interessante pattern?',
      time: '22:51',
    },
    {
      from: 'agent',
      card: {
        emoji: '📊',
        title: 'Kura Log-Muster',
        body: (
          <>
            <p>Ja, da zeichnen sich ein paar klare Muster ab:</p>
            <div className={s.sectionTitle}>1. Readiness im Sinkflug</div>
            <ul className={s.bulletList}>
              <li>Deine Readiness ist von ~83% (17.02.) auf <strong>57% (heute)</strong> gefallen.</li>
              <li><strong>Grund:</strong> Hoher Load (zwei harte Sessions 20./21.02.) + wenig Schlaf (6h letzte Nacht).</li>
              <li>Trend: „Decelerating / Down". Der morgige Ruhetag ist also physiologisch notwendig.</li>
            </ul>
            <div className={s.sectionTitle}>2. Schlaf als Engpass</div>
            <ul className={s.bulletList}>
              <li>Schnitt letzte Woche: 7.3h → diese Woche: <strong>6.0h</strong>.</li>
              <li>Qualität schwankt (5/10 heute). Das korreliert direkt mit dem Energie-Level (von 7/10 auf 5/10 gefallen).</li>
            </ul>
            <div className={s.sectionTitle}>3. Training Load Peaks</div>
            <ul className={s.bulletList}>
              <li>Gestern (Fr): <strong>Load Score 45.2</strong> (Front Squat 5x80kg, Clean 4x72.5kg)</li>
              <li>Heute (Sa): <strong>Load Score 40.9</strong> (Split Squats 6x50kg, Push Press Eccentric 5x60kg)</li>
              <li>Das sind zwei sehr volumenreiche Tage hintereinander. Der „Jump Focus" am Montag braucht Frische, also nutz den Sonntag wirklich zum Aufladen.</li>
            </ul>
          </>
        ),
      },
    },
  ],
];

/* ═══════════════════════════════════════════════════════════
   ENGLISH (1:1 translation — content not altered)
   ═══════════════════════════════════════════════════════════ */

const en: LocaleConversations = [

  /* ── 1. Morning Check-in ── */
  [
    {
      from: 'user',
      text: 'Sleep: 7.5h rating 5\nSoreness: 3',
      time: '7:20',
    },
    {
      from: 'agent',
      card: {
        emoji: '✅',
        title: 'Logged!',
        body: (
          <>
            <div className={s.sectionTitle}>This morning (Feb 12):</div>
            <ul className={s.bulletList}>
              <li>Sleep: 7.5h, Quality 5/10 (mediocre)</li>
              <li>Soreness: 3/10 (mild - probably from Session 1)</li>
            </ul>
            <div className={s.sectionTitle}>Quick Check:</div>
            <ul className={s.bulletList}>
              <li>Sleep duration okay (+0.5h vs yesterday)</li>
              <li>Quality slightly worse (7 → 5)</li>
              <li>Soreness is there, but moderate</li>
            </ul>
            <p>How about your <strong>energy level</strong> this morning? (1-10)</p>
            <p>Then I can calculate your <strong>readiness for Session 2</strong>! 💪</p>
          </>
        ),
      },
    },
  ],

  /* ── 2. Training plan lookup ── */
  [
    {
      from: 'user',
      text: "What does my Friday training look like, no need to go into detail?",
      time: '14:22',
    },
    {
      from: 'agent',
      card: {
        emoji: '🦊',
        title: 'Session: Heavy Power (Friday)',
        body: (
          <>
            <div className={s.sectionTitle}>1. Box Jumps</div>
            <ul className={s.bulletList}>
              <li>Sets x Reps: 3 x 3–5</li>
              <li>Rest: 2 min</li>
              <li>Focus: Maximum height, CNS primer.</li>
            </ul>
            <div className={s.sectionTitle}>2. Hang Power Clean</div>
            <ul className={s.bulletList}>
              <li>Sets x Reps: 4–5 x 2–3</li>
              <li>Rest: 3 min</li>
              <li>Focus: 70–75 kg. &quot;Power&quot; catch (catch high).</li>
            </ul>
            <div className={s.sectionTitle}>3. Front Squat</div>
            <ul className={s.bulletList}>
              <li>Sets x Reps: 3–4 x 5</li>
              <li>Rest: 3 min</li>
              <li>Focus: 80 kg. Upright chest, elbows high.</li>
            </ul>
            <div className={s.sectionTitle}>4. Pull-ups</div>
            <ul className={s.bulletList}>
              <li>Sets x Reps: 3 x RIR 1–2</li>
              <li>Rest: 2 min</li>
              <li>Focus: Full ROM (dead hang to chin over bar).</li>
            </ul>
          </>
        ),
      },
    },
  ],

  /* ── 3. Spotting progression ── */
  [
    {
      from: 'user',
      text: 'Is there any progression visible yet, or is the training actually working?',
      time: '16:03',
    },
    {
      from: 'agent',
      card: {
        emoji: '📈',
        title: 'Progression',
        body: (
          <>
            <p>Yes, there is very clear progression when we look at the last two weeks (Week 07 and Week 08). 🦊📈</p>
            <p>Since you&apos;re asking about &quot;results&quot;, here are the highlights from your data:</p>
            <div className={s.sectionTitle}>1. &quot;Dunk Power&quot; (Plyometric Load)</div>
            <ul className={s.bulletList}>
              <li><strong>Volume shift:</strong> In Week 07 (last week) you logged 64 sets, this week we&apos;re trending toward higher intensity (fewer sets, but more specific).</li>
              <li><strong>Plyo output:</strong> Your plyometric load score has risen from nearly zero to a stable trend. This shows you&apos;re really committing to the jump power specialization now.</li>
              <li><strong>Prerequisite:</strong> Your readiness is at 0.74 (High) today. So the training isn&apos;t overloading you — you&apos;re in a good &quot;supercompensation window&quot;.</li>
            </ul>
            <div className={s.sectionTitle}>2. Strength Development (e1RM Trends)</div>
            <p>Kura sees a clear upward trend in Strength Inference for two exercises:</p>
            <ul className={s.bulletList}>
              <li><strong>Front Squat:</strong> You&apos;ve established a stable baseline at 80 kg. The e1RM has now jumped to 93.3 kg. The system sees positive acceleration here (&quot;trajectory_code: improving&quot;).</li>
              <li><strong>Hang Power Clean:</strong> The curve shows from about 70 kg to an e1RM of 82.2 kg. That&apos;s an increase in explosive strength base of about 15% in just 10 days.</li>
            </ul>
          </>
        ),
      },
    },
  ],

  /* ── 4. Live coaching: technique + adaptation ── */
  [
    {
      from: 'user',
      text: "I'm in the basement. For approach jumps I'd do a two-step run-up and then straight legs onto the 60-65 cm plyo box",
      time: '15:49',
    },
    {
      from: 'agent',
      card: {
        emoji: '🦊',
        title: 'Dunk Training Pattern',
        body: (
          <>
            <p>Perfect! <strong>That&apos;s exactly the right pattern</strong> for dunk training:</p>
            <p>✅ <strong>2-step run-up</strong> - trains the approach like a real dunk<br />
            ✅ <strong>Straight legs</strong> - realistic pattern (not tuck like normal box jumps)<br />
            ✅ <strong>60-65cm</strong> - matches your current CMJ level</p>
            <p><strong>This is better than the planned &quot;Approach Jumps 4×3&quot;</strong> - you&apos;re combining both (approach + box jump straight legs) in one exercise!</p>
            <p><strong>How many sets/reps are you planning?</strong> 4×3 as planned or adjust?</p>
            <p>Good luck! 🦊💪</p>
          </>
        ),
      },
    },
    {
      from: 'user',
      text: "4x3 should work I think. I'll alternate them with hang power snatch. one set snatch, one set jumps etc",
      time: '15:51',
    },
  ],

  /* ── 5. Renaming exercises (aliases) ── */
  [
    {
      from: 'user',
      text: "I think it would be better to rename the exercise to bss safety, right? and BSS smith machine? and BSS barbell? otherwise it's hard to compare, what do you think?",
      time: '4 min',
    },
    {
      from: 'agent',
      card: {
        emoji: '🦊',
        title: 'Exercise Aliases',
        body: (
          <>
            <p>That&apos;s an absolutely right thought. If you log everything under <strong>bulgarian_split_squat</strong> (BSS), Kura &quot;mixes up&quot; the strength curves. A 1RM on the Smith Machine is biomechanically not directly comparable to the free barbell or the Safety Bar (stability, bar path, lever arm).</p>
            <p>For a clean <strong>Strength Inference</strong> (strength development over time) we should separate them.</p>
            <div className={s.sectionTitle}>My suggestion for the new names (aliases):</div>
            <p>1. <strong>bulgarian_split_squat_ssb</strong> (Safety Bar)<br />
            2. <strong>bulgarian_split_squat_smith</strong> (Smith Machine)<br />
            3. <strong>bulgarian_split_squat_barbell</strong> (Classic Barbell)<br />
            4. <strong>bulgarian_split_squat_db</strong> (Dumbbells)</p>
            <div className={s.sectionTitle}>Why this is better for you (and Kura):</div>
            <ul className={s.bulletList}>
              <li><strong>Precision:</strong> Kura calculates a separate e1RM for each exercise. You&apos;ll then see exactly: &quot;On the Smith Machine I can do X, but with the SSB it&apos;s Y&quot;.</li>
            </ul>
          </>
        ),
      },
    },
  ],

  /* ── 6. Training patterns (existing) ── */
  [
    {
      from: 'user',
      text: 'Is my log data showing any interesting patterns yet?',
      time: '22:51',
    },
    {
      from: 'agent',
      card: {
        emoji: '📊',
        title: 'Kura Log Patterns',
        body: (
          <>
            <p>Yes, a few clear patterns are emerging:</p>
            <div className={s.sectionTitle}>1. Readiness Declining</div>
            <ul className={s.bulletList}>
              <li>Your readiness has dropped from ~83% (Feb 17) to <strong>57% (today)</strong>.</li>
              <li><strong>Reason:</strong> High load (two hard sessions Feb 20/21) + little sleep (6h last night).</li>
              <li>Trend: &quot;Decelerating / Down&quot;. Tomorrow&apos;s rest day is physiologically necessary.</li>
            </ul>
            <div className={s.sectionTitle}>2. Sleep as Bottleneck</div>
            <ul className={s.bulletList}>
              <li>Average last week: 7.3h → this week: <strong>6.0h</strong>.</li>
              <li>Quality is fluctuating (5/10 today). This correlates directly with energy level (dropped from 7/10 to 5/10).</li>
            </ul>
            <div className={s.sectionTitle}>3. Training Load Peaks</div>
            <ul className={s.bulletList}>
              <li>Yesterday (Fri): <strong>Load Score 45.2</strong> (Front Squat 5x80kg, Clean 4x72.5kg)</li>
              <li>Today (Sat): <strong>Load Score 40.9</strong> (Split Squats 6x50kg, Push Press Eccentric 5x60kg)</li>
              <li>That&apos;s two very high-volume days back to back. The &quot;Jump Focus&quot; on Monday needs freshness, so really use Sunday to recharge.</li>
            </ul>
          </>
        ),
      },
    },
  ],
];

/* ═══════════════════════════════════════════════════════════ */

const all: Record<string, LocaleConversations> = { de, en, 'en-US': en };

export function getConversations(locale: string): ChatMessage[][] {
  return all[locale] ?? en;
}
