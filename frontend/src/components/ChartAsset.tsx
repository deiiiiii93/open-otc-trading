import {
  BarChart,
  Bar,
  LineChart,
  Line,
  AreaChart,
  Area,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
} from 'recharts';
import './ChartAsset.css';

type ChartData = {
  chart_type: 'bar' | 'line' | 'area';
  x_key: string;
  y_key: string;
  series: Record<string, number | string>[];
};

type Props = {
  data: ChartData;
  title: string;
};

export function ChartAsset({ data, title }: Props) {
  const { chart_type, x_key, y_key, series } = data;

  const chart = (() => {
    const common = (
      <>
        <CartesianGrid strokeDasharray="3 3" stroke="var(--hairline)" />
        <XAxis dataKey={x_key} tick={{ fill: 'var(--ink-2)', fontSize: 10, fontFamily: 'var(--font-numeric)' }} />
        <YAxis tick={{ fill: 'var(--ink-2)', fontSize: 10, fontFamily: 'var(--font-numeric)' }} />
        <Tooltip
          contentStyle={{
            background: 'var(--paper)',
            border: '1px solid var(--ink)',
            fontFamily: 'var(--font-numeric)',
            fontSize: 12,
          }}
        />
      </>
    );

    if (chart_type === 'bar') {
      return (
        <BarChart data={series}>
          {common}
          <Bar dataKey={y_key} fill="var(--ink)" />
        </BarChart>
      );
    }
    if (chart_type === 'line') {
      return (
        <LineChart data={series}>
          {common}
          <Line type="monotone" dataKey={y_key} stroke="var(--ink)" strokeWidth={2} dot={false} />
        </LineChart>
      );
    }
    return (
      <AreaChart data={series}>
        {common}
        <Area type="monotone" dataKey={y_key} stroke="var(--ink)" fill="var(--ink-2)" />
      </AreaChart>
    );
  })();

  return (
    <div className="wl-chart-asset">
      <div className="wl-chart-asset__title">{title}</div>
      <div className="wl-chart-asset__canvas">
        <ResponsiveContainer width="100%" height="100%">
          {chart}
        </ResponsiveContainer>
      </div>
    </div>
  );
}
