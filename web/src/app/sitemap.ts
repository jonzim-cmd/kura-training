import { MetadataRoute } from 'next';
import { routing } from '@/i18n/routing';
import { languageAlternates, localeUrl } from '@/lib/seo';

const PAGES: ReadonlyArray<{
  path: string;
  changeFrequency: MetadataRoute.Sitemap[number]['changeFrequency'];
  priority: number;
}> = [
  { path: '/', changeFrequency: 'weekly', priority: 1.0 },
  { path: '/start', changeFrequency: 'monthly', priority: 0.9 },
  { path: '/request-access', changeFrequency: 'monthly', priority: 0.8 },
  { path: '/setup', changeFrequency: 'monthly', priority: 0.7 },
  { path: '/datenschutz', changeFrequency: 'yearly', priority: 0.3 },
  { path: '/nutzungsbedingungen', changeFrequency: 'yearly', priority: 0.3 },
  { path: '/impressum', changeFrequency: 'yearly', priority: 0.2 },
];

export default function sitemap(): MetadataRoute.Sitemap {
  return PAGES.flatMap((page) =>
    routing.locales.map((locale) => ({
      url: localeUrl(locale, page.path),
      lastModified: new Date(),
      changeFrequency: page.changeFrequency,
      priority: page.priority,
      alternates: {
        languages: languageAlternates(page.path),
      },
    }))
  );
}
