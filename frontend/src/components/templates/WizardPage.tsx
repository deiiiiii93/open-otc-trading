import React from 'react';
import { PageScaffold } from './PageScaffold';
import { Stepper, type Step } from '../Stepper';
import './WizardPage.css';

type Props = {
  title: string;
  chips?: string[];
  actions?: React.ReactNode;
  feedback?: React.ReactNode;
  steps?: Step[];
  tabs?: React.ReactNode;
  children: React.ReactNode;
  footer?: React.ReactNode;
};

export function WizardPage({ title, chips, actions, feedback, steps, tabs, children, footer }: Props) {
  return (
    <PageScaffold title={title} chips={chips} actions={actions} feedback={feedback}>
      {steps && <Stepper steps={steps} />}
      {tabs}
      <div className="wl-wizard__body">{children}</div>
      {footer && <div className="wl-wizard__footer">{footer}</div>}
    </PageScaffold>
  );
}
