type LegalLocale = 'de' | 'en';

type LegalSection = {
  title: string;
  paragraphs?: string[];
  bullets?: string[];
};

type LegalDocument = {
  title: string;
  subtitle: string;
  updatedLabel: string;
  updatedAt: string;
  sections: LegalSection[];
};

type LegalBundle = {
  privacy: LegalDocument;
  terms: LegalDocument;
};

const LEGAL_CONTENT: Record<LegalLocale, LegalBundle> = {
  de: {
    privacy: {
      title: 'Datenschutzhinweise',
      subtitle:
        'Diese Hinweise beschreiben, wie personenbezogene Daten in der Kura-Web-Anwendung verarbeitet werden (EU/DSGVO-Standard).',
      updatedLabel: 'Stand',
      updatedAt: '16. Februar 2026',
      sections: [
        {
          title: '1. Verantwortlicher und Kontakt',
          paragraphs: [
            'Verantwortlicher fuer die Datenverarbeitung ist die im Impressum genannte Person bzw. Stelle.',
            'Bei Fragen zum Datenschutz kannst du uns ueber die im Impressum angegebene E-Mail-Adresse oder ueber das Support-Formular in den Einstellungen kontaktieren.',
          ],
        },
        {
          title: '2. Welche Daten wir verarbeiten',
          bullets: [
            'Konto- und Profildaten: E-Mail-Adresse, optional Anzeigename, Kontostatus, Zeitstempel.',
            'Authentifizierungsdaten: gehashte Passwoerter, Access-/Refresh-Token, OAuth- und Session-Metadaten.',
            'Social-Login-Daten: Provider, Provider-ID, verifizierte E-Mail (z. B. Google, GitHub, Apple via Supabase).',
            'Nutzungs- und Inhaltsdaten: Trainings-/Gesundheits-/Ernaehrungseintraege, daraus berechnete Projektionen und zugehoerige Metadaten.',
            'Access-Request-Daten: E-Mail, optional Name und Kontext.',
            'Supportdaten: Kategorie, Nachricht, Absenderadresse.',
            'Technische Daten: Spracheinstellung, sicherheitsrelevante Log- und Zugriffsdaten.',
          ],
        },
        {
          title: '3. Zwecke und Rechtsgrundlagen (Art. 6 DSGVO)',
          bullets: [
            'Vertragserfuellung (Art. 6 Abs. 1 lit. b): Kontoanlage, Login, Bereitstellung der Web-App und API-Funktionen.',
            'Sicherheit und Missbrauchsabwehr (Art. 6 Abs. 1 lit. f): Rate-Limiting, Session-Schutz, Token-Widerruf, technische Fehleranalyse.',
            'Kommunikation und Support (Art. 6 Abs. 1 lit. b und f): Bearbeitung von Anfragen und Service-Nachrichten.',
            'Anonymisierte Lern- und Verbesserungsprozesse (Art. 6 Abs. 1 lit. a und/oder b): sofern und soweit im jeweiligen Produktmodus vorgesehen.',
            'Erfuellung rechtlicher Pflichten (Art. 6 Abs. 1 lit. c), sofern einschlaegig.',
          ],
        },
        {
          title: '4. Registrierung, Login und Kontofunktionen',
          paragraphs: [
            'Bei der Registrierung verarbeiten wir E-Mail, Passwort (nur als Hash), optional Anzeigename, Invite-Informationen und ggf. die Zustimmung zur anonymisierten Datennutzung im Early-Access-Kontext.',
            'Bei Passwort-Reset werden zeitlich begrenzte Reset-Token verwendet (derzeit 60 Minuten gueltig). Beim Zuruecksetzen werden bestehende Sessions/Tokens widerrufen.',
            'Bei Kontoloeschung wird dein Konto zunaechst deaktiviert; die endgueltige Loeschung ist derzeit nach einer Schonfrist von 30 Tagen vorgesehen.',
          ],
        },
        {
          title: '5. Social Login (Google, GitHub, Apple)',
          paragraphs: [
            'Wenn du Social Login nutzt, pruefen wir den bereitgestellten Session-Token ueber Supabase und uebernehmen nur die fuer Anmeldung/Verknuepfung erforderlichen Identitaetsdaten.',
            'Es werden keine Passwoerter der Social-Provider bei uns gespeichert. Wir speichern nur die fuer die Kontoverknuepfung notwendigen Kennungen.',
          ],
        },
        {
          title: '6. E-Mail-Kommunikation',
          paragraphs: [
            'Fuer transaktionale E-Mails (z. B. Zugangseinladung, Passwort-Reset, Kontaktformular) nutzen wir derzeit Resend als Versanddienstleister.',
            'Dabei werden insbesondere Empfaengeradresse, Nachrichteninhalt und technische Versanddaten verarbeitet.',
          ],
        },
        {
          title: '7. Cookies, Local Storage und aehnliche Technologien',
          bullets: [
            'NEXT_LOCALE (Cookie): Speicherung der gewaehlten Sprache (technisch notwendig fuer konsistente Lokalisierung).',
            'kura_rt (Local Storage): Refresh-Token zur Aufrechterhaltung der Sitzung.',
            'kura_setup_seen (Local Storage): Merker fuer den Onboarding-Status.',
            'kura_oauth_session (HttpOnly Cookie im OAuth-Kontext): Session-Unterstuetzung fuer OAuth-Autorisierung.',
            'Aktuell verwenden wir keine Marketing- oder Werbetracker im Web-Frontend.',
          ],
        },
        {
          title: '8. Zugriffsdaten und Sicherheitstelemetrie',
          paragraphs: [
            'Zur Stabilitaet und Sicherheit protokollieren wir API-Zugriffe (u. a. Methode, Pfad, Statuscode, Antwortzeit, ggf. Benutzer-ID).',
            'IP-basierte Informationen koennen fuer Rate-Limits und Missbrauchsabwehr technisch verarbeitet werden.',
          ],
        },
        {
          title: '9. Empfaenger und Auftragsverarbeiter',
          bullets: [
            'Hosting/Plattform und Datenbankdienste (derzeit u. a. Supabase-Komponenten).',
            'E-Mail-Zustelldienst (derzeit Resend).',
            'Interne Administratoren und Support nur im erforderlichen Umfang (Need-to-know-Prinzip, mit Auditierung).',
          ],
        },
        {
          title: '10. Drittlanduebermittlungen',
          paragraphs: [
            'Soweit Dienstleister Daten ausserhalb des EWR verarbeiten, erfolgt dies nur unter den gesetzlichen Voraussetzungen (z. B. Angemessenheitsbeschluss oder EU-Standardvertragsklauseln).',
          ],
        },
        {
          title: '11. Speicherdauer',
          bullets: [
            'Kontodaten: bis zur Kontoloeschung bzw. bis zum Ablauf gesetzlicher Aufbewahrungspflichten.',
            'Kontoloeschung: derzeit 30 Tage Schonfrist nach Deaktivierung; anschliessend Hard-Delete der zugeordneten Daten gemaess Systemlogik.',
            'Invite-Token: derzeit 7 Tage gueltig.',
            'Passwort-Reset-Token: derzeit 60 Minuten gueltig.',
            'API-Schluessel: bis Widerruf oder Kontoloeschung.',
            'Log- und Sicherheitsdaten: nur solange erforderlich fuer Sicherheit, Betrieb und Fehleranalyse.',
          ],
        },
        {
          title: '12. Deine Rechte',
          bullets: [
            'Auskunft (Art. 15 DSGVO)',
            'Berichtigung (Art. 16 DSGVO)',
            'Loeschung (Art. 17 DSGVO)',
            'Einschraenkung der Verarbeitung (Art. 18 DSGVO)',
            'Datenuebertragbarkeit (Art. 20 DSGVO)',
            'Widerspruch (Art. 21 DSGVO) bei Verarbeitungen nach Art. 6 Abs. 1 lit. f',
            'Widerruf erteilter Einwilligungen mit Wirkung fuer die Zukunft (Art. 7 Abs. 3 DSGVO)',
          ],
        },
        {
          title: '13. Beschwerderecht',
          paragraphs: [
            'Du hast das Recht, dich bei einer Datenschutz-Aufsichtsbehoerde zu beschweren, insbesondere in dem Mitgliedstaat deines gewoehnlichen Aufenthalts, deines Arbeitsplatzes oder des Orts des mutmasslichen Verstosses (Art. 77 DSGVO).',
          ],
        },
        {
          title: '14. Datensicherheit',
          paragraphs: [
            'Wir setzen angemessene technische und organisatorische Massnahmen ein, insbesondere rollenbasierten Zugriff, Token-Widerruf, Hashing sensibler Geheimnisse, Transportverschluesselung sowie sicherheitsbezogene Audits und Logging.',
          ],
        },
        {
          title: '15. Aktualisierung dieser Hinweise',
          paragraphs: [
            'Wir koennen diese Datenschutzhinweise anpassen, wenn sich Funktionen, Rechtslage oder Datenverarbeitungen aendern. Die jeweils aktuelle Version ist in der Web-App abrufbar.',
          ],
        },
      ],
    },
    terms: {
      title: 'Nutzungsbedingungen',
      subtitle:
        'Diese Bedingungen regeln die Nutzung der Kura-Web-Anwendung, der zugehoerigen API-Zugaenge und Early-Access-Funktionen.',
      updatedLabel: 'Stand',
      updatedAt: '16. Februar 2026',
      sections: [
        {
          title: '1. Anbieter und Geltungsbereich',
          paragraphs: [
            'Vertragspartner fuer die Nutzung von Kura ist die im Impressum genannte Person bzw. Stelle.',
            'Diese Bedingungen gelten fuer die Nutzung der Web-App, der Kontofunktionen sowie der bereitgestellten API-Zugaenge.',
          ],
        },
        {
          title: '2. Leistungsbeschreibung und Early Access',
          paragraphs: [
            'Kura ist eine softwaregestuetzte Plattform zur strukturierten Erfassung und Auswertung von Trainingsdaten fuer die Zusammenarbeit mit KI-Agenten.',
            'Der Dienst kann ganz oder teilweise als Early-Access/Beta angeboten werden. In diesem Modus koennen Funktionen unvollstaendig sein, sich kurzfristig aendern oder entfallen.',
          ],
        },
        {
          title: '3. Registrierung und Konto',
          bullets: [
            'Fuer wesentliche Funktionen ist ein Nutzerkonto erforderlich.',
            'Du musst bei der Registrierung wahrheitsgemaesse Angaben machen und Zugangsdaten vertraulich behandeln.',
            'Einladungs- oder Zugangsgates koennen je nach Betriebsmodus Voraussetzung fuer die Registrierung sein.',
          ],
        },
        {
          title: '4. Login, API-Schluessel und Sicherheit',
          bullets: [
            'Du bist fuer alle Aktivitaeten verantwortlich, die ueber dein Konto oder deine API-Schluessel erfolgen.',
            'API-Schluessel muessen sicher gespeichert werden und duerfen nicht unbefugt weitergegeben werden.',
            'Bei Verdacht auf Missbrauch musst du Zugangsdaten unverzueglich aendern bzw. Schluessel widerrufen und uns informieren.',
          ],
        },
        {
          title: '5. Zulaessige Nutzung',
          bullets: [
            'Die Nutzung muss mit geltendem Recht vereinbar sein.',
            'Untersagt sind insbesondere missbraeuchliche, sicherheitsgefaehrdende, automatisiert-angreifende oder rechtsverletzende Nutzungen.',
            'Untersagt sind auch Versuche, Schutzmechanismen zu umgehen oder unberechtigten Zugriff auf Systeme und Daten zu erlangen.',
          ],
        },
        {
          title: '6. Inhalte und Verantwortlichkeit',
          paragraphs: [
            'Du bleibst fuer die von dir bereitgestellten Inhalte verantwortlich. Stelle sicher, dass du berechtigt bist, diese zu verarbeiten und an Kura zu uebermitteln.',
            'Bitte gib nur solche Daten ein, deren Verarbeitung fuer deinen Nutzungszweck erforderlich und rechtlich zulaessig ist.',
          ],
        },
        {
          title: '7. Datennutzung zur Produktverbesserung',
          paragraphs: [
            'Soweit vorgesehen, koennen anonymisierte oder aggregierte Nutzungs- und Trainingsdaten fuer Qualitaetssicherung, Statistik und Weiterentwicklung verwendet werden.',
            'Details zur Datenverarbeitung findest du in den Datenschutzhinweisen.',
          ],
        },
        {
          title: '8. Verfuegbarkeit und Aenderungen',
          paragraphs: [
            'Wir bemuehen uns um einen moeglichst unterbrechungsfreien Betrieb, garantieren aber keine jederzeitige Verfuegbarkeit.',
            'Wir duerfen Funktionen, Schnittstellen, Sicherheitsmassnahmen und Leistungsumfang anpassen, soweit dies aus technischen, rechtlichen oder produktbezogenen Gruenden erforderlich ist.',
          ],
        },
        {
          title: '9. Kein medizinischer Rat',
          paragraphs: [
            'Kura liefert technische Auswertungen und Hilfestellungen, ersetzt jedoch keine medizinische, therapeutische oder sonstige fachliche Beratung.',
            'Entscheidungen zu Training, Gesundheit und Behandlung triffst du eigenverantwortlich.',
          ],
        },
        {
          title: '10. Entgelte',
          paragraphs: [
            'Sofern nicht anders ausgewiesen, kann der Dienst im jeweiligen Zeitraum kostenfrei bereitgestellt werden (z. B. Early Access).',
            'Fuer spaetere kostenpflichtige Angebote gelten die jeweils vor Vertragsschluss angegebenen Preise und Bedingungen.',
          ],
        },
        {
          title: '11. Laufzeit, Kuendigung und Kontoloeschung',
          paragraphs: [
            'Du kannst die Nutzung jederzeit beenden und eine Kontoloeschung veranlassen.',
            'Nach Loeschanforderung kann eine technische Schonfrist gelten (derzeit 30 Tage), bevor die endgueltige Loeschung erfolgt.',
            'Wir duerfen Konten sperren oder kuendigen, wenn erhebliche Verstoesse gegen diese Bedingungen vorliegen oder Sicherheitsrisiken bestehen.',
          ],
        },
        {
          title: '12. Geistiges Eigentum',
          paragraphs: [
            'Alle Rechte an der Plattform, Software, Marken, Dokumentation und nicht von dir stammenden Inhalten verbleiben bei uns bzw. den jeweiligen Rechteinhabern.',
            'Du erhaeltst ein nicht ausschliessliches, nicht uebertragbares Recht zur Nutzung im Rahmen dieser Bedingungen.',
          ],
        },
        {
          title: '13. Haftung',
          paragraphs: [
            'Wir haften unbeschraenkt bei Vorsatz und grober Fahrlaessigkeit sowie bei Verletzung von Leben, Koerper oder Gesundheit.',
            'Bei leichter Fahrlaessigkeit haften wir nur bei Verletzung wesentlicher Vertragspflichten und begrenzt auf den vertragstypischen, vorhersehbaren Schaden.',
            'Zwingende gesetzliche Haftungsregeln (insbesondere nach EU-Verbraucherrecht) bleiben unberuehrt.',
          ],
        },
        {
          title: '14. Rechtswahl und Gerichtsstand',
          paragraphs: [
            'Es gilt deutsches Recht unter Ausschluss des UN-Kaufrechts.',
            'Fuer Verbraucherinnen und Verbraucher in der EU bleiben zwingende Verbraucherschutzvorschriften des Wohnsitzstaates unberuehrt.',
            'Ist der Nutzer Kaufmann, juristische Person des oeffentlichen Rechts oder oeffentlich-rechtliches Sondervermoegen, ist Gerichtsstand Muenchen, soweit gesetzlich zulaessig.',
          ],
        },
        {
          title: '15. Aenderungen dieser Bedingungen',
          paragraphs: [
            'Wir koennen diese Bedingungen mit Wirkung fuer die Zukunft aendern, wenn dies aus sachlichen Gruenden erforderlich ist (z. B. Funktions- oder Rechtsaenderungen).',
            'Die jeweils aktuelle Fassung ist in der Web-App abrufbar.',
          ],
        },
      ],
    },
  },
  en: {
    privacy: {
      title: 'Privacy Notice',
      subtitle:
        'This notice explains how personal data is processed in the Kura web application (EU/GDPR baseline).',
      updatedLabel: 'Last updated',
      updatedAt: 'February 16, 2026',
      sections: [
        {
          title: '1. Controller and contact',
          paragraphs: [
            'The controller is the person or entity listed in the Legal Notice (Impressum).',
            'For privacy requests, contact us via the email address in the Legal Notice or through the support form in settings.',
          ],
        },
        {
          title: '2. Categories of data we process',
          bullets: [
            'Account and profile data: email address, optional display name, account status, timestamps.',
            'Authentication data: password hashes, access/refresh tokens, OAuth and session metadata.',
            'Social login data: provider, provider user ID, verified email (for example Google, GitHub, Apple via Supabase).',
            'Usage and content data: training/health/nutrition entries, derived projections, and related metadata.',
            'Access request data: email, optional name and context.',
            'Support data: category, message, sender email.',
            'Technical data: locale preference, security-relevant log and access data.',
          ],
        },
        {
          title: '3. Purposes and legal bases (Art. 6 GDPR)',
          bullets: [
            'Contract performance (Art. 6(1)(b)): account creation, login, web app and API delivery.',
            'Security and abuse prevention (Art. 6(1)(f)): rate limiting, session protection, token revocation, technical troubleshooting.',
            'Communication and support (Art. 6(1)(b) and (f)): handling requests and service communication.',
            'Anonymized learning and product improvement (Art. 6(1)(a) and/or (b)), where applicable for the active product mode.',
            'Compliance with legal obligations (Art. 6(1)(c)) where required.',
          ],
        },
        {
          title: '4. Registration, login, and account management',
          paragraphs: [
            'During registration we process email, password (stored only as a hash), optional display name, invite information, and where applicable consent state for anonymized learning in early access.',
            'Password reset uses time-limited reset tokens (currently 60 minutes). Existing sessions/tokens are revoked after password reset.',
            'Account deletion currently follows a 30-day grace period before permanent deletion.',
          ],
        },
        {
          title: '5. Social login (Google, GitHub, Apple)',
          paragraphs: [
            'When social login is used, we validate the provided session token through Supabase and only ingest identity information required for authentication and account linking.',
            'We do not store social provider passwords. We only store identifiers required for account linkage.',
          ],
        },
        {
          title: '6. Email communication',
          paragraphs: [
            'For transactional emails (for example invites, password reset, contact form notifications) we currently use Resend as mail delivery provider.',
            'This includes processing recipient address, message content, and technical delivery metadata.',
          ],
        },
        {
          title: '7. Cookies, local storage, and similar technologies',
          bullets: [
            'NEXT_LOCALE (cookie): stores selected language for consistent localization.',
            'kura_rt (local storage): refresh token to keep a user session active.',
            'kura_setup_seen (local storage): onboarding completion marker.',
            'kura_oauth_session (HttpOnly cookie in OAuth flows): supports OAuth session continuity.',
            'We currently do not use marketing or advertising trackers in the web frontend.',
          ],
        },
        {
          title: '8. Access logs and security telemetry',
          paragraphs: [
            'To operate and secure the service, we log API access details such as method, path, status code, response time, and where applicable user ID.',
            'IP-related information may be processed for rate limiting and abuse prevention.',
          ],
        },
        {
          title: '9. Recipients and processors',
          bullets: [
            'Hosting/platform and database service providers (currently including Supabase components).',
            'Email delivery provider (currently Resend).',
            'Internal admin/support staff only where required under least-privilege and audited access.',
          ],
        },
        {
          title: '10. International data transfers',
          paragraphs: [
            'If a provider processes data outside the EEA, transfers occur only under applicable legal safeguards (for example adequacy decisions or EU Standard Contractual Clauses).',
          ],
        },
        {
          title: '11. Retention',
          bullets: [
            'Account data: retained until account deletion, unless legal retention duties apply.',
            'Account deletion: currently 30-day grace period after deactivation, then hard-delete according to system logic.',
            'Invite tokens: currently valid for 7 days.',
            'Password reset tokens: currently valid for 60 minutes.',
            'API keys: retained until revoked or account deletion.',
            'Log and security data: retained only as long as necessary for security, operations, and troubleshooting.',
          ],
        },
        {
          title: '12. Your rights',
          bullets: [
            'Access (Art. 15 GDPR)',
            'Rectification (Art. 16 GDPR)',
            'Erasure (Art. 17 GDPR)',
            'Restriction (Art. 18 GDPR)',
            'Data portability (Art. 20 GDPR)',
            'Objection (Art. 21 GDPR) for processing based on Art. 6(1)(f)',
            'Withdrawal of consent for future processing (Art. 7(3) GDPR)',
          ],
        },
        {
          title: '13. Right to lodge a complaint',
          paragraphs: [
            'You have the right to lodge a complaint with a supervisory authority, in particular in the EU member state of your habitual residence, workplace, or the place of the alleged infringement (Art. 77 GDPR).',
          ],
        },
        {
          title: '14. Data security',
          paragraphs: [
            'We apply appropriate technical and organizational measures, including role-based access controls, token revocation, hashing of sensitive secrets, encrypted transport, and security-oriented auditing/logging.',
          ],
        },
        {
          title: '15. Updates to this notice',
          paragraphs: [
            'We may update this privacy notice if features, legal requirements, or processing activities change. The current version is available in the web app.',
          ],
        },
      ],
    },
    terms: {
      title: 'Terms of Use',
      subtitle:
        'These terms govern use of the Kura web application, related API access, and early access features.',
      updatedLabel: 'Last updated',
      updatedAt: 'February 16, 2026',
      sections: [
        {
          title: '1. Provider and scope',
          paragraphs: [
            'Your contractual partner is the person or entity listed in the Legal Notice (Impressum).',
            'These terms apply to use of the web app, account functionality, and provided API access.',
          ],
        },
        {
          title: '2. Service description and early access',
          paragraphs: [
            'Kura is a software platform for structured training data capture and analysis for collaboration with AI agents.',
            'The service may be offered fully or partially as early access/beta. In this mode, features may be incomplete, changed, or removed at short notice.',
          ],
        },
        {
          title: '3. Registration and account',
          bullets: [
            'An account is required for core functionality.',
            'You must provide accurate information and keep credentials confidential.',
            'Invite or access gates may apply depending on the current product mode.',
          ],
        },
        {
          title: '4. Login, API keys, and security',
          bullets: [
            'You are responsible for all activity performed via your account or API keys.',
            'API keys must be stored securely and must not be shared with unauthorized parties.',
            'If you suspect compromise, immediately rotate credentials/revoke keys and notify us.',
          ],
        },
        {
          title: '5. Acceptable use',
          bullets: [
            'Use must comply with applicable law.',
            'Abusive, security-threatening, attack-like, or unlawful use is prohibited.',
            'Attempts to bypass safeguards or gain unauthorized access are prohibited.',
          ],
        },
        {
          title: '6. Content and responsibility',
          paragraphs: [
            'You remain responsible for content you submit. Ensure you are authorized to process and transmit that data.',
            'Only submit data that is necessary for your purpose and lawful to process.',
          ],
        },
        {
          title: '7. Data use for product improvement',
          paragraphs: [
            'Where applicable, anonymized or aggregated usage/training data may be used for quality assurance, statistics, and service improvement.',
            'See the Privacy Notice for processing details.',
          ],
        },
        {
          title: '8. Availability and changes',
          paragraphs: [
            'We aim for stable operation but do not guarantee uninterrupted availability.',
            'We may adapt features, interfaces, security controls, and service scope where technically, legally, or product-wise required.',
          ],
        },
        {
          title: '9. No medical advice',
          paragraphs: [
            'Kura provides technical analysis and assistance, not medical, therapeutic, or other professional advice.',
            'Training and health decisions remain your own responsibility.',
          ],
        },
        {
          title: '10. Fees',
          paragraphs: [
            'Unless stated otherwise, the service may be provided free of charge for a given period (for example early access).',
            'For future paid offerings, pricing and applicable terms are shown before purchase/commitment.',
          ],
        },
        {
          title: '11. Term, termination, and account deletion',
          paragraphs: [
            'You may stop using the service at any time and request account deletion.',
            'After a deletion request, a technical grace period may apply (currently 30 days) before permanent deletion.',
            'We may suspend or terminate accounts for serious violations of these terms or where security risk requires this.',
          ],
        },
        {
          title: '12. Intellectual property',
          paragraphs: [
            'All rights in the platform, software, trademarks, documentation, and non-user content remain with us or respective rights holders.',
            'You receive a non-exclusive, non-transferable right to use the service under these terms.',
          ],
        },
        {
          title: '13. Liability',
          paragraphs: [
            'We have unlimited liability for intent, gross negligence, and injury to life, body, or health.',
            'For slight negligence, liability is limited to breaches of essential contractual duties and to foreseeable typical damages.',
            'Mandatory statutory liability rules, including EU consumer protections, remain unaffected.',
          ],
        },
        {
          title: '14. Governing law and venue',
          paragraphs: [
            'German law applies, excluding the UN Convention on Contracts for the International Sale of Goods (CISG).',
            'If you are a consumer in the EU, mandatory consumer protection laws of your country of residence remain applicable.',
            'If you are a merchant/public body under applicable law, venue is Munich where legally permissible.',
          ],
        },
        {
          title: '15. Changes to these terms',
          paragraphs: [
            'We may update these terms for future effect where objectively required (for example legal or feature changes).',
            'The current version is available in the web app.',
          ],
        },
      ],
    },
  },
};

function resolveLegalLocale(locale: string): LegalLocale {
  return locale.toLowerCase().startsWith('de') ? 'de' : 'en';
}

export function getPrivacyContent(locale: string): LegalDocument {
  return LEGAL_CONTENT[resolveLegalLocale(locale)].privacy;
}

export function getTermsContent(locale: string): LegalDocument {
  return LEGAL_CONTENT[resolveLegalLocale(locale)].terms;
}

