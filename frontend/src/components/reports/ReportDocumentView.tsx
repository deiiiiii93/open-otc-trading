import { Badge } from '../Badge';
import { Chip } from '../Chip';
import { BlockRenderer } from './BlockRenderer';
import type { ReportDocument } from '../../types';
import './ReportDocumentView.css';

type Props = { document: ReportDocument };

export function ReportDocumentView({ document }: Props) {
  const { template, provenance, params, sections, generated_at: generatedAt } = document;
  const coverage = provenance.coverage as { priced?: number; total?: number } | undefined;

  return (
    <article className="wl-report">
      <header className="wl-report__head">
        <div className="wl-report__title-row">
          <h2 className="wl-report__title">{template.title}</h2>
          <Badge variant="info">{template.persona}</Badge>
        </div>
        <div className="wl-report__chips">
          <Chip>portfolio #{params.portfolio_id}</Chip>
          {provenance.valuation_as_of && (
            <Chip>as of {String(provenance.valuation_as_of).slice(0, 10)}</Chip>
          )}
          {params.compare_to_run_id != null && <Chip>vs run #{params.compare_to_run_id}</Chip>}
          {/* Coverage rides in the header on purpose: a P&L number over an
              incompletely-priced book is not the book's P&L, and the reader
              must see that without opening a drawer. */}
          {coverage?.total != null && (
            <Chip>
              {coverage.priced ?? 0} / {coverage.total} priced
            </Chip>
          )}
          <Chip>generated {generatedAt.slice(0, 16).replace('T', ' ')}</Chip>
        </div>
      </header>

      {sections.map((section) => (
        <section key={section.id} className="wl-report__section">
          {section.title && <h3 className="wl-report__section-title">{section.title}</h3>}

          <div className="wl-report__blocks">
            {section.blocks.map((block) => (
              <div key={block.key} className="wl-report__block">
                <BlockRenderer block={block} />
              </div>
            ))}
          </div>

          {section.narrative && <p className="wl-report__narrative">{section.narrative}</p>}

          {section.narrative_error && (
            <p className="wl-report__warn" role="note" data-testid="narrative-error">
              This section&rsquo;s commentary could not be written: {section.narrative_error}
            </p>
          )}

          {section.grounding.flags.length > 0 && (
            <p className="wl-report__warn" role="note" data-testid="grounding-warning">
              Ungrounded {section.grounding.flags.length === 1 ? 'figure' : 'figures'} in the
              commentary: {section.grounding.flags.map((flag) => flag.token).join(', ')}. These do
              not appear in this section&rsquo;s data and need review.
            </p>
          )}
        </section>
      ))}

      <footer className="wl-report__provenance">
        <h4 className="wl-report__provenance-title">Provenance</h4>
        <dl className="wl-report__provenance-list">
          {provenance.risk_run_id != null && (
            <div>
              <dt>Risk run</dt>
              <dd>risk run #{provenance.risk_run_id}</dd>
            </div>
          )}
          {provenance.position_set_hash && (
            <div>
              <dt>Position set</dt>
              <dd>{String(provenance.position_set_hash)}</dd>
            </div>
          )}
          <div>
            <dt>Template</dt>
            <dd>
              {template.slug} v{template.version}
            </dd>
          </div>
          <div>
            <dt>Spec</dt>
            <dd>{template.spec_sha256}</dd>
          </div>
        </dl>
      </footer>
    </article>
  );
}
