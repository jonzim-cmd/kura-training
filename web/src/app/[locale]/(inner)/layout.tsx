import { Header } from '@/components/Header';
import { Footer } from '@/components/Footer';

export default function InnerLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <>
      <Header />
      <main style={{ paddingTop: '3.5rem' }}>{children}</main>
      <Footer />
    </>
  );
}
