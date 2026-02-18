import type { Metadata } from 'next';
import { routing } from '@/i18n/routing';

export const BASE_URL = 'https://withkura.com';

const LOCALIZED_PATHS: Record<string, Record<string, string>> = {
  '/datenschutz': {
    en: '/privacy',
    'en-US': '/privacy',
    de: '/datenschutz',
    ja: '/privacy',
  },
  '/nutzungsbedingungen': {
    en: '/terms',
    'en-US': '/terms',
    de: '/nutzungsbedingungen',
    ja: '/terms',
  },
  '/impressum': {
    en: '/legal',
    'en-US': '/legal',
    de: '/impressum',
    ja: '/legal',
  },
};

function ensureLeadingSlash(path: string): string {
  if (!path) return '/';
  if (path.startsWith('/')) return path;
  return `/${path}`;
}

function localePrefix(locale: string): string {
  return locale === routing.defaultLocale ? '' : `/${locale}`;
}

export function localizedPath(locale: string, internalPath: string): string {
  const normalizedPath = ensureLeadingSlash(internalPath);
  return LOCALIZED_PATHS[normalizedPath]?.[locale] ?? normalizedPath;
}

export function localeUrl(locale: string, internalPath: string): string {
  const path = localizedPath(locale, internalPath);
  const suffix = path === '/' ? '' : path;
  return `${BASE_URL}${localePrefix(locale)}${suffix}`;
}

export function languageAlternates(internalPath: string): Record<string, string> {
  const languages = Object.fromEntries(
    routing.locales.map((locale) => [locale, localeUrl(locale, internalPath)])
  ) as Record<string, string>;

  languages['x-default'] = localeUrl(routing.defaultLocale, internalPath);
  return languages;
}

type BuildPageMetadataArgs = {
  locale: string;
  internalPath: string;
  title: string;
  description: string;
  noindex?: boolean;
};

export function buildPageMetadata({
  locale,
  internalPath,
  title,
  description,
  noindex = false,
}: BuildPageMetadataArgs): Metadata {
  const url = localeUrl(locale, internalPath);

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
      languages: languageAlternates(internalPath),
    },
    robots: {
      index: !noindex,
      follow: !noindex,
    },
  };
}

export const NO_INDEX_METADATA: Metadata = {
  robots: {
    index: false,
    follow: false,
  },
};
