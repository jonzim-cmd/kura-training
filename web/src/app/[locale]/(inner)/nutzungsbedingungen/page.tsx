import styles from './nutzungsbedingungen.module.css';
import { getTermsContent } from '@/lib/legal-content';

type Props = {
  params: Promise<{ locale: string }>;
};

export default async function NutzungsbedingungenPage({ params }: Props) {
  const { locale } = await params;
  const content = getTermsContent(locale);

  return (
    <div className={styles.page}>
      <div className={`kura-container ${styles.container}`}>
        <h1 className={styles.title}>{content.title}</h1>
        <p className={styles.subtitle}>{content.subtitle}</p>
        <p className={styles.updated}>
          {content.updatedLabel}: {content.updatedAt}
        </p>

        {content.sections.map((section) => (
          <section key={section.title} className={styles.section}>
            <h2 className={styles.sectionTitle}>{section.title}</h2>
            {section.paragraphs?.map((paragraph) => (
              <p key={paragraph} className={styles.text}>
                {paragraph}
              </p>
            ))}
            {section.bullets && section.bullets.length > 0 ? (
              <ul className={styles.list}>
                {section.bullets.map((item) => (
                  <li key={item} className={styles.listItem}>
                    {item}
                  </li>
                ))}
              </ul>
            ) : null}
          </section>
        ))}
      </div>
    </div>
  );
}
