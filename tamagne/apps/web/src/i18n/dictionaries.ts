/**
 * UI copy. The Amharic dictionary is typed against the English one, so a missing
 * key is a compile error. Amharic copy must be reviewed by a native-speaking
 * editor before launch (tracked in docs/ROADMAP.md).
 */
const en = {
  meta: {
    title: "TAMAGNE: Trust. Verify. Trade with Confidence.",
    description: "Independent seller identity and business verification for Ethiopian online commerce.",
  },
  nav: { switchLanguage: "አማርኛ", switchLanguageLabel: "Switch to Amharic" },
  hero: {
    tagline: "Trust. Verify. Trade with Confidence.",
    lead: "TAMAGNE helps buyers check who they are dealing with. Sellers verify their phone, identity and business, and buyers confirm the result with a Tamagne ID or QR code.",
  },
  levels: {
    heading: "Verification levels",
    intro: "Each level is a separate check with its own date, expiry and audit history. A higher level never replaces a lower one.",
    items: [
      { n: "1", title: "Phone verified", body: "The seller controls this phone number. This is not identity verification." },
      { n: "2", title: "Fayda identity verified", body: "Identity confirmed through the authorized Fayda eSignet service. Shown only after a successful, validated Fayda response." },
      { n: "3", title: "Business documents verified", body: "A reviewer checked the business licence and registration documents." },
      { n: "4", title: "Enhanced business review", body: "An additional, in-depth TAMAGNE review of the business." },
    ],
    manualNote: "Until official Fayda production access is approved, identity can be reviewed manually. Manual reviews are labelled as such and are never shown as Fayda verification.",
  },
  limits: {
    heading: "What verification means",
    items: [
      "Verification confirms an identity or documents at a point in time.",
      "It does not guarantee honesty, product authenticity or successful delivery.",
      "Always use safe payment methods and report suspicious sellers.",
    ],
  },
  status: {
    heading: "Launch status",
    body: "TAMAGNE is in development. Seller registration and public lookups are not open yet.",
  },
  footer: {
    independence: "TAMAGNE is an independent private platform. It is not a government institution and is not affiliated with the National ID Program.",
    privacy: "Personal data is handled under Ethiopia's Personal Data Protection Proclamation No. 1321/2024.",
  },
};

export type Dictionary = typeof en;

const am: Dictionary = {
  meta: {
    title: "ታማኝ፦ እመኑ። ያረጋግጡ። በልበ ሙሉነት ይገበያዩ።",
    description: "ለኢትዮጵያ የኦንላይን ንግድ ገለልተኛ የሻጭ ማንነት እና የንግድ ማረጋገጫ።",
  },
  nav: { switchLanguage: "English", switchLanguageLabel: "ወደ እንግሊዝኛ ቀይር" },
  hero: {
    tagline: "እመኑ። ያረጋግጡ። በልበ ሙሉነት ይገበያዩ።",
    lead: "ታማኝ ገዢዎች ከማን ጋር እንደሚገበያዩ እንዲያውቁ ይረዳል። ሻጮች ስልካቸውን፣ ማንነታቸውን እና ንግዳቸውን ያረጋግጣሉ፤ ገዢዎች ውጤቱን በታማኝ መለያ ቁጥር ወይም በQR ኮድ ያረጋግጣሉ።",
  },
  levels: {
    heading: "የማረጋገጫ ደረጃዎች",
    intro: "እያንዳንዱ ደረጃ የራሱ ቀን፣ የሚያበቃበት ጊዜ እና የኦዲት ታሪክ ያለው የተለየ ማረጋገጫ ነው። ከፍተኛ ደረጃ ዝቅተኛውን አይተካም።",
    items: [
      { n: "1", title: "ስልክ ተረጋግጧል", body: "ሻጩ ይህን የስልክ ቁጥር ይቆጣጠራል። ይህ የማንነት ማረጋገጫ አይደለም።" },
      { n: "2", title: "የፋይዳ ማንነት ተረጋግጧል", body: "ማንነት በተፈቀደው የፋይዳ eSignet አገልግሎት ተረጋግጧል። የሚታየው ከተሳካና ከተረጋገጠ የፋይዳ ምላሽ በኋላ ብቻ ነው።" },
      { n: "3", title: "የንግድ ሰነዶች ተረጋግጠዋል", body: "ገምጋሚ የንግድ ፈቃድ እና የምዝገባ ሰነዶችን መርምሯል።" },
      { n: "4", title: "የተጠናከረ የንግድ ግምገማ", body: "በታማኝ የሚደረግ ተጨማሪ ጥልቅ የንግድ ግምገማ።" },
    ],
    manualNote: "ይፋዊ የፋይዳ የምርት ፈቃድ እስኪጸድቅ ድረስ ማንነት በእጅ ሊገመገም ይችላል። በእጅ የተደረጉ ግምገማዎች እንደዚያው ይገለጻሉ፤ ፈጽሞ እንደ ፋይዳ ማረጋገጫ አይታዩም።",
  },
  limits: {
    heading: "ማረጋገጫ ምን ማለት ነው",
    items: [
      "ማረጋገጫ ማንነትን ወይም ሰነዶችን በአንድ ወቅት ያረጋግጣል።",
      "ታማኝነትን፣ የምርት ትክክለኛነትን ወይም የተሳካ አቅርቦትን ዋስትና አይሰጥም።",
      "ሁልጊዜ ደህንነቱ የተጠበቀ የክፍያ ዘዴ ይጠቀሙ፤ አጠራጣሪ ሻጮችን ሪፖርት ያድርጉ።",
    ],
  },
  status: {
    heading: "የሥራ ሁኔታ",
    body: "ታማኝ በመገንባት ላይ ነው። የሻጭ ምዝገባ እና የሕዝብ ፍለጋ ገና አልተከፈቱም።",
  },
  footer: {
    independence: "ታማኝ ገለልተኛ የግል መድረክ ነው። የመንግሥት ተቋም አይደለም፤ ከብሔራዊ መታወቂያ ፕሮግራም ጋርም ግንኙነት የለውም።",
    privacy: "የግል መረጃ በኢትዮጵያ የግል መረጃ ጥበቃ አዋጅ ቁጥር 1321/2016 መሠረት ይያዛል።",
  },
};

export const locales = ["en", "am"] as const;
export type Locale = (typeof locales)[number];

const dictionaries: Record<Locale, Dictionary> = { en, am };

export function hasLocale(value: string): value is Locale {
  return (locales as readonly string[]).includes(value);
}

export function getDictionary(locale: Locale): Dictionary {
  return dictionaries[locale];
}
