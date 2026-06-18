export const PROVIDER_COLOR: Record<string, string> = {
  anthropic: '#e8743b',
  openai: '#19c37d',
  deepseek: '#4d7cff',
  meta: '#0866ff',
  mistral: '#ff7000',
};

export const PROVIDER_COLOR_DEFAULT = '#888888';

export function colorForProvider(provider: string | undefined): string {
  return PROVIDER_COLOR[provider ?? ''] ?? PROVIDER_COLOR_DEFAULT;
}
