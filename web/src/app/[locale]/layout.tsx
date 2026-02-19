import { notFound } from 'next/navigation';
import { NextIntlClientProvider } from 'next-intl';
import { getMessages, getTranslations } from 'next-intl/server';
import { routing } from '@/i18n/routing';
import { AuthProvider } from '@/lib/auth-context';
import type { Metadata } from 'next';
import { BASE_URL } from '@/lib/seo';

const LOCALES = routing.locales;
const GOOGLE_ADS_TAG_ID = 'AW-17957929836';

type Props = {
  children: React.ReactNode;
  params: Promise<{ locale: string }>;
};

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { locale } = await params;
  const t = await getTranslations({ locale, namespace: 'meta' });

  const title = t('title');
  const description = t('description');

  return {
    title,
    description,
    robots: {
      index: true,
      follow: true,
    },
  };
}

const JSON_LD = {
  '@context': 'https://schema.org',
  '@graph': [
    {
      '@type': 'WebSite',
      name: 'Kura',
      url: BASE_URL,
      description: 'AI training diary and workout logger',
      inLanguage: [...LOCALES],
    },
    {
      '@type': 'SoftwareApplication',
      name: 'Kura',
      applicationCategory: 'HealthApplication',
      applicationSubCategory: 'Fitness',
      operatingSystem: 'Web',
      url: BASE_URL,
      description:
        'AI-powered training diary that structures, stores, and analyzes your workout data. Works with Claude, ChatGPT, and any AI agent.',
      offers: {
        '@type': 'Offer',
        price: '0',
        priceCurrency: 'USD',
        availability: 'https://schema.org/LimitedAvailability',
      },
      featureList: [
        'AI training log',
        'Workout tracking via natural language',
        'Training data analysis',
        'Works with Claude and ChatGPT',
        'Structured training data storage',
      ],
    },
    {
      '@type': 'Organization',
      name: 'Kura',
      url: BASE_URL,
      logo: `${BASE_URL}/icon.png`,
    },
  ],
};

const JSON_LD_STRING = JSON.stringify(JSON_LD);

export default async function LocaleLayout({ children, params }: Props) {
  const { locale } = await params;

  if (!routing.locales.includes(locale as any)) {
    notFound();
  }

  const messages = await getMessages();

  return (
    <html lang={locale}>
      <head>
        <script
          async
          src={`https://www.googletagmanager.com/gtag/js?id=${GOOGLE_ADS_TAG_ID}`}
        />
        <script
          dangerouslySetInnerHTML={{
            __html: `
              window.dataLayer = window.dataLayer || [];
              function gtag(){dataLayer.push(arguments);}
              gtag('js', new Date());
              gtag('config', '${GOOGLE_ADS_TAG_ID}');
            `,
          }}
        />
      </head>
      <body>
        <script
          type="application/ld+json"
          dangerouslySetInnerHTML={{ __html: JSON_LD_STRING }}
        />
        <NextIntlClientProvider messages={messages}>
          <AuthProvider>
            {children}
          </AuthProvider>
        </NextIntlClientProvider>
      </body>
    </html>
  );
}
