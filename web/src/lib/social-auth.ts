export type SocialProvider = 'google' | 'github' | 'apple';

const SOCIAL_AUTH_BASE_URL = process.env.NEXT_PUBLIC_SOCIAL_AUTH_BASE_URL?.trim() ?? '';

export const SOCIAL_AUTH_ENABLED = SOCIAL_AUTH_BASE_URL.length > 0;

export function socialAuthorizeUrl(provider: SocialProvider, redirectTo: string): string {
  const authUrl = new URL('/auth/v1/authorize', SOCIAL_AUTH_BASE_URL);
  authUrl.searchParams.set('provider', provider);
  authUrl.searchParams.set('redirect_to', redirectTo);
  return authUrl.toString();
}
