export const TAIPEI_TIMEZONE = "Asia/Taipei";

const taipeiDateTimeFormatter = new Intl.DateTimeFormat("en-CA", {
  timeZone: TAIPEI_TIMEZONE,
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hourCycle: "h23",
});

function parseUtcDate(value) {
  if (value instanceof Date) return value;
  if (typeof value === "number") return new Date(value);

  const text = String(value ?? "").trim();
  if (!text) return null;

  // MongoDB/PyMongo may serialize a UTC datetime without an explicit suffix.
  const hasOffset = /(?:z|[+-]\d{2}:?\d{2}|\b(?:gmt|utc)\b)/i.test(text);
  const normalized = hasOffset ? text : `${text.replace(" ", "T")}Z`;
  const parsed = new Date(normalized);
  return Number.isNaN(parsed.getTime()) ? null : parsed;
}

export function formatTaipeiDateTime(value, fallback = "-") {
  const date = parseUtcDate(value);
  if (!date) return fallback;

  const parts = Object.fromEntries(
    taipeiDateTimeFormatter
      .formatToParts(date)
      .filter((part) => part.type !== "literal")
      .map((part) => [part.type, part.value]),
  );

  return `${parts.year}-${parts.month}-${parts.day} ${parts.hour}:${parts.minute}:${parts.second}`;
}
