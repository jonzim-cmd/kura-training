import createMiddleware from 'next-intl/middleware';
import { NextRequest, NextResponse } from 'next/server';
import { routing } from './i18n/routing';

const intlMiddleware = createMiddleware(routing);
const LEGACY_HOST = 'withkura.com';
const CANONICAL_HOST = 'www.withkura.com';

export default function middleware(request: NextRequest) {
  const forwardedHost = request.headers.get('x-forwarded-host');
  const host = (forwardedHost ?? request.headers.get('host') ?? '')
    .split(':')[0]
    .toLowerCase();

  if (host === LEGACY_HOST) {
    const redirectUrl = request.nextUrl.clone();
    redirectUrl.protocol = 'https:';
    redirectUrl.hostname = CANONICAL_HOST;
    redirectUrl.port = '';
    return NextResponse.redirect(redirectUrl, 308);
  }

  return intlMiddleware(request);
}

export const config = {
  // Match all paths except Next.js internals, static files, and API routes
  matcher: ['/((?!api|_next|_vercel|.*\\..*).*)', '/', '/(de|en|en-US|ja)/:path*'],
};
