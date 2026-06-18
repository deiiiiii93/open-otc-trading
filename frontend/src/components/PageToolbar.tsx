import React, { useId } from 'react';
import { Search } from 'lucide-react';
import './PageToolbar.css';

type ToolbarProps = {
  children: React.ReactNode;
  className?: string;
  role?: string;
  'aria-label'?: string;
};

export function PageToolbar({ children, className = '', ...rest }: ToolbarProps) {
  return (
    <div className={`wl-page-toolbar ${className}`.trim()} {...rest}>
      {children}
    </div>
  );
}

export function PageToolbarSpacer() {
  return <div className="wl-page-toolbar__spacer" />;
}

type SearchProps = {
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  'aria-label'?: string;
  id?: string;
};

export function PageToolbarSearch({
  value,
  onChange,
  placeholder = 'Search…',
  'aria-label': ariaLabel = 'Search',
  id: providedId,
}: SearchProps) {
  const generatedId = useId();
  const id = providedId ?? generatedId;
  return (
    <label className="wl-page-toolbar__search" htmlFor={id}>
      <Search size={14} aria-hidden="true" />
      <input
        id={id}
        type="search"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        aria-label={ariaLabel}
      />
    </label>
  );
}
