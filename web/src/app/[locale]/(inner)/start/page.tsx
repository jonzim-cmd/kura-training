import { useTranslations } from 'next-intl';
import { Link } from '@/i18n/routing';
import styles from './start.module.css';
import StoryMedia from './StoryMedia';

export default function StartPage() {
  const t = useTranslations('start');
  const tn = useTranslations('nav');

  return (
    <div className={styles.page}>
      {/* Personal Story */}
      <section className={styles.storyWrapper}>
        <h1 className={styles.pageTitle}>{t('title')}</h1>
        <div className={styles.story}>
          <StoryMedia />
          <div className={styles.storyText}>
            <p className={styles.storyP}>{t('story.p1')}</p>
            <p className={styles.storyP}>{t('story.p2')}</p>
          </div>
        </div>
        <p className={styles.storyPStrong}>{t('story.p3')}</p>
      </section>

      {/* Training apps don't solve this */}
      <section className={styles.section}>
        <h2 className={styles.sectionTitle}>{t('apps.title')}</h2>
        <p className={styles.text}>{t('apps.text')}</p>
        <p className={styles.text}>{t('apps.bridge1')}</p>
        <p className={styles.text}>{t('apps.bridge2')}</p>
      </section>

      {/* Chat problems */}
      <section className={styles.section}>
        <h2 className={styles.sectionTitle}>{t('chat.title')}</h2>
        <div className={styles.problems}>
          {['forgets', 'zero', 'locked', 'text'].map((key) => (
            <div key={key} className={styles.problem}>
              <strong className={styles.problemTitle}>{t(`chat.${key}`)}</strong>
              <p className={styles.problemDesc}>{t(`chat.${key}Desc`)}</p>
            </div>
          ))}
        </div>
      </section>

      {/* What Kura changes */}
      <section className={styles.section}>
        <h2 className={styles.sectionTitle}>{t('solution.title')}</h2>
        <p className={styles.text}>{t('solution.p1')}</p>
        <p className={styles.text}>{t('solution.p2')}</p>
        <p className={styles.text}>{t('solution.p3')}</p>
        <p className={styles.text}>{t('solution.p4')}</p>
      </section>

      {/* How it works */}
      <section className={styles.section}>
        <h2 className={styles.sectionTitle}>{t('how.title')}</h2>
        <div className={styles.steps}>
          <div className={styles.step}>
            <span className={styles.stepNum}>1</span>
            <h3 className={styles.stepTitle}>{t('how.step1Title')}</h3>
            <p className={styles.stepDesc}>{t('how.step1Desc')}</p>
          </div>
          <div className={styles.step}>
            <span className={styles.stepNum}>2</span>
            <h3 className={styles.stepTitle}>{t('how.step2Title')}</h3>
            <p className={styles.stepDesc}>{t('how.step2Desc')}</p>
            <div className={styles.agents}>
              <span className={styles.agent}>Claude</span>
              <span className={styles.agent}>ChatGPT</span>
              <span className={styles.agent}>OpenClaw</span>
            </div>
            <Link href="/setup" className={styles.stepLink}>{t('how.step2SetupLink')}</Link>
          </div>
          <div className={styles.step}>
            <span className={styles.stepNum}>3</span>
            <h3 className={styles.stepTitle}>{t('how.step3Title')}</h3>
            <p className={styles.stepDesc}>{t('how.step3Desc')}</p>
          </div>
        </div>
      </section>

      {/* CTA */}
      <section className={styles.cta}>
        <Link href="/request-access" className="kura-btn kura-btn--primary">{tn('requestAccess')}</Link>
        <p className={styles.tagline}>{t('tagline')}</p>
      </section>
    </div>
  );
}
