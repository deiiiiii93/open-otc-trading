const countFormatter = new Intl.NumberFormat('en-US', {
  maximumFractionDigits: 0,
});

const signedFormatters = new Map<number, Intl.NumberFormat>();

function signedFormatter(decimals: number): Intl.NumberFormat {
  const cached = signedFormatters.get(decimals);
  if (cached) return cached;
  const formatter = new Intl.NumberFormat('en-US', {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
    signDisplay: 'always',
  });
  signedFormatters.set(decimals, formatter);
  return formatter;
}

export function formatSignedNumber(n: number, decimals = 4): string {
  if (!Number.isFinite(n)) return String(n);
  return signedFormatter(decimals).format(n);
}

export function formatCount(n: number): string {
  return countFormatter.format(n);
}
