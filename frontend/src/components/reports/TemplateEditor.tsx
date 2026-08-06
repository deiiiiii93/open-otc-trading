import { useEffect, useState } from 'react';
import { Badge } from '../Badge';
import { Button } from '../Button';
import type { ReportTemplate } from '../../types';
import './TemplateEditor.css';

type Props = {
  template: ReportTemplate;
  onSave: (slug: string, specYaml: string) => Promise<void> | void;
  onValidate: (specYaml: string) => Promise<{ ok: boolean; errors: string[] }>;
};

export function TemplateEditor({ template, onSave, onValidate }: Props) {
  const [spec, setSpec] = useState(template.spec ?? '');
  const [errors, setErrors] = useState<string[]>([]);
  const [status, setStatus] = useState<'idle' | 'valid' | 'invalid' | 'saved'>('idle');
  const isSeed = template.source === 'seed';

  useEffect(() => {
    setSpec(template.spec ?? '');
    setErrors([]);
    setStatus('idle');
  }, [template.slug, template.spec]);

  async function validate(): Promise<boolean> {
    const result = await onValidate(spec);
    setErrors(result.errors);
    setStatus(result.ok ? 'valid' : 'invalid');
    return result.ok;
  }

  async function save(): Promise<void> {
    // Validate before every save: the server refuses an invalid spec anyway,
    // and showing the errors here avoids a pointless round-trip.
    if (!(await validate())) return;
    await onSave(template.slug, spec);
    setStatus('saved');
  }

  return (
    <div className="wl-tpl-editor">
      <header className="wl-tpl-editor__head">
        <span className="wl-tpl-editor__slug">{template.slug}</span>
        <Badge variant={isSeed ? 'info' : 'ink'}>{template.source}</Badge>
        <span className="wl-tpl-editor__version">v{template.version}</span>
      </header>

      <textarea
        className="wl-tpl-editor__area"
        value={spec}
        spellCheck={false}
        aria-label={`Spec for ${template.slug}`}
        onChange={(event) => {
          setSpec(event.target.value);
          setStatus('idle');
        }}
      />

      <div className="wl-tpl-editor__actions">
        <Button variant="ghost" onClick={validate}>
          Validate
        </Button>
        <Button onClick={save} disabled={isSeed}>
          Save
        </Button>
        {status === 'valid' && <span className="wl-tpl-editor__ok">Spec is valid.</span>}
        {status === 'saved' && <span className="wl-tpl-editor__ok">Saved.</span>}
      </div>

      {isSeed && (
        <p className="wl-tpl-editor__note">
          Seeded templates are read-only. Copy this spec into a new slug to customise it.
        </p>
      )}

      {errors.length > 0 && (
        <ul className="wl-tpl-editor__errors" data-testid="template-errors">
          {errors.map((message) => (
            <li key={message}>{message}</li>
          ))}
        </ul>
      )}
    </div>
  );
}
