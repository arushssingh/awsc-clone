import { useState, useEffect } from 'react';
import { Line } from 'react-chartjs-2';
import {
  Chart as ChartJS, CategoryScale, LinearScale, PointElement,
  LineElement, Title, Tooltip, Filler,
} from 'chart.js';
import api from '../api';

ChartJS.register(CategoryScale, LinearScale, PointElement, LineElement, Title, Tooltip, Filler);

const METRICS = ['cpu_percent', 'memory_percent', 'disk_percent'];
const METRIC_LABELS = { cpu_percent: 'CPU Usage (%)', memory_percent: 'Memory Usage (%)', disk_percent: 'Disk Usage (%)' };
const METRIC_COLORS = { cpu_percent: '#3b82f6', memory_percent: '#10b981', disk_percent: '#f59e0b' };

const TIME_RANGES = [
  { label: '1h', hours: 1 },
  { label: '6h', hours: 6 },
  { label: '24h', hours: 24 },
  { label: '7d', hours: 168 },
];

function MetricChart({ name, timeRange }) {
  const [data, setData] = useState(null);

  useEffect(() => {
    const end = new Date().toISOString();
    const start = new Date(Date.now() - timeRange * 3600 * 1000).toISOString();
    api.get('/cloudwatch/metrics', { params: { name, start, end, interval: Math.max(60, timeRange * 60) } })
      .then(res => {
        const dp = res.data.datapoints || [];
        setData({
          labels: dp.map(p => new Date(p.timestamp).toLocaleTimeString()),
          datasets: [{
            label: METRIC_LABELS[name],
            data: dp.map(p => p.value),
            borderColor: METRIC_COLORS[name],
            backgroundColor: METRIC_COLORS[name] + '20',
            fill: true,
            tension: 0.3,
            pointRadius: 0,
          }],
        });
      })
      .catch(() => setData(null));
  }, [name, timeRange]);

  const options = {
    responsive: true,
    plugins: { tooltip: { mode: 'index' }, legend: { display: false } },
    scales: {
      y: { min: 0, max: 100, grid: { color: '#374151' }, ticks: { color: '#9ca3af' } },
      x: { grid: { display: false }, ticks: { color: '#9ca3af', maxTicksLimit: 8 } },
    },
  };

  return (
    <div className="bg-gray-800 rounded-lg p-4">
      <h3 className="text-sm font-medium text-gray-300 mb-3">{METRIC_LABELS[name]}</h3>
      {data ? (
        <Line data={data} options={options} height={80} />
      ) : (
        <div className="h-32 flex items-center justify-center text-gray-500 text-sm">No data available</div>
      )}
    </div>
  );
}

export default function CloudWatch() {
  const [timeRange, setTimeRange] = useState(1);
  const [alarms, setAlarms] = useState([]);
  const [latest, setLatest] = useState(null);

  useEffect(() => {
    api.get('/cloudwatch/metrics/latest').then(res => setLatest(res.data)).catch(() => {});
    api.get('/cloudwatch/alarms').then(res => setAlarms(res.data)).catch(() => {});
  }, []);

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-bold text-white">CloudWatch</h1>
        <div className="flex gap-1">
          {TIME_RANGES.map(tr => (
            <button key={tr.label} onClick={() => setTimeRange(tr.hours)}
              className={`px-3 py-1 text-sm rounded transition-colors ${
                timeRange === tr.hours ? 'bg-blue-600 text-white' : 'bg-gray-700 text-gray-400 hover:text-white'
              }`}>{tr.label}</button>
          ))}
        </div>
      </div>

      {/* Current values */}
      {latest && (
        <div className="grid grid-cols-3 gap-4 mb-6">
          {Object.entries(latest).map(([k, v]) => (
            <div key={k} className="bg-gray-800 rounded-lg p-4 text-center">
              <p className="text-xs text-gray-500">{METRIC_LABELS[k] || k}</p>
              <p className="text-2xl font-bold text-white">{typeof v === 'number' ? v.toFixed(1) : v}%</p>
            </div>
          ))}
        </div>
      )}

      {/* Charts */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4 mb-8">
        {METRICS.map(m => <MetricChart key={m} name={m} timeRange={timeRange} />)}
      </div>

      {/* Alarms */}
      <div className="bg-gray-800 rounded-lg p-6">
        <h2 className="text-lg font-semibold text-white mb-4">Alarms</h2>
        {alarms.length === 0 ? (
          <p className="text-gray-400 text-sm">No alarms configured.</p>
        ) : (
          <div className="space-y-2">
            {alarms.map(a => (
              <div key={a.id} className="flex items-center justify-between bg-gray-900 rounded p-3">
                <div>
                  <span className="text-white text-sm">{a.name}</span>
                  <span className="text-gray-500 text-xs ml-2">{a.metric_name} {a.comparison} {a.threshold}</span>
                </div>
                <span className={`px-2 py-1 rounded text-xs font-medium ${
                  a.state === 'OK' ? 'bg-green-500/20 text-green-400' : 'bg-red-500/20 text-red-400'
                }`}>{a.state}</span>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
