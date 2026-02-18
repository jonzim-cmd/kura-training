import { getTranslations } from 'next-intl/server';
import type { Metadata } from 'next';
import { buildPageMetadata } from '@/lib/seo';

type Props = {
  children: React.ReactNode;
  params: Promise<{ locale: string }>;
};

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { locale } = await params;
  const t = await getTranslations({ locale, namespace: 'requestAccess' });

  return buildPageMetadata({
    locale,
    internalPath: '/request-access',
    title: `${t('title')} | Kura`,
    description: t('subtitle'),
  });
}

export default function RequestAccessLayout({ children }: Props) {
  return children;
}
