import type { AgentAsset } from '../types';
import { AssetCard } from './AssetCard';
import { Empty } from './Empty';
import './AssetsPane.css';

type Props = {
  assets: AgentAsset[];
};

export function AssetsPane({ assets }: Props) {
  return (
    <section className="wl-assets-pane">
      <header className="wl-assets-pane__head">
        <span className="wl-assets-pane__title">ASSETS</span>
        <span className="wl-assets-pane__count">{assets.length}</span>
      </header>
      <div className="wl-assets-pane__body">
        {assets.length === 0 ? (
          <Empty message="No assets yet — agent outputs will dock here." symbol="◌" />
        ) : (
          <ul className="wl-assets-pane__list">
            {assets.map((asset) => (
              <li key={asset.id} className="wl-assets-pane__item">
                <AssetCard asset={asset} />
              </li>
            ))}
          </ul>
        )}
      </div>
    </section>
  );
}
