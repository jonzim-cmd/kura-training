import { notFound } from 'next/navigation';
import { NextIntlClientProvider } from 'next-intl';
import { getMessages, getTranslations } from 'next-intl/server';
import { routing } from '@/i18n/routing';
import { AuthProvider } from '@/lib/auth-context';
import type { Metadata } from 'next';

const BASE_URL = 'https://withkura.com';
const LOCALES = routing.locales;
const DEFAULT_LOCALE = 'en';

type Props = {
  children: React.ReactNode;
  params: Promise<{ locale: string }>;
};

function localePath(locale: string): string {
  return locale === DEFAULT_LOCALE ? '' : `/${locale}`;
}

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { locale } = await params;
  const t = await getTranslations({ locale, namespace: 'meta' });

  const title = t('title');
  const description = t('description');
  const url = `${BASE_URL}${localePath(locale)}`;

  return {
    title,
    description,
    openGraph: {
      title,
      description,
      url,
      siteName: 'Kura',
      locale: locale.replace('-', '_'),
      type: 'website',
    },
    twitter: {
      card: 'summary_large_image',
      title,
      description,
    },
    alternates: {
      canonical: url,
      languages: Object.fromEntries(
        LOCALES.map((l) => [l, `${BASE_URL}${localePath(l)}`])
      ),
    },
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
