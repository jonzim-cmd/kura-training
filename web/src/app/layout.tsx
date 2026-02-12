import type { Metadata } from 'next';
import './globals.css';

export const metadata: Metadata = {
  title: 'Kura',
  description: 'Your body, understood.',
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return children;
}
