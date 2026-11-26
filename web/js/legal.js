// Legal & compliance pages, rendered inside the SPA router (/legal, /legal/<page>).
// ---------------------------------------------------------------------------
// FILL THESE IN before applying for payment-gateway KYC. Every page reads from here,
// so one edit updates the Terms, Privacy, DMCA, Refund and Contact pages together.
// This text is a well-structured starting point, not legal advice: have it reviewed
// by a lawyer before you rely on it commercially.
// ---------------------------------------------------------------------------
import { html, raw, render, $ } from "./ui.js";

export const LEGAL = {
  app: "PENDEL",
  operator: "Nitin Kumar M",
  email: "support@pendel.live",
  grievanceEmail: "grievance@pendel.live",
  dmcaEmail: "dmca@pendel.live",
  location: "Bengaluru / Karnataka, India",
  address: "Sindhanur, Karnataka, India",
  updated: "26 November 2026",
};
const P = (k) => (String(LEGAL[k]).startsWith("[") ? html`<span class="ph">${LEGAL[k]}</span>` : html`${LEGAL[k]}`);  // unfilled placeholders are highlighted
// Which address to use: "support" (general), "grievance" (IT Act / DPDP complaints), "dmca" (copyright notices)
const ADDRESS = { support: "email", grievance: "grievanceEmail", dmca: "dmcaEmail" };
const mail = (subject, to = "support") => {
  const addr = LEGAL[ADDRESS[to] || "email"];
  return html`<a href="mailto:${addr}?subject=${encodeURIComponent(subject)}">${addr}</a>`;
};
const L = (page, text) => html`<a href="/legal/${page}" data-link>${text}</a>`;

export const PAGES = [
  { key: "terms", title: "Terms of Service", short: "Terms" },
  { key: "privacy", title: "Privacy Policy", short: "Privacy" },
  { key: "copyright", title: "Copyright & DMCA Policy", short: "Copyright / DMCA" },
  { key: "refunds", title: "Refund & Cancellation Policy", short: "Refunds" },
  { key: "contact", title: "Contact & Grievance Officer", short: "Contact" },
];

// Each page: a list of [id, heading, body]. Bodies are built with the escaping html`` tag.
const CONTENT = {
  terms: () => [
    ["acceptance", "1. Agreement to these terms", html`<p>These Terms of Service ("Terms") are an agreement between you and ${P("operator")} ("we", "us"), the operator of ${LEGAL.app} (the "Service"), a music streaming web application and progressive web app. By creating an account or using the Service you accept these Terms, our ${L("privacy", "Privacy Policy")}, our ${L("copyright", "Copyright & DMCA Policy")} and our ${L("refunds", "Refund & Cancellation Policy")}. If you do not agree, please do not use the Service.</p>`],
    ["eligibility", "2. Who can use PENDEL", html`<ul>
      <li>You must be at least <strong>13 years old</strong> to use the Service.</li>
      <li><strong>Users in India under 18</strong> are children under the Digital Personal Data Protection Act, 2023. They may use the Service only with the verifiable consent of a parent or lawful guardian, who accepts these Terms on their behalf. If you are under 18 in India, please ask your parent or guardian to contact us at ${mail("Parental consent")} before you register.</li>
      <li>If you live in a country where the age of digital consent is higher than 13 (for example some EU member states, where it can be up to 16), you must meet that age or have parental consent.</li>
      <li>You must be legally able to enter into this agreement and not barred from using the Service under any applicable law.</li></ul>`],
    ["accounts", "3. Your account", html`<ul>
      <li>Give accurate information when you register and keep it up to date.</li>
      <li>Keep your password confidential. You are responsible for activity on your account. Tell us at ${mail("Account security")} if you suspect unauthorised access; you can also use <em>Log out of all devices</em> on your Profile.</li>
      <li>One person per account. Accounts are personal and may not be sold, shared or transferred.</li>
      <li>You can delete your account at any time from <a href="/profile" data-link>Profile → Your data</a>.</li>
      <li>We may suspend or close accounts that break these Terms, and we will tell you why unless the law prevents us.</li></ul>`],
    ["acceptable-use", "4. Acceptable use", html`<p>You agree not to:</p><ul>
      <li>upload, share or stream content you do not have the rights to, or content that is unlawful, obscene, defamatory, hateful, harassing, invasive of privacy, harmful to children, or that threatens the unity, integrity, security or public order of India, or is otherwise prohibited by Rule 3(1)(b) of the Information Technology (Intermediary Guidelines and Digital Media Ethics Code) Rules, 2021;</li>
      <li>download, rip, record, copy or redistribute streams, or bypass any technical protection, region or licence restriction;</li>
      <li>scrape the Service, use bots to inflate plays, or manipulate charts, highlights or recommendations;</li>
      <li>probe, attack or disrupt the Service, its servers or other users, or attempt to access accounts or admin areas you are not authorised to use;</li>
      <li>impersonate any person or misrepresent your connection with an artist, label or rights holder;</li>
      <li>use the Service for any commercial public performance (shops, venues, broadcasts) without the appropriate public-performance licences.</li></ul>`],
    ["youtube", "5. YouTube playback", html`<p>Some tracks in ${LEGAL.app} are played through the official embedded YouTube player using YouTube API Services. ${LEGAL.app} does not download, store, extract or re-host audio or video from YouTube, and the YouTube player stays visible while it plays.</p>
      <p><strong>By using ${LEGAL.app} you agree to be bound by the <a href="https://www.youtube.com/t/terms" target="_blank" rel="noopener">YouTube Terms of Service</a></strong>, and YouTube's handling of your data is governed by the <a href="https://policies.google.com/privacy" target="_blank" rel="noopener">Google Privacy Policy</a>. YouTube content belongs to its respective owners; ${LEGAL.app} is not affiliated with or endorsed by YouTube or Google. Availability of YouTube content can change at any time, and we are not responsible for content served by YouTube.</p>`],
    ["uploader-warranty", "6. Uploader copyright warranty", html`<div class="legal-callout" role="note"><p><strong>Anyone who uploads audio, artwork, lyrics or other content to ${LEGAL.app} — whether a user, an artist, a content manager or an administrator — confirms and warrants, every time they upload, that:</strong></p><ol>
      <li>they own <strong>100% of the master (sound recording) rights and 100% of the publishing (musical work and lyrics) rights</strong> in the content, <strong>or</strong> hold a <strong>valid, written distribution licence</strong> from every owner that permits streaming it on ${LEGAL.app} in every territory where it is made available;</li>
      <li>all performers, producers, composers, lyricists and featured artists have given every consent required, and any samples or interpolations are cleared;</li>
      <li>the content does not infringe any copyright, trademark, performer's right, moral right, privacy or other right of any person; and</li>
      <li>they will provide proof of ownership or licence (contracts, distributor statements, PRO / IPRS / PPL registrations) within 7 days if we ask.</li></ol>
      <p>The uploader grants us a non-exclusive, royalty-free, worldwide licence to host, transcode, store (including on Cloudflare R2 cloud storage), stream and display the content within the Service for as long as it is published, and to create short highlights and artwork thumbnails for discovery. The uploader <strong>indemnifies</strong> ${P("operator")} against all claims, losses, damages and costs (including reasonable legal fees) arising from a breach of this warranty. Content may be removed without notice when we receive a valid infringement notice or cannot verify the rights.</p></div>`],
    ["our-content", "7. Our rights and your licence to listen", html`<p>The ${LEGAL.app} name, logo, design and software belong to us or our licensors. Music and artwork belong to their respective owners. We give you a personal, non-exclusive, non-transferable, revocable licence to stream content through the Service for private, non-commercial listening. Nothing in these Terms transfers ownership of any content to you.</p>`],
    ["plans", "8. Free and Premium plans", html`<p>The Free plan gives highlights, previews and ad-supported listening with a limited number of skips. Premium is a paid digital subscription billed through a third-party payment gateway. Prices, taxes and billing periods are shown before you pay. Cancellation and refunds are covered by our ${L("refunds", "Refund & Cancellation Policy")}.</p>`],
    ["intermediary", "9. Our role as an intermediary", html`<p>For content uploaded by users, we act as an intermediary under Section 79 of the Information Technology Act, 2000 and as a service provider under 17 U.S.C. §512 (US DMCA). We do not initiate the transmission, select the receiver or modify user-uploaded content, and we act on valid notices as described in our ${L("copyright", "Copyright & DMCA Policy")}. We may remove content or disable access to it to comply with court orders, government directions or these Terms.</p>`],
    ["disclaimer", "10. Disclaimers and limitation of liability", html`<p>The Service is provided "as is" and "as available". To the fullest extent the law allows, we disclaim all warranties, express or implied, including fitness for a particular purpose and non-infringement, and we do not promise that the Service or any track will always be available or error-free. To the fullest extent the law allows, our total liability for any claim relating to the Service is limited to the amount you paid us in the 12 months before the claim (or ₹1,000 if you paid nothing), and we are not liable for indirect or consequential losses. Nothing in these Terms limits liability that cannot be limited by law, or your statutory rights as a consumer.</p>`],
    ["changes", "11. Changes and termination", html`<p>We may update these Terms. For material changes we will give notice in the app or by email at least 7 days before they take effect. You may stop using the Service and delete your account at any time. Sections 6, 7, 10 and 12 survive termination.</p>`],
    ["law", "12. Governing law and disputes", html`<p>These Terms are governed by the laws of India. Subject to any mandatory consumer-protection rights you have where you live, the courts at ${P("location")} have exclusive jurisdiction. Please raise any complaint with our ${L("contact", "Grievance Officer")} first; most issues are resolved quickly that way.</p>`],
  ],

  privacy: () => [
    ["who", "1. Who we are", html`<p>${LEGAL.app} is operated by ${P("operator")}, ${P("location")}, who is the <strong>Data Fiduciary</strong> under India's Digital Personal Data Protection Act, 2023 ("DPDP Act") and the <strong>controller</strong> under the EU / UK General Data Protection Regulation ("GDPR"). Contact: ${mail("Privacy request")}.</p>`],
    ["collect", "2. What we collect", html`<div class="table-wrap"><table class="legal-table"><caption class="sr-only">Personal data we collect</caption>
      <thead><tr><th scope="col">Data</th><th scope="col">Examples</th><th scope="col">Why</th></tr></thead><tbody>
      <tr><td>Account details</td><td>Name, email address, password (stored only as a salted hash), profile photo, sign-in method (email or Google)</td><td>Create and secure your account</td></tr>
      <tr><td>Listening activity</td><td>Songs played, how long, highlights vs full plays, skips, favourites</td><td>Resume playback, history, recommendations, free-tier skip limits, royalty and licence reporting</td></tr>
      <tr><td>Your library</td><td>Playlists, followed playlists, preferred languages and moods</td><td>Provide the features you use</td></tr>
      <tr><td>Payment metadata (Premium only)</td><td>Plan, amount, currency, status, payment method type, gateway order / payment IDs</td><td>Billing, invoices, refunds, tax and accounting records</td></tr>
      <tr><td>Technical data</td><td>IP address, browser type, error logs (kept briefly by our host)</td><td>Security, abuse prevention, debugging</td></tr>
      <tr><td>Uploads (uploaders only)</td><td>Audio files, artwork, rights and licence details</td><td>Host and stream licensed content; respond to copyright claims</td></tr>
      </tbody></table></div>
      <p><strong>We never receive or store your full card number, CVV, UPI PIN or bank login.</strong> Payments are processed directly by the payment gateway (such as Razorpay, Cashfree or Stripe), which is responsible for that data under its own PCI-DSS certified systems and privacy policy.</p>
      <p>Voice control uses your browser's built-in speech service and is <strong>off by default</strong>. The microphone is used only after you tap the mic button, for a single command (or at most 8 seconds), and then switches off by itself; tap it again to stop sooner. ${LEGAL.app} does not record or store your audio. Your browser's speech service (for Chrome, Google's servers) receives the audio in order to convert it to text, under its own privacy policy; only the resulting command (for example a song name to search) reaches ${LEGAL.app}. Your browser shows a microphone indicator while it listens.</p>`],
    ["storage", "3. Local storage, cookies and offline caching", html`<p>${LEGAL.app} does not use advertising or cross-site tracking cookies. It stores a few items in your browser's <code>localStorage</code> / <code>sessionStorage</code>, which stay on your device:</p><ul>
      <li><code>rf_token</code> — keeps you signed in; <code>rf_theme</code>, <code>rf_vol</code>, <code>rf_autoplay</code>, <code>rf_dock</code>, <code>rf_lyr_tr</code> — your display and player settings; <code>rf_skips</code>, <code>rf_tracks</code>, <code>rf_ad_at</code> — free-tier skip and ad counters.</li>
      <li>As a progressive web app, a <strong>service worker</strong> caches the app shell (HTML, styles, scripts, icons) so it loads fast and opens offline. It <strong>never caches your account data or audio streams</strong>.</li></ul>
      <p>These items are strictly necessary for the features you choose, so no consent banner is needed. You can clear them at any time by logging out and clearing site data in your browser settings.</p>`],
    ["basis", "4. Legal basis and consent", html`<p>Under the DPDP Act we process personal data for the specified purposes above, on the basis of your consent given when you create an account, and for legitimate uses permitted by Section 7 (such as complying with law). Under the GDPR our bases are: performance of our contract with you (account, streaming, billing), legitimate interests (security, fraud prevention, improving recommendations), legal obligation (tax records) and consent where we ask for it. You can withdraw consent at any time by deleting your account; this does not affect processing already done.</p>`],
    ["children", "5. Children", html`<p>The Service is not for anyone under 13. For users in India under 18 we require verifiable parental consent before processing their data, and we do not carry out tracking, behavioural monitoring or targeted advertising directed at children. If you believe a child has registered without consent, contact ${mail("Child account")} and we will delete the account.</p>`],
    ["sharing", "6. Who we share data with", html`<p>We do not sell your personal data. We share it only with service providers that help us run ${LEGAL.app}, under contracts that limit their use of it:</p><ul>
      <li><strong>Render</strong> (application hosting) and <strong>Neon</strong> (PostgreSQL database) — servers may be located in the United States;</li>
      <li><strong>Cloudflare R2</strong> (storage of uploaded audio, artwork and profile photos);</li>
      <li><strong>Google</strong> (optional "Sign in with Google", and the YouTube embedded player — see ${L("terms#youtube", "YouTube playback")});</li>
      <li><strong>Payment gateways</strong> such as Razorpay, Cashfree or Stripe (Premium payments only);</li>
      <li>authorities, courts or rights holders when the law requires it, or to protect rights and safety.</li></ul>
      <p>Where data is transferred outside India or the EEA / UK, we rely on the transfer being permitted under Section 16 of the DPDP Act and, for GDPR, on adequacy decisions or the European Commission's Standard Contractual Clauses offered by these providers.</p>`],
    ["retention", "7. How long we keep data", html`<ul>
      <li>Account, library and listening data: for as long as your account is active.</li>
      <li>When you delete your account, your profile, email, photo, playlists, favourites, history and preferences are erased or irreversibly anonymised straight away.</li>
      <li>Payment and invoice records are kept, without your name or email attached, for up to 8 years as required by Indian tax and accounting law (Companies Act, 2013 / Income-tax Act, 1961 / GST law).</li>
      <li>Server logs are kept by our host for a short period (typically up to 30 days).</li></ul>`],
    ["rights", "8. Your rights", html`<p>Under the DPDP Act (Sections 11–14) and the GDPR (Articles 15–22) you can:</p><ul>
      <li><strong>Access</strong> a summary of your data and how it is processed — use <a href="/profile" data-link>Profile → Your data → Download my data</a> for an instant JSON copy (this also covers <strong>data portability</strong>);</li>
      <li><strong>Correct or update</strong> your name, preferences and photo on your Profile, or ask us to correct anything else;</li>
      <li><strong>Erase</strong> your data — use <a href="/profile" data-link>Profile → Your data → Delete my account</a>;</li>
      <li><strong>Withdraw consent</strong>, and (GDPR) <strong>object to or restrict</strong> processing;</li>
      <li><strong>Nominate</strong> another person to exercise your rights if you die or become incapacitated (DPDP Act, Section 14) by emailing us;</li>
      <li><strong>Complain</strong> to our ${L("contact", "Grievance Officer")}. If you are not satisfied, you may complain to the Data Protection Board of India, or (in the EU / UK) to your local data protection supervisory authority.</li></ul>
      <p>We respond to rights requests sent by email within 30 days, and usually much sooner. We may need to verify your identity first.</p>`],
    ["security", "9. Security", html`<p>Passwords are hashed with a strong, salted algorithm; all traffic uses HTTPS; protected audio is served through short-lived signed URLs; admin areas require role-based permissions. No system is perfectly secure. If a personal data breach affects you, we will notify you and the Data Protection Board of India (and, where applicable, the relevant GDPR authority) as the law requires.</p>`],
    ["grievance", "10. Grievance Officer", html`<p>In line with the DPDP Act and the Information Technology (Intermediary Guidelines and Digital Media Ethics Code) Rules, 2021:</p>${officerCard()}`],
    ["changes", "11. Changes to this policy", html`<p>We will post any changes on this page with a new "last updated" date, and tell you in the app or by email before material changes take effect.</p>`],
  ],

  copyright: () => [
    ["respect", "1. Our commitment", html`<p>${LEGAL.app} respects the rights of artists, songwriters, labels and publishers. We stream only content that is licensed to us, played through official embeds, or uploaded by people who warrant they own or license it (see the ${L("terms#uploader-warranty", "Uploader copyright warranty")}). We act as an intermediary under <strong>Section 79 of India's Information Technology Act, 2000</strong> and the IT Rules, 2021, and as a service provider under the <strong>US Digital Millennium Copyright Act (17 U.S.C. §512)</strong>.</p>`],
    ["notice", "2. How to submit a takedown notice", html`<p>If you own the copyright (or are authorised to act for the owner) and believe content on ${LEGAL.app} infringes it, email ${mail("Copyright takedown notice", "dmca")} with the subject line <strong>"Copyright takedown notice"</strong> and include:</p><ol>
      <li>your full name, postal address, phone number and email address;</li>
      <li>identification of the copyrighted work (song title, artist, ISRC / ISWC if available);</li>
      <li>the exact location of the infringing material on ${LEGAL.app} — the song page URL (for example <code>https://pendel.onrender.com/song/…</code>);</li>
      <li>proof of ownership or authority (registration, contract or distributor statement);</li>
      <li>a statement that you have a good-faith belief that the use is not authorised by the copyright owner, its agent or the law;</li>
      <li>a statement that the information in your notice is accurate and, <strong>under penalty of perjury</strong>, that you are the owner or authorised to act on the owner's behalf; and</li>
      <li>your physical or electronic signature.</li></ol>
      <p>Notices under Indian law may also be sent with a copy of any court order or government direction, which we will comply with within the time it specifies (and in any case within 36 hours where the IT Rules require).</p>`],
    ["timeline", "3. What happens next", html`<div class="legal-callout" role="note"><ul>
      <li><strong>Within 24 hours</strong> we acknowledge your notice.</li>
      <li><strong>Within 24–36 hours</strong> of a complete, valid notice we review it and <strong>remove or disable access to the infringing audio</strong>, including files hosted on our Cloudflare R2 storage, and take the song page down.</li>
      <li>We notify the uploader, record the takedown in our audit log, and keep a copy of the notice.</li></ul></div>
      <p>Incomplete notices may delay action; we will tell you what is missing. Content played through the YouTube embed is hosted by YouTube: we will remove it from ${LEGAL.app} on notice, but to remove the video itself please also use <a href="https://support.google.com/youtube/answer/2807622" target="_blank" rel="noopener">YouTube's copyright process</a>.</p>`],
    ["counter", "4. Counter-notice", html`<p>If your content was removed and you believe that was a mistake or that you have the rights, email ${mail("Copyright counter-notice", "dmca")} with: your contact details; identification of the removed content and where it appeared; a statement <strong>under penalty of perjury</strong> that you have a good-faith belief it was removed by mistake or misidentification; your consent to the jurisdiction of the courts at ${P("location")} (or, for US users, your local US federal district court) and to accept service of process from the complainant; and your signature. We forward valid counter-notices to the complainant and may restore the content in 10–14 business days unless they tell us they have started court proceedings.</p>`],
    ["repeat", "5. Repeat infringers", html`<p>We will terminate the accounts and upload access of users who are the subject of repeated valid infringement notices.</p>`],
    ["misuse", "6. False notices", html`<p>Knowingly misrepresenting that content is infringing, or that it was removed by mistake, can make you liable for damages (including under 17 U.S.C. §512(f)). If you are unsure whether a use is infringing, please get legal advice before sending a notice.</p>`],
    ["agent", "7. Designated copyright agent", html`${officerCard("Copyright agent", "dmca")}<p class="muted small">Note for the operator: to rely on the US DMCA safe harbour you must also register a designated agent with the US Copyright Office (copyright.gov/dmca-directory).</p>`],
  ],

  refunds: () => [
    ["free", "1. Free tier", html`<p>${LEGAL.app}'s Free plan costs nothing and needs no payment details. It includes highlights, previews and ad-supported listening with a limited number of skips per hour.</p>`],
    ["premium", "2. Premium subscriptions", html`<ul>
      <li>Premium is a <strong>digital service</strong> billed in advance for the period you choose (monthly or yearly). The price, taxes (including GST where applicable) and billing period are shown before you pay.</li>
      <li>Payments are processed securely by our payment partners (such as Razorpay, Cashfree or Stripe). We never see your full card or bank details.</li>
      <li>Premium starts immediately after a successful payment, and you'll see it on your Profile.</li></ul>`],
    ["cancel", "3. Cancelling", html`<p>You can cancel at any time from <a href="/profile" data-link>Profile → Your plan → Cancel subscription</a>. Cancellation stops the next renewal. You keep Premium until the end of the period you have already paid for, then move to the Free plan automatically. No cancellation fee applies.</p>`],
    ["refunds", "4. Refunds", html`<div class="legal-callout" role="note"><p><strong>Because Premium gives you immediate access to digital content, subscription fees are non-refundable once the subscription has been activated</strong>, including for partially used billing periods, except as set out below or where the law requires otherwise.</p></div>
      <p>We will refund in full:</p><ul>
      <li>duplicate charges, or payments deducted where Premium was <strong>not</strong> activated because of a technical failure;</li>
      <li>charges you did not authorise, once verified (please also contact your bank);</li>
      <li>cases where we are unable to provide Premium for a substantial part of a paid period because of a fault on our side.</li></ul>
      <p>To request a refund, email ${mail("Refund request")} within <strong>7 days</strong> of the charge with your registered email, the date, amount and the payment / order ID. Approved refunds are sent to the original payment method within <strong>5–7 business days</strong> (bank timelines may vary).</p>
      <p><strong>EU / UK consumers:</strong> you have a 14-day right of withdrawal from distance contracts. By starting Premium immediately you expressly request and agree that the service begins during that period and acknowledge that you lose the right of withdrawal once access has been provided.</p>`],
    ["failed", "5. Failed or pending payments", html`<p>If money was deducted but Premium is not active, it is usually because the bank has not yet confirmed the payment. Wait 30 minutes and refresh. If the payment ultimately fails, the gateway automatically reverses the amount to your account, typically within 5–7 business days.</p>`],
    ["changes", "6. Price changes", html`<p>We will tell you about any price change before it applies to your next renewal. If you don't agree, you can cancel before the renewal date.</p>`],
  ],

  contact: () => [
    ["contact", "Get in touch", html`<p>For support, account, privacy or billing questions, email ${mail(`${LEGAL.app} support`)}. Please write from your registered email address and include your song links, payment IDs or screenshots where relevant.</p>
      <ul><li>Complaints under the IT Act / IT Rules or the DPDP Act: ${mail("Grievance", "grievance")}</li>
      <li>Copyright / DMCA takedown and counter-notices: ${mail("Copyright takedown notice", "dmca")}</li></ul>`],
    ["grievance", "Grievance Officer", html`<p>Appointed under Rule 3(2) of the Information Technology (Intermediary Guidelines and Digital Media Ethics Code) Rules, 2021 and for the purposes of Section 8(10) and Section 13 of the Digital Personal Data Protection Act, 2023.</p>${officerCard()}
      <ul><li>We <strong>acknowledge complaints within 24 hours</strong> and <strong>resolve them within 15 days</strong> of receipt.</li>
      <li>Complaints about content that exposes a person's private areas, shows nudity or sexual acts, or impersonates someone are acted on within 24 hours.</li>
      <li>Copyright complaints follow our ${L("copyright", "Copyright & DMCA Policy")} (removal within 24–36 hours of a valid notice).</li></ul>
      <p>If you are not satisfied with our response to a personal-data complaint, you may approach the Data Protection Board of India. EU / UK users may contact their local data protection authority.</p>`],
  ],
};

function officerCard(role = "Grievance Officer", to = "grievance") {
  return html`<address class="legal-officer">
    <div><span class="lbl">${role}</span><strong>${P("operator")}</strong></div>
    <div><span class="lbl">Email</span>${mail(role, to)}</div>
    <div><span class="lbl">Location</span>${P("location")}</div>
    ${LEGAL.address ? html`<div><span class="lbl">Address</span>${P("address")}</div>` : ""}
    <div><span class="lbl">Hours</span>Monday to Friday, 10:00 AM – 6:00 PM IST</div>
  </address>`;
}

const tabs = (active) => html`<nav class="legal-tabs" aria-label="Legal documents">${PAGES.map((p) =>
  html`<a href="/legal/${p.key}" data-link class="chip ${p.key === active ? "active" : ""}" ${raw(p.key === active ? 'aria-current="page"' : "")}>${p.short}</a>`)}</nav>`;

export function legalView(view, page) {
  const key = (page || "").toLowerCase();
  if (!key) {
    render(view, html`<article class="legal" aria-labelledby="legal-h1"><h1 id="legal-h1">Legal</h1>
      <p class="muted">How ${LEGAL.app} works, what we do with your data and how to reach us. Last updated ${LEGAL.updated}.</p>
      <div class="legal-index">${PAGES.map((p) => html`<a class="legal-card" href="/legal/${p.key}" data-link><strong>${p.title}</strong><span>${({
        terms: "Eligibility, acceptable use, YouTube playback and the uploader copyright warranty.",
        privacy: "What we collect, local storage, your DPDP Act and GDPR rights.",
        copyright: "Send a takedown notice under the IT Act and the DMCA.",
        refunds: "Free tier, Premium billing, cancelling and refunds.",
        contact: "Support and our Grievance Officer.",
      })[p.key]}</span></a>`)}</div></article>`);
    document.title = `Legal | ${LEGAL.app}`;
    return;
  }
  const meta = PAGES.find((p) => p.key === key);
  if (!meta) {
    render(view, html`<article class="legal"><h1>Page not found</h1><p>That legal page doesn't exist. <a href="/legal" data-link>See all legal pages</a>.</p></article>`);
    return;
  }
  const sections = CONTENT[key]();
  render(view, html`<article class="legal" aria-labelledby="legal-h1">
    ${tabs(key)}
    <header><h1 id="legal-h1">${meta.title}</h1><p class="muted small">Last updated ${LEGAL.updated} · Applies to ${LEGAL.app} on the web and as an installed app</p></header>
    ${sections.length > 3 ? html`<nav class="legal-toc" aria-label="On this page"><h2 class="lbl">On this page</h2><ol>${sections.map(([id, h]) => html`<li><a href="#${id}">${h.replace(/^\d+\.\s*/, "")}</a></li>`)}</ol></nav>` : ""}
    ${sections.map(([id, h, body]) => html`<section id="${id}" aria-labelledby="${id}-h"><h2 id="${id}-h" tabindex="-1">${h}</h2>${body}</section>`)}
    <p class="muted small legal-foot">Questions about this page? Email ${mail(`${meta.title} question`)}.</p>
  </article>`);
  document.title = `${meta.title} | ${LEGAL.app}`;
  // In-page links (#section) must not touch location.hash: that would fire popstate and re-run the router.
  view.querySelector(".legal").addEventListener("click", (e) => {
    const a = e.target.closest('a[href^="#"]');
    if (!a) return;
    e.preventDefault();
    jump(view, a.getAttribute("href").slice(1));
  });
  // Deep links like /legal/terms#youtube come through the router as a pending hash.
  const want = pendingHash; pendingHash = "";
  if (want) requestAnimationFrame(() => jump(view, want));
}

let pendingHash = "";
export function setPendingHash(h) { pendingHash = h || ""; }

function jump(view, id) {
  const sec = view.querySelector(`#${CSS.escape(id)}`);
  if (!sec) return;
  sec.scrollIntoView({ behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth", block: "start" });
  $("h2", sec)?.focus({ preventScroll: true });
}
