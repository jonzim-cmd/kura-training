import { MetadataRoute } from 'next';

const BASE_URL = 'https://withkura.com';
const LOCALES = ['en', 'de', 'en-US', 'ja'] as const;
const DEFAULT_LOCALE = 'en';

/** Locale-specific path overrides (mirrors routing.ts pathnames). */
const LOCALIZED_PATHS: Record<string, Record<string, string>> = {
  '/datenschutz': { en: '/privacy', 'en-US': '/privacy', de: '/datenschutz', ja: '/privacy' },
  '/nutzungsbedingungen': { en: '/terms', 'en-US': '/terms', de: '/nutzungsbedingungen', ja: '/terms' },
  '/impressum': { en: '/legal', 'en-US': '/legal', de: '/impressum', ja: '/legal' },
};

const PAGES: Array<{
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

function localeUrl(locale: string, internalPath: string): string {
  const prefix = locale === DEFAULT_LOCALE ? '' : `/${locale}`;
  const localizedPath = LOCALIZED_PATHS[internalPath]?.[locale] ?? internalPath;
  const suffix = localizedPath === '/' ? '' : localizedPath;
  return `${BASE_URL}${prefix}${suffix}`;
}

export default function sitemap(): MetadataRoute.Sitemap {
  return PAGES.flatMap((page) =>
    LOCALES.map((locale) => ({
      url: localeUrl(locale, page.path),
      lastModified: new Date(),
      changeFrequency: page.changeFrequency,
      priority: page.priority,
      alternates: {
        languages: Object.fromEntries(
          LOCALES.map((l) => [l, localeUrl(l, page.path)])
        ),
      },
    }))
  );
}
