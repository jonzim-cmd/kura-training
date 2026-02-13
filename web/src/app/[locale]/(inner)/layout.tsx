import { Header } from '@/components/Header';
import { Footer } from '@/components/Footer';
import { AuthProvider } from '@/lib/auth-context';

export default function InnerLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <AuthProvider>
      <Header />
      <main style={{ paddingTop: '3.5rem' }}>{children}</main>
      <Footer />
    </AuthProvider>
  );
}
