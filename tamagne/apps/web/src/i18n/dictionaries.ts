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
  nav: { switchLanguage: "አማርኛ", switchLanguageLabel: "Switch to Amharic", signIn: "Sign in", register: "Register", dashboard: "Dashboard", signOut: "Sign out" },
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
  auth: {
    registerTitle: "Create your seller account",
    registerLead: "We'll send a 6-digit code to your phone. Phone verification is Level 1; it is not identity verification.",
    loginTitle: "Sign in",
    loginLead: "Enter the phone number on your TAMAGNE account. We'll send you a code.",
    phoneLabel: "Mobile number",
    phoneHint: "Ethiopian mobile number, e.g. 0911 223 344",
    acceptTerms: "I agree to the Terms of Service",
    acceptPrivacy: "I have read the Privacy Policy and agree to my data being processed as described",
    draftNotice: "Policy texts are drafts pending legal review.",
    sendCode: "Send code",
    codeLabel: "6-digit code",
    codeSentTo: "We sent a code by SMS. It expires in 5 minutes.",
    verify: "Verify and continue",
    changeNumber: "Use a different number",
    resend: "Send a new code",
    haveAccount: "Already registered?",
    noAccount: "New to TAMAGNE?",
    errors: {
      invalid_phone: "Enter a valid Ethiopian mobile number.",
      invalid_code: "That code is incorrect or has expired.",
      rate_limited: "Too many attempts. Please wait a few minutes and try again.",
      sms_unavailable: "We can't send SMS right now. Please try again later.",
      csrf_failed: "Your session changed. Refresh the page and try again.",
      terms: "You need to accept both to continue.",
      generic: "Something went wrong. Please try again.",
    },
  },
  setup: {
    title: "Set up your seller profile",
    lead: "This is what buyers will see. You can change it later.",
    displayName: "Shop or seller name",
    displayNameAm: "Name in Amharic (optional)",
    sellerType: "What kind of seller are you?",
    region: "Region (optional)",
    city: "City (optional)",
    submit: "Create profile",
    types: {
      INDIVIDUAL: "Individual seller",
      SOCIAL_MEDIA_SELLER: "Social media seller",
      TELEGRAM_SHOP: "Telegram shop",
      REGISTERED_BUSINESS: "Registered business",
      ECOMMERCE_COMPANY: "E-commerce company",
      ONLINE_SERVICE_PROVIDER: "Online service provider",
    },
  },
  dashboard: {
    title: "Your verification",
    tamagneId: "Tamagne ID",
    accountStatus: "Account status",
    accountStatuses: { ACTIVE: "Active", UNDER_REVIEW: "Under review", SUSPENDED: "Suspended", DEACTIVATED: "Deactivated" },
    categories: {
      phone: "Level 1 · Phone",
      faydaIdentity: "Level 2 · Fayda identity",
      identityManualReview: "Identity reviewed manually",
      business: "Level 3 · Business documents",
      enhancedReview: "Level 4 · Enhanced review",
    },
    statuses: { VERIFIED: "Verified", NOT_VERIFIED: "Not verified", EXPIRED: "Expired", REVOKED: "Revoked", SUSPENDED: "Suspended" },
    validUntil: "Valid until",
    nextSteps: "Next steps",
    faydaPending: "Fayda identity verification will open once TAMAGNE receives official authorization. Until then, a manual identity review will be offered.",
    businessPending: "Business document upload is coming soon.",
    publicProfile: "Public profile",
    publicOn: "Your profile can be found by buyers.",
    publicOff: "Your profile is hidden from buyers.",
    makePublic: "Show my profile to buyers",
    makePrivate: "Hide my profile",
    suspended: "Your seller account is suspended. Your badges are not shown as current. You can appeal this decision.",
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
  nav: { switchLanguage: "English", switchLanguageLabel: "ወደ እንግሊዝኛ ቀይር", signIn: "ግባ", register: "ይመዝገቡ", dashboard: "ዳሽቦርድ", signOut: "ውጣ" },
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
  auth: {
    registerTitle: "የሻጭ መለያ ይፍጠሩ",
    registerLead: "ባለ 6 አሃዝ ኮድ ወደ ስልክዎ እንልካለን። የስልክ ማረጋገጫ ደረጃ 1 ነው፤ የማንነት ማረጋገጫ አይደለም።",
    loginTitle: "ግባ",
    loginLead: "በታማኝ መለያዎ ላይ ያለውን የስልክ ቁጥር ያስገቡ። ኮድ እንልክልዎታለን።",
    phoneLabel: "የሞባይል ቁጥር",
    phoneHint: "የኢትዮጵያ ሞባይል ቁጥር፣ ለምሳሌ 0911 223 344",
    acceptTerms: "የአገልግሎት ውሉን እስማማለሁ",
    acceptPrivacy: "የግላዊነት ፖሊሲውን አንብቤያለሁ፤ መረጃዬ በተገለጸው መሠረት እንዲሠራበት እስማማለሁ",
    draftNotice: "የፖሊሲ ጽሑፎቹ የሕግ ግምገማ በመጠባበቅ ላይ ያሉ ረቂቆች ናቸው።",
    sendCode: "ኮድ ላክ",
    codeLabel: "ባለ 6 አሃዝ ኮድ",
    codeSentTo: "ኮድ በኤስኤምኤስ ልከናል። በ5 ደቂቃ ውስጥ ያበቃል።",
    verify: "አረጋግጥና ቀጥል",
    changeNumber: "ሌላ ቁጥር ተጠቀም",
    resend: "አዲስ ኮድ ላክ",
    haveAccount: "ተመዝግበዋል?",
    noAccount: "ለታማኝ አዲስ ነዎት?",
    errors: {
      invalid_phone: "ትክክለኛ የኢትዮጵያ ሞባይል ቁጥር ያስገቡ።",
      invalid_code: "ኮዱ ትክክል አይደለም ወይም ጊዜው አልፎበታል።",
      rate_limited: "በጣም ብዙ ሙከራዎች። ጥቂት ደቂቃዎች ጠብቀው እንደገና ይሞክሩ።",
      sms_unavailable: "አሁን ኤስኤምኤስ መላክ አልቻልንም። እባክዎ ቆይተው ይሞክሩ።",
      csrf_failed: "ክፍለ ጊዜዎ ተቀይሯል። ገጹን አድሰው እንደገና ይሞክሩ።",
      terms: "ለመቀጠል ሁለቱንም መቀበል ያስፈልጋል።",
      generic: "የሆነ ችግር ተፈጥሯል። እባክዎ እንደገና ይሞክሩ።",
    },
  },
  setup: {
    title: "የሻጭ መገለጫዎን ያዘጋጁ",
    lead: "ገዢዎች የሚያዩት ይህንን ነው። በኋላ መቀየር ይችላሉ።",
    displayName: "የሱቅ ወይም የሻጭ ስም",
    displayNameAm: "ስም በአማርኛ (አማራጭ)",
    sellerType: "ምን ዓይነት ሻጭ ነዎት?",
    region: "ክልል (አማራጭ)",
    city: "ከተማ (አማራጭ)",
    submit: "መገለጫ ፍጠር",
    types: {
      INDIVIDUAL: "ግለሰብ ሻጭ",
      SOCIAL_MEDIA_SELLER: "በማኅበራዊ ሚዲያ የሚሸጥ",
      TELEGRAM_SHOP: "የቴሌግራም ሱቅ",
      REGISTERED_BUSINESS: "የተመዘገበ ንግድ",
      ECOMMERCE_COMPANY: "የኢ-ኮሜርስ ኩባንያ",
      ONLINE_SERVICE_PROVIDER: "የኦንላይን አገልግሎት ሰጪ",
    },
  },
  dashboard: {
    title: "የእርስዎ ማረጋገጫ",
    tamagneId: "የታማኝ መለያ ቁጥር",
    accountStatus: "የመለያ ሁኔታ",
    accountStatuses: { ACTIVE: "ንቁ", UNDER_REVIEW: "በግምገማ ላይ", SUSPENDED: "ታግዷል", DEACTIVATED: "ተዘግቷል" },
    categories: {
      phone: "ደረጃ 1 · ስልክ",
      faydaIdentity: "ደረጃ 2 · የፋይዳ ማንነት",
      identityManualReview: "ማንነት በእጅ ተገምግሟል",
      business: "ደረጃ 3 · የንግድ ሰነዶች",
      enhancedReview: "ደረጃ 4 · የተጠናከረ ግምገማ",
    },
    statuses: { VERIFIED: "ተረጋግጧል", NOT_VERIFIED: "አልተረጋገጠም", EXPIRED: "ጊዜው አልፏል", REVOKED: "ተሰርዟል", SUSPENDED: "ታግዷል" },
    validUntil: "የሚያገለግለው እስከ",
    nextSteps: "ቀጣይ እርምጃዎች",
    faydaPending: "ታማኝ ይፋዊ ፈቃድ ሲያገኝ የፋይዳ ማንነት ማረጋገጫ ይከፈታል። እስከዚያው በእጅ የሚደረግ የማንነት ግምገማ ይቀርባል።",
    businessPending: "የንግድ ሰነድ መጫኛ በቅርቡ ይመጣል።",
    publicProfile: "የሕዝብ መገለጫ",
    publicOn: "ገዢዎች መገለጫዎን ማግኘት ይችላሉ።",
    publicOff: "መገለጫዎ ከገዢዎች ተደብቋል።",
    makePublic: "መገለጫዬን ለገዢዎች አሳይ",
    makePrivate: "መገለጫዬን ደብቅ",
    suspended: "የሻጭ መለያዎ ታግዷል። ባጆችዎ እንደ ወቅታዊ አይታዩም። በዚህ ውሳኔ ላይ ይግባኝ ማለት ይችላሉ።",
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
