import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { ModelMaintenanceLive } from './ModelMaintenance.live';
import * as client from '../api/client';
import type { AgentRegistry } from '../types';

const registry: AgentRegistry = {
  default: { channel: 'zenmux', model: 'deepseek/deepseek-v4-flash' },
  channels: [
    {
      name: 'zenmux',
      label: 'ZenMux',
      type: 'zenmux',
      base_url: 'https://zenmux.example/v1',
      anthropic_base_url: 'https://zenmux.example/anthropic',
      api_key_env: 'ZENMUX_API_KEY',
      healthy: true,
      models: [
        {
          id: 'deepseek/deepseek-v4-flash',
          provider: 'openai',
          label: 'DeepSeek V4 Flash',
          description: null,
          tags: ['fast'],
          protocol: null,
        },
      ],
    },
    {
      name: 'direct',
      label: 'Direct DeepSeek',
      type: 'openai_compatible',
      base_url: 'https://api.deepseek.com',
      anthropic_base_url: null,
      api_key_env: 'DEEPSEEK_API_KEY',
      healthy: false,
      models: [
        {
          id: 'deepseek-v4-pro',
          provider: 'openai',
          label: 'DeepSeek V4 Pro',
          description: null,
          tags: [],
          protocol: null,
        },
      ],
    },
  ],
};

beforeEach(() => {
  vi.restoreAllMocks();
  vi.spyOn(client, 'getAgentRegistry').mockResolvedValue(registry);
});

describe('ModelMaintenanceLive', () => {
  it('renders each channel label and model label from the registry', async () => {
    render(<ModelMaintenanceLive />);
    await waitFor(() => expect(screen.getByText('ZenMux')).toBeInTheDocument());
    expect(screen.getByText('Direct DeepSeek')).toBeInTheDocument();
    expect(screen.getByText('DeepSeek V4 Flash')).toBeInTheDocument();
    expect(screen.getByText('DeepSeek V4 Pro')).toBeInTheDocument();
  });

  it('shows the Anthropic base URL field for a zenmux channel and hides it for an openai_compatible one', async () => {
    render(<ModelMaintenanceLive />);
    await waitFor(() => screen.getByText('ZenMux'));

    fireEvent.click(screen.getByText('ZenMux'));
    expect(screen.getByLabelText('Anthropic base URL')).toBeInTheDocument();

    fireEvent.click(screen.getByText('Direct DeepSeek'));
    expect(screen.queryByLabelText('Anthropic base URL')).not.toBeInTheDocument();
  });
});
