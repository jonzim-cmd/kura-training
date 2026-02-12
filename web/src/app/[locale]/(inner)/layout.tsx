import { Header } from '@/components/Header';

export default function InnerLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <>
      <Header />
      <main style={{ paddingTop: '3.5rem' }}>{children}</main>
    </>
  );
}
