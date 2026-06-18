import { afterEach, describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ScenarioGridDialog } from './ScenarioGridDialog';

describe('ScenarioGridDialog', () => {
  afterEach(() => vi.restoreAllMocks());

  it('previews the cross-product cell count and submits a fraction-scaled grid', async () => {
    const onGenerate = vi.fn().mockResolvedValue(undefined);
    render(
      <ScenarioGridDialog open initial={null} existingNames={[]}
        onGenerate={onGenerate} onClose={() => {}} />,
    );
    const user = userEvent.setup();

    await user.type(screen.getByLabelText('Set name'), 'spot_vol');
    // axis 0 defaults to spot; fill its range -20%..20% step 10% (entered as %)
    await user.clear(screen.getByLabelText('start 0')); await user.type(screen.getByLabelText('start 0'), '-20');
    await user.clear(screen.getByLabelText('stop 0')); await user.type(screen.getByLabelText('stop 0'), '20');
    await user.clear(screen.getByLabelText('step 0')); await user.type(screen.getByLabelText('step 0'), '10');

    // 5 cells for one axis
    expect(screen.getByText(/→\s*5\s*scenarios/i)).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: /generate/i }));
    expect(onGenerate).toHaveBeenCalledTimes(1);
    const body = onGenerate.mock.calls[0][0];
    expect(body.name).toBe('spot_vol');
    expect(body.combine_mode).toBe('cross_product');
    // -20% entered -> -0.2 fraction on the wire
    expect(body.axes[0].start).toBeCloseTo(-0.2);
    expect(body.axes[0].step).toBeCloseTo(0.1);
  });

  it('disables Generate when the cell count exceeds the cap', async () => {
    render(
      <ScenarioGridDialog open initial={null} existingNames={[]}
        onGenerate={vi.fn()} onClose={() => {}} />,
    );
    const user = userEvent.setup();
    await user.type(screen.getByLabelText('Set name'), 'huge');
    await user.clear(screen.getByLabelText('start 0')); await user.type(screen.getByLabelText('start 0'), '0');
    await user.clear(screen.getByLabelText('stop 0')); await user.type(screen.getByLabelText('stop 0'), '100');
    await user.clear(screen.getByLabelText('step 0')); await user.type(screen.getByLabelText('step 0'), '0.1');
    expect(screen.getByRole('button', { name: /generate/i })).toBeDisabled();
  });
});
