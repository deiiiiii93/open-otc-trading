import './Stepper.css';

export type Step = { label: string; status: 'done' | 'active' | 'todo' };

type Props = { steps: Step[]; className?: string };

export function Stepper({ steps, className = '' }: Props) {
  return (
    <ol className={`wl-stepper ${className}`.trim()}>
      {steps.map((s, i) => (
        <li
          key={`${s.label}-${i}`}
          className={`wl-stepper__step wl-stepper__step--${s.status}`}
          aria-current={s.status === 'active' ? 'step' : undefined}
        >
          <span className="wl-stepper__dot" aria-hidden="true" />
          <span className="wl-stepper__label">{s.label}</span>
        </li>
      ))}
    </ol>
  );
}
