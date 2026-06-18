import { describe, expect, it } from 'vitest';
import { extractReplyOptions } from './replyOptions';

describe('extractReplyOptions', () => {
  it('extracts terminal Yes/No options with descriptions', () => {
    const arrow = String.fromCharCode(8594);
    const result = extractReplyOptions([
      'Do you want to proceed with repricing all 104 positions (~47s)?',
      '',
      `- **Yes** ${arrow} I'll reprice the book, then run the full risk report end-to-end`,
      `- **No** ${arrow} I'll proceed with stored data and note the staleness`,
    ].join('\n'));

    expect(result?.contentWithoutOptions).toBe(
      'Do you want to proceed with repricing all 104 positions (~47s)?',
    );
    expect(result?.options).toEqual([
      {
        label: 'Yes',
        description: "I'll reprice the book, then run the full risk report end-to-end",
      },
      {
        label: 'No',
        description: "I'll proceed with stored data and note the staleness",
      },
    ]);
  });

  it('extracts labels separated by ASCII arrows', () => {
    const result = extractReplyOptions([
      'Which route should I use?',
      '- Manual -> Keep current stored values',
      '- Refresh -> Pull fresh market data first',
    ].join('\n'));

    expect(result?.options).toEqual([
      { label: 'Manual', description: 'Keep current stored values' },
      { label: 'Refresh', description: 'Pull fresh market data first' },
    ]);
  });

  it('extracts icon-prefixed options followed by a trailing question', () => {
    const clipboard = String.fromCodePoint(0x1F4CB);
    const refresh = String.fromCodePoint(0x1F504);
    const page = String.fromCodePoint(0x1F4C4);
    const dash = String.fromCharCode(8212);
    const result = extractReplyOptions([
      'However, it is 4 days old, has 3 positions with pricing degradation, and only ran as a "summary" (no stress/scenario results).',
      '',
      'You have three options:',
      '',
      `${clipboard} Use what exists ${dash} I'll have risk_manager compile a comprehensive written report from the stored data. Fastest, no HITL needed for the write-up (only if we try to create_report system tool).`,
      `${refresh} Fresh risk run first, then report ${dash} re-run risk on 104 positions (~52s ETA). I'd recommend dispatching it async so you can keep working. Cost preview first.`,
      `${page} Full governance-grade report via create_report ${dash} clean risk run + persisted published report. HITL-gated.`,
      'Which way would you like to go?',
    ].join('\n'));

    expect(result?.contentWithoutOptions).toBe([
      'However, it is 4 days old, has 3 positions with pricing degradation, and only ran as a "summary" (no stress/scenario results).',
      '',
      'You have three options:',
      '',
      'Which way would you like to go?',
    ].join('\n'));
    expect(result?.options).toEqual([
      {
        label: 'Use what exists',
        description: "I'll have risk_manager compile a comprehensive written report from the stored data. Fastest, no HITL needed for the write-up (only if we try to create_report system tool).",
      },
      {
        label: 'Fresh risk run first, then report',
        description: "re-run risk on 104 positions (~52s ETA). I'd recommend dispatching it async so you can keep working. Cost preview first.",
      },
      {
        label: 'Full governance-grade report via create_report',
        description: 'clean risk run + persisted published report. HITL-gated.',
      },
    ]);
  });

  it('does not extract ordinary non-choice bullet lists', () => {
    const result = extractReplyOptions([
      'Latest risk notes:',
      '- **Delta**: 100',
      '- **Gamma**: 25',
    ].join('\n'));

    expect(result).toBeNull();
  });

  it('does not extract non-terminal lists', () => {
    const result = extractReplyOptions([
      'Do you want to proceed?',
      '- **Yes**: Run it',
      '- **No**: Stop here',
      '',
      'I can wait for your answer.',
    ].join('\n'));

    expect(result).toBeNull();
  });

  it('does not extract more than five options', () => {
    const result = extractReplyOptions([
      'Choose one option:',
      '- A: one',
      '- B: two',
      '- C: three',
      '- D: four',
      '- E: five',
      '- F: six',
    ].join('\n'));

    expect(result).toBeNull();
  });

  it('does not extract option-looking bullets from an open code fence', () => {
    const result = extractReplyOptions([
      'Example:',
      '```md',
      'Do you want to proceed?',
      '- **Yes**: Run it',
      '- **No**: Stop here',
    ].join('\n'));

    expect(result).toBeNull();
  });
});
