import { getTranslations } from 'next-intl/server';
import type { Metadata } from 'next';
import { buildPageMetadata } from '@/lib/seo';

type Props = {
  children: React.ReactNode;
  params: Promise<{ locale: string }>;
};

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { locale } = await params;
  const tImpressum = await getTranslations({ locale, namespace: 'impressum' });
  const tMeta = await getTranslations({ locale, namespace: 'meta' });

  return buildPageMetadata({
    locale,
    internalPath: '/impressum',
    title: `${tImpressum('title')} | Kura`,
    description: tMeta('description'),
  });
}

export default function ImpressumLayout({ children }: Props) {
  return children;
}
