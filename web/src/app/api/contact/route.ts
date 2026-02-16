import { NextRequest, NextResponse } from 'next/server';

const RESEND_API_KEY = process.env.RESEND_API_KEY || '';
const ADMIN_NOTIFY_EMAIL = process.env.ADMIN_NOTIFY_EMAIL || '';
const EMAIL_FROM = process.env.EMAIL_FROM || 'Kura <noreply@withkura.com>';

export async function POST(req: NextRequest) {
  if (!RESEND_API_KEY || !ADMIN_NOTIFY_EMAIL) {
    return NextResponse.json(
      { error: 'Contact form is not configured.' },
      { status: 503 },
    );
  }

  let body: { category?: string; message?: string; email?: string };
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ error: 'Invalid request.' }, { status: 400 });
  }

  const { category, message, email } = body;

  if (!message || !message.trim()) {
    return NextResponse.json({ error: 'Message is required.' }, { status: 400 });
  }

  const subject = `[Kura Support] ${category || 'Sonstiges'}`;
  const text = `Kategorie: ${category || 'Sonstiges'}\nAbsender: ${email || 'unbekannt'}\n\n${message}`;

  const res = await fetch('https://api.resend.com/emails', {
    method: 'POST',
    headers: {
      'Authorization': `Bearer ${RESEND_API_KEY}`,
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({
      from: EMAIL_FROM,
      to: [ADMIN_NOTIFY_EMAIL],
      reply_to: email || undefined,
      subject,
      text,
    }),
  });

  if (!res.ok) {
    console.error('Resend error:', res.status, await res.text().catch(() => ''));
    return NextResponse.json(
      { error: 'Could not send message.' },
      { status: 502 },
    );
  }

  return NextResponse.json({ ok: true });
}
