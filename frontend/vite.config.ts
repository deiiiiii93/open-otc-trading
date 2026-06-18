import { defineConfig, loadEnv } from 'vite';
import react from '@vitejs/plugin-react';
import path from 'node:path';

const repoRoot = path.resolve(__dirname, '..');

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, repoRoot, '');
  const apiTarget = process.env.VITE_API_TARGET || env.VITE_API_TARGET || 'http://localhost:8000';

  return {
    envDir: repoRoot,
    plugins: [react()],
    server: {
      port: 5173,
      proxy: {
        '/api': apiTarget
      }
    }
  };
});
