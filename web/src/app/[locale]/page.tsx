import { getTranslations } from 'next-intl/server';
import { routing } from '@/i18n/routing';
import type { Metadata } from 'next';
import LandingContent from './LandingContent';

const BASE_URL = 'https://withkura.com';

type Props = {
  params: Promise<{ locale: string }>;
};

function localePath(locale: string): string {
  return locale === 'en' ? '' : `/${locale}`;
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
        routing.locales.map((l) => [l, `${BASE_URL}${localePath(l)}`])
      ),
    },
  };
}

export default function LandingPage() {
  return <LandingContent />;
}
