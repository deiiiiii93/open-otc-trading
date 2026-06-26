import type { ReactNode } from 'react';
import { Button } from '../components/Button';
import './WorkflowBuilder.css';

type Props = {
  chat: ReactNode;
  draftScript: string;
  onSave: () => void;
  saving: boolean;
};

export function WorkflowBuilder({ chat, draftScript, onSave, saving }: Props) {
  return (
    <div className="wl-wf-builder">
      <div className="wl-wf-builder__chat">{chat}</div>
      <div className="wl-wf-builder__preview">
        <h3 className="wl-wf-builder__heading">Drafted workflow</h3>
        <pre className="wl-wf-builder__script">
          {draftScript || '// Ask the assistant to build a workflow — its draft appears here.'}
        </pre>
        <Button variant="primary" onClick={onSave} disabled={!draftScript || saving}>
          {saving ? 'Saving…' : 'Save workflow'}
        </Button>
      </div>
    </div>
  );
}
