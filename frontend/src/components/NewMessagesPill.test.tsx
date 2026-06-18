import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { NewMessagesPill } from './NewMessagesPill';

describe('NewMessagesPill', () => {
  it('shows live label when streaming and no count', () => {
    render(<NewMessagesPill streaming count={0} onClick={() => {}} />);
    expect(screen.getByRole('button')).toHaveTextContent(/live/i);
  });

  it('shows count label when not streaming and count > 0', () => {
    render(<NewMessagesPill streaming={false} count={3} onClick={() => {}} />);
    expect(screen.getByRole('button')).toHaveTextContent(/3/);
  });

  it('renders nothing when neither streaming nor count > 0', () => {
    const { container } = render(
      <NewMessagesPill streaming={false} count={0} onClick={() => {}} />,
    );
    expect(container.firstChild).toBeNull();
  });

  it('calls onClick when clicked', async () => {
    const onClick = vi.fn();
    render(<NewMessagesPill streaming count={0} onClick={onClick} />);
    await userEvent.click(screen.getByRole('button'));
    expect(onClick).toHaveBeenCalledTimes(1);
  });
});
