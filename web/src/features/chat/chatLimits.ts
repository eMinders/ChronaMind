/** The server accepts 1 to 3000 characters per message (`app/schemas/chat.py`). */
export const MAX_MESSAGE_LENGTH = 3_000;

/** One click sends each step of the demo script, in English and in Arabic. */
export const DEMO_CHIPS = [
  "I live in Berlin",
  "I moved to Paris last week",
  "Where do I live?",
  "أنا ساكن في برلين",
  "انتقلت إلى باريس",
] as const;
