import { PanelLeftClose, PanelLeftOpen } from 'lucide-react';
import type { Route } from '../types';
import './Sidebar.css';

export type SidebarItem = {
  route: Route;
  label: string;
};

type Props = {
  items: SidebarItem[];
  active: Route;
  onNavigate: (route: Route) => void;
  collapsed?: boolean;
  onToggleCollapsed?: () => void;
};

export function Sidebar({
  items,
  active,
  onNavigate,
  collapsed = false,
  onToggleCollapsed,
}: Props) {
  return (
    <aside className={`wl-sidebar${collapsed ? ' wl-sidebar--collapsed' : ''}`} aria-label="Primary navigation">
      <div className="wl-sidebar__brand">
        <button
          type="button"
          className="wl-sidebar__brand-toggle"
          onClick={onToggleCollapsed}
          aria-label={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
          title={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
          aria-pressed={collapsed}
        >
          <span className="wl-sidebar__brand-mark" aria-hidden="true">OTC</span>
          {!collapsed && (
            <span className="wl-sidebar__brand-text" aria-hidden="true">
              <strong>Open OTC</strong>
              <span>AI trading platform</span>
            </span>
          )}
          <span className="wl-sidebar__toggle-icon" aria-hidden="true">
            {collapsed ? <PanelLeftOpen size={16} /> : <PanelLeftClose size={16} />}
          </span>
        </button>
      </div>
      {!collapsed && (
        <nav className="wl-sidebar__nav">
          {items.map((item) => (
            <button
              key={item.route}
              type="button"
              className={`wl-sidebar__nav-item ${item.route === active ? 'wl-sidebar__nav-item--active' : ''}`.trim()}
              aria-current={item.route === active ? 'page' : undefined}
              onClick={() => onNavigate(item.route)}
            >
              {item.label}
            </button>
          ))}
        </nav>
      )}
    </aside>
  );
}
