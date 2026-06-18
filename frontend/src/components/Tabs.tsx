import * as RadixTabs from '@radix-ui/react-tabs';
import './Tabs.css';

export const Tabs = (props: RadixTabs.TabsProps) => (
  <RadixTabs.Root className="wl-tabs" {...props} />
);

export const TabsList = (props: RadixTabs.TabsListProps) => (
  <RadixTabs.List className="wl-tabs__list" {...props} />
);

export const TabsTrigger = (props: RadixTabs.TabsTriggerProps) => (
  <RadixTabs.Trigger className="wl-tabs__trigger" {...props} />
);

export const TabsContent = (props: RadixTabs.TabsContentProps) => (
  <RadixTabs.Content className="wl-tabs__content" {...props} />
);
