import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ArenaLive } from './Arena.live';

// Mock the arenaApi module
vi.mock('../lib/arenaApi', () => ({
  listArenaRuns: vi.fn(),
  getArenaRun: vi.fn(),
  getArenaLeaderboard: vi.fn(),
  getMatchTranscript: vi.fn(),
  listArenaModels: vi.fn(),
  createArenaRun: vi.fn(),
}));

import * as arenaApi from '../lib/arenaApi';

const mockModels = [
  { slug: 'claude-sonnet', zenmux_name: 'claude-sonnet', display_name: 'Claude Sonnet' },
  { slug: 'gpt-4o', zenmux_name: 'gpt-4o', display_name: 'GPT-4o' },
];

const mockRuns = [
  {
    id: 'run-abc123',
    status: 'completed',
    created_at: '2026-06-24T10:00:00Z',
    workflow_ids: ['workflow-a'],
    model_ids: ['claude-sonnet'],
  },
];

const mockLeaderboard = [
  { model_id: 'claude-sonnet', avg_total: 0.85, avg_objective: 0.9, matches: 3 },
  { model_id: 'gpt-4o', avg_total: 0.72, avg_objective: 0.75, matches: 3 },
];

const mockMatches = [
  {
    id: 'match-001',
    workflow_id: 'workflow-a',
    model_id: 'claude-sonnet',
    status: 'completed',
    objective_score: 0.9,
    judged_score: 0.8,
    total_score: 0.85,
    judge_missing: false,
    transcript_path: 'artifacts/arena/run-abc123/claude-sonnet/workflow-a/transcript.json',
  },
];

const mockTranscript = { messages: [{ role: 'user', content: 'Run workflow' }] };

function setupMocks() {
  vi.mocked(arenaApi.listArenaRuns).mockResolvedValue({ runs: mockRuns, total: 1 });
  vi.mocked(arenaApi.getArenaLeaderboard).mockResolvedValue({ rows: mockLeaderboard });
  vi.mocked(arenaApi.listArenaModels).mockResolvedValue({ models: mockModels });
  vi.mocked(arenaApi.getArenaRun).mockResolvedValue({
    run: mockRuns[0],
    matches: mockMatches,
  });
  vi.mocked(arenaApi.getMatchTranscript).mockResolvedValue(mockTranscript);
}

afterEach(() => { vi.clearAllMocks(); });

describe('ArenaLive', () => {
  it('renders leaderboard rows with display names and scores from mocked data', async () => {
    setupMocks();
    render(<ArenaLive />);

    // Leaderboard table should appear with display names
    expect(await screen.findByText('Claude Sonnet')).toBeInTheDocument();
    expect(await screen.findByText('GPT-4o')).toBeInTheDocument();

    // Scores should be formatted
    expect(screen.getByText('0.850')).toBeInTheDocument();
    expect(screen.getByText('0.900')).toBeInTheDocument();
  });

  it('renders the runs list', async () => {
    setupMocks();
    render(<ArenaLive />);

    // Run ID (first 8 chars) should appear in the run picker
    expect(await screen.findByText('run-abc1')).toBeInTheDocument();
  });

  it('clicking a run loads run detail and shows match grid', async () => {
    setupMocks();
    render(<ArenaLive />);

    const runButton = await screen.findByText('run-abc1');
    await userEvent.click(runButton);

    // Match cell should appear with workflow and model info
    expect(await screen.findByText('workflow-a')).toBeInTheDocument();
    expect(await screen.findByText(/Total:/)).toBeInTheDocument();
    expect(arenaApi.getArenaRun).toHaveBeenCalledWith('run-abc123');
  });

  it('clicking a match fetches the transcript and renders transcript content', async () => {
    setupMocks();
    render(<ArenaLive />);

    // First, click the run
    const runButton = await screen.findByText('run-abc1');
    await userEvent.click(runButton);

    // Wait for match cell
    const matchButton = await screen.findByText('workflow-a');
    await userEvent.click(matchButton);

    // Transcript should be loaded and shown
    await waitFor(() => {
      expect(arenaApi.getMatchTranscript).toHaveBeenCalledWith('match-001');
    });

    // The transcript JSON content should appear
    expect(await screen.findByText(/Run workflow/)).toBeInTheDocument();
  });

  it('shows empty state when no leaderboard data', async () => {
    vi.mocked(arenaApi.listArenaRuns).mockResolvedValue({ runs: [], total: 0 });
    vi.mocked(arenaApi.getArenaLeaderboard).mockResolvedValue({ rows: [] });
    vi.mocked(arenaApi.listArenaModels).mockResolvedValue({ models: [] });

    render(<ArenaLive />);

    expect(
      await screen.findByText(/No leaderboard data yet/),
    ).toBeInTheDocument();
    expect(screen.getByText(/No arena runs yet/)).toBeInTheDocument();
  });

  it('shows error when API fails', async () => {
    vi.mocked(arenaApi.listArenaRuns).mockRejectedValue(new Error('network error'));
    vi.mocked(arenaApi.getArenaLeaderboard).mockRejectedValue(new Error('network error'));
    vi.mocked(arenaApi.listArenaModels).mockRejectedValue(new Error('network error'));

    render(<ArenaLive />);

    expect(await screen.findByRole('alert')).toBeInTheDocument();
  });
});
