import createMiddleware from 'next-intl/middleware';
import { NextRequest, NextResponse } from 'next/server';
import { routing } from './i18n/routing';

const intlMiddleware = createMiddleware(routing);
export default function middleware(request: NextRequest) {
  return intlMiddleware(request);
}

export const config = {
  // Match all paths except Next.js internals, static files, and API routes
  matcher: ['/((?!api|_next|_vercel|.*\\..*).*)', '/', '/(de|en|en-US|ja)/:path*'],
};
