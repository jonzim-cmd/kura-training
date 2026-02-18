import { ImageResponse } from 'next/og';
import { readFile } from 'node:fs/promises';
import { join } from 'node:path';

export const alt = 'Kura — AI Training Diary & Workout Logger';
export const size = { width: 1200, height: 630 };
export const contentType = 'image/png';

const SUBTITLES: Record<string, string> = {
  en: 'AI Training Diary & Workout Logger',
  'en-US': 'AI Training Diary & Workout Logger',
  de: 'KI-Trainingstagebuch & Workout-Logger',
  ja: 'AI Training Diary & Workout Logger',
};

export default async function Image({
  params,
}: {
  params: Promise<{ locale: string }>;
}) {
  const { locale } = await params;
  const subtitle = SUBTITLES[locale] || SUBTITLES.en;

  const outfitBlack = await readFile(
    join(process.cwd(), 'src/app/[locale]/fonts/Outfit-Black.woff')
  );

  return new ImageResponse(
    (
      <div
        style={{
          width: '100%',
          height: '100%',
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          justifyContent: 'center',
          background: '#000',
          fontFamily: 'Outfit',
        }}
      >
        {/* Subtle glow behind wordmark */}
        <div
          style={{
            position: 'absolute',
            width: 600,
            height: 200,
            borderRadius: '50%',
            background:
              'radial-gradient(circle, rgba(255,229,102,0.12) 0%, transparent 70%)',
            top: '35%',
          }}
        />

        {/* KURA wordmark */}
        <div
          style={{
            fontSize: 164,
            fontWeight: 900,
            color: '#FFE566',
            letterSpacing: 6,
            lineHeight: 1,
          }}
        >
          KURA
        </div>

        {/* Subtitle */}
        <div
          style={{
            fontSize: 24,
            fontWeight: 900,
            color: 'rgba(255, 255, 255, 0.35)',
            letterSpacing: 5,
            marginTop: 28,
            textTransform: 'uppercase' as const,
          }}
        >
          {subtitle}
        </div>

        {/* withkura.com */}
        <div
          style={{
            position: 'absolute',
            bottom: 40,
            fontSize: 18,
            fontWeight: 900,
            color: 'rgba(255, 255, 255, 0.15)',
            letterSpacing: 3,
          }}
        >
          withkura.com
        </div>
      </div>
    ),
    {
      ...size,
      fonts: [
        {
          name: 'Outfit',
          data: outfitBlack,
          style: 'normal',
          weight: 900,
        },
      ],
    }
  );
}
