import createNextIntlPlugin from 'next-intl/plugin';

const withNextIntl = createNextIntlPlugin('./src/i18n/request.ts');

export default withNextIntl({
  // Future: API proxy to Kura backend
  // async rewrites() {
  //   return [
  //     { source: '/api/:path*', destination: 'http://localhost:3000/v1/:path*' },
  //   ];
  // },
});
