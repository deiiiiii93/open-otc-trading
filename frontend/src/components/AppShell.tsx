import React, { useState } from 'react';
import type { Route } from '../types';
import { Sidebar, type SidebarItem } from './Sidebar';
import './AppShell.css';

type Props = {
  active: Route;
  onNavigate: (route: Route) => void;
  items: SidebarItem[];
  children: React.ReactNode;
  toolbar?: React.ReactNode;
};

export function AppShell({ active, onNavigate, items, children, toolbar }: Props) {
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);

  return (
    <div className={`wl-shell${sidebarCollapsed ? ' wl-shell--sidebar-collapsed' : ''}`}>
      <Sidebar
        items={items}
        active={active}
        onNavigate={onNavigate}
        collapsed={sidebarCollapsed}
        onToggleCollapsed={() => setSidebarCollapsed((value) => !value)}
      />
      <main className="wl-shell__main">
        {toolbar && <div className="wl-shell__toolbar">{toolbar}</div>}
        <div className="wl-shell__content">{children}</div>
      </main>
    </div>
  );
}
