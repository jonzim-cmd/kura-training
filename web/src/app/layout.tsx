import type { Metadata } from 'next';
import './globals.css';

export const metadata: Metadata = {
  metadataBase: new URL('https://www.withkura.com'),
  title: {
    default: 'Kura',
    template: '%s',
  },
  description: 'Your body, understood.',
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return children;
}
