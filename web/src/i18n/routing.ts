import { defineRouting } from 'next-intl/routing';
import { createNavigation } from 'next-intl/navigation';

export const routing = defineRouting({
  locales: ['en', 'en-US', 'de', 'ja'],
  defaultLocale: 'en',
  localePrefix: 'as-needed',
  pathnames: {
    '/': '/',
    '/start': '/start',
    '/request-access': '/request-access',
    '/how-i-use-it': '/how-i-use-it',
    '/setup': '/setup',
    '/login': '/login',
    '/signup': '/signup',
    '/forgot-password': '/forgot-password',
    '/reset-password': '/reset-password',
    '/settings': '/settings',
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
  },
});

export type Pathnames = keyof typeof routing.pathnames;

export const { Link, redirect, usePathname, useRouter } =
  createNavigation(routing);
