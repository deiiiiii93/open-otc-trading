export type ReplyOption = {
  label: string;
  description?: string;
  value?: string;
};

export type ReplyOptionsExtraction = {
  options: ReplyOption[];
  contentWithoutOptions: string;
};

const MIN_OPTIONS = 2;
const MAX_OPTIONS = 5;
const MAX_LABEL_LENGTH = 56;
const MAX_DESCRIPTION_LENGTH = 240;

export function extractReplyOptions(content: string): ReplyOptionsExtraction | null {
  if (!content.trim()) return null;

  const normalized = content.replace(/\r\n?/g, '\n');
  const lines = normalized.split('\n');
  let end = lines.length;
  while (end > 0 && lines[end - 1].trim() === '') end -= 1;

  const trailingPrompt = findTrailingPrompt(lines, end);
  let optionsEnd = trailingPrompt.optionsEnd;
  while (optionsEnd > 0 && lines[optionsEnd - 1].trim() === '') optionsEnd -= 1;

  let start = optionsEnd;
  const options: ReplyOption[] = [];
  while (start > 0) {
    const parsed = parseOptionLine(lines[start - 1]);
    if (!parsed) break;
    options.unshift(parsed);
    start -= 1;
  }

  if (options.length < MIN_OPTIONS || options.length > MAX_OPTIONS) return null;
  if (isInsideFence(lines.slice(0, start).join('\n'))) return null;
  const contextLines = [...lines.slice(0, start).slice(-4), ...trailingPrompt.lines];
  if (!hasChoiceContext(contextLines.join('\n'))) return null;
  if (!labelsAreUnique(options)) return null;

  return {
    options,
    contentWithoutOptions: contentWithoutOptions(lines.slice(0, start), trailingPrompt.lines),
  };
}

function parseOptionLine(line: string): ReplyOption | null {
  const bullet = line.match(/^\s*(?:[-*+]|\d+[.)])\s+(.+?)\s*$/);
  const icon = bullet
    ? null
    : line.match(/^\s*(?:[^\p{L}\p{N}\s`*_#-][^\p{L}\p{N}\s]*)+\s+(.+?)\s*$/u);
  if (!bullet && !icon) return null;

  const raw = (bullet?.[1] ?? icon?.[1] ?? '').trim();
  if (/^\[[ xX]\]\s+/.test(raw)) return null;

  const parsed = parseLabelAndDescription(raw);
  if (!parsed) return null;
  if (icon && !parsed.description) return null;

  const label = cleanInlineMarkdown(parsed.label).replace(/[.:]+$/, '').trim();
  const description = cleanInlineMarkdown(parsed.description ?? '');
  if (!isValidLabel(label)) return null;
  if (description.length > MAX_DESCRIPTION_LENGTH) return null;

  return description ? { label, description } : { label };
}

function parseLabelAndDescription(raw: string): { label: string; description?: string } | null {
  const bold = raw.match(/^\*\*([^*\n]{1,48})\*\*\s*(.*)$/);
  if (bold) {
    return {
      label: bold[1],
      description: stripLeadingSeparator(bold[2]),
    };
  }

  const code = raw.match(/^`([^`\n]{1,48})`\s*(.*)$/);
  if (code) {
    return {
      label: code[1],
      description: stripLeadingSeparator(code[2]),
    };
  }

  const separated = raw.match(/^(.{1,56}?)(?:\s*(?:->|=>|\u2192|:)\s*|\s+[-\u2013\u2014]\s+)(.+)$/);
  if (separated) {
    return {
      label: separated[1],
      description: separated[2],
    };
  }

  return { label: raw };
}

function stripLeadingSeparator(value: string): string {
  return value
    .replace(/^\s*(?:->|=>|\u2192|:|[-\u2013\u2014])\s*/, '')
    .trim();
}

function cleanInlineMarkdown(value: string): string {
  return value
    .trim()
    .replace(/^["']|["']$/g, '')
    .replace(/\*\*/g, '')
    .replace(/__/g, '')
    .replace(/`/g, '')
    .trim();
}

function isValidLabel(label: string): boolean {
  if (!label || label.length > MAX_LABEL_LENGTH) return false;
  if (label.split(/\s+/).filter(Boolean).length > 8) return false;
  if (/[{}\[\]|]/.test(label)) return false;
  if (/[.!?]\s+\S/.test(label)) return false;
  return true;
}

function hasChoiceContext(context: string): boolean {
  const text = context.trim();
  if (!text) return false;
  return /[?？]/.test(text)
    || /\b(choose|select|pick|option|options|which|confirm|proceed|reply|respond|answer)\b/i.test(text)
    || /\b(do you want|would you like|should i|shall i)\b/i.test(text);
}

function findTrailingPrompt(
  lines: string[],
  end: number,
): { lines: string[]; optionsEnd: number } {
  if (end === 0) return { lines: [], optionsEnd: end };

  const last = lines[end - 1].trim();
  if (parseOptionLine(last) || !isTrailingChoicePrompt(last)) {
    return { lines: [], optionsEnd: end };
  }

  let optionsEnd = end - 1;
  while (optionsEnd > 0 && lines[optionsEnd - 1].trim() === '') optionsEnd -= 1;
  return { lines: [last], optionsEnd };
}

function isTrailingChoicePrompt(line: string): boolean {
  return /[?？]/.test(line)
    && /\b(choose|select|pick|option|options|which|way|would you like|do you want)\b/i.test(line);
}

function contentWithoutOptions(beforeLines: string[], afterLines: string[]): string {
  const before = beforeLines.join('\n').trimEnd();
  const after = afterLines.join('\n').trim();
  if (!before) return after;
  if (!after) return before;
  return `${before}\n\n${after}`;
}

function labelsAreUnique(options: ReplyOption[]): boolean {
  const normalized = options.map((option) => option.label.toLowerCase());
  return new Set(normalized).size === normalized.length;
}

function isInsideFence(text: string): boolean {
  const matches = text.match(/```/g);
  return !!matches && matches.length % 2 === 1;
}
