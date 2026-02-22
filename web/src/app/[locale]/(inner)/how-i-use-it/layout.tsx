import { getTranslations } from 'next-intl/server';
import type { Metadata } from 'next';
import { buildPageMetadata } from '@/lib/seo';

type Props = {
  children: React.ReactNode;
  params: Promise<{ locale: string }>;
};

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { locale } = await params;
  const t = await getTranslations({ locale, namespace: 'howIUseIt' });

  return buildPageMetadata({
    locale,
    internalPath: '/how-i-use-it',
    title: `${t('title')} | Kura`,
    description: t('subtitle'),
  });
}

export default function HowIUseItLayout({ children }: Props) {
  return children;
}
