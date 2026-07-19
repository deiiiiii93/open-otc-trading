export function parseServerDateTime(value: string): Date {
  const timezoneNaiveIso =
    /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?$/.test(value);
  return new Date(timezoneNaiveIso ? `${value}Z` : value);
}
