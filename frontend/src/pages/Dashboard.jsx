import { useState, useEffect } from 'react';
import api from '../api';

function StatCard({ title, value, subtitle, color = 'blue', loading = false }) {
  const colors = {
    blue:   'border-blue-500 text-blue-400',
    green:  'border-green-500 text-green-400',
    yellow: 'border-yellow-500 text-yellow-400',
    red:    'border-red-500 text-red-400',
    purple: 'border-purple-500 text-purple-400',
    orange: 'border-orange-500 text-orange-400',
  };

  return (
    <div className={`bg-gray-800 rounded-lg p-6 border-l-4 ${colors[color]}`}>
      <p className="text-sm text-gray-400">{title}</p>
      <p className={`text-2xl font-bold mt-1 ${loading ? 'text-gray-600 animate-pulse' : 'text-white'}`}>
        {loading ? '...' : value}
      </p>
      {subtitle && <p className="text-xs text-gray-500 mt-1">{subtitle}</p>}
    </div>
  );
}

function MiniBar({ label, value, max = 100, color = 'blue' }) {
  const colors = {
    blue:   'bg-blue-500',
    green:  'bg-green-500',
    yellow: 'bg-yellow-500',
    red:    'bg-red-500',
  };
  const pct = Math.min(100, Math.max(0, value));
  const barColor = pct > 85 ? 'red' : pct > 65 ? 'yellow' : color;

  return (
    <div>
      <div className="flex justify-between text-xs mb-1">
        <span className="text-gray-400">{label}</span>
        <span className="text-white font-medium">{value != null ? `${value.toFixed(1)}%` : '--'}</span>
      </div>
      <div className="h-2 bg-gray-700 rounded-full overflow-hidden">
        <div className={`h-full rounded-full transition-all ${colors[barColor]}`} style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}

export default function Dashboard() {
  const [counts, setCounts] = useState(null);
  const [metrics, setMetrics] = useState(null);
  const [alarms, setAlarms] = useState([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const fetchAll = async () => {
      try {
        const [instances, buckets, functions, domains, metricsRes, alarmsRes] = await Promise.allSettled([
          api.get('/ec2/instances'),
          api.get('/s3/buckets'),
          api.get('/lambda/functions'),
          api.get('/route53/domains'),
          api.get('/cloudwatch/metrics/latest'),
          api.get('/cloudwatch/alarms'),
        ]);

        const inst = instances.status === 'fulfilled' ? instances.value.data : [];
        const bkts = buckets.status === 'fulfilled' ? buckets.value.data : [];
        const fns  = functions.status === 'fulfilled' ? functions.value.data : [];
        const doms = domains.status === 'fulfilled' ? domains.value.data : [];

        setCounts({
          instances:        inst.length,
          instancesRunning: inst.filter(i => i.state === 'running').length,
          buckets:          bkts.length,
          functions:        fns.length,
          domains:          doms.length,
          domainsActive:    doms.filter(d => d.state === 'active').length,
        });

        if (metricsRes.status === 'fulfilled') {
          setMetrics(metricsRes.value.data);
        }
        if (alarmsRes.status === 'fulfilled') {
          setAlarms(alarmsRes.value.data.filter(a => a.state === 'ALARM'));
        }
      } catch (_) {
        // silent
      } finally {
        setLoading(false);
      }
    };

    fetchAll();
    const interval = setInterval(fetchAll, 30000); // refresh every 30s
    return () => clearInterval(interval);
  }, []);

  const alarmCount = alarms.length;

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-bold text-white">Dashboard</h1>
        {alarmCount > 0 && (
          <div className="flex items-center gap-2 bg-red-900/40 border border-red-600 text-red-300 px-4 py-2 rounded-lg text-sm">
            <span className="w-2 h-2 bg-red-400 rounded-full animate-pulse inline-block" />
            {alarmCount} alarm{alarmCount > 1 ? 's' : ''} firing
          </div>
        )}
      </div>

      {/* Service Counts */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-6">
        <StatCard
          title="EC2 Instances"
          value={counts ? `${counts.instancesRunning} / ${counts.instances}` : '--'}
          subtitle="Running / Total"
          color="blue"
          loading={loading}
        />
        <StatCard
          title="S3 Buckets"
          value={counts?.buckets ?? '--'}
          subtitle="Total"
          color="green"
          loading={loading}
        />
        <StatCard
          title="Lambda Functions"
          value={counts?.functions ?? '--'}
          subtitle="Deployed"
          color="purple"
          loading={loading}
        />
        <StatCard
          title="Domains"
          value={counts ? `${counts.domainsActive} / ${counts.domains}` : '--'}
          subtitle="Active / Total"
          color="yellow"
          loading={loading}
        />
      </div>

      {/* System Metrics */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 mb-6">
        <div className="bg-gray-800 rounded-lg p-6">
          <h2 className="text-sm font-semibold text-gray-300 mb-4 uppercase tracking-wide">System Resources</h2>
          <div className="space-y-4">
            <MiniBar label="CPU Usage" value={metrics?.cpu_percent} color="blue" />
            <MiniBar label="Memory Usage" value={metrics?.memory_percent} color="green" />
            <MiniBar label="Disk Usage" value={metrics?.disk_percent} color="yellow" />
          </div>
          {metrics && (
            <div className="mt-4 grid grid-cols-2 gap-2 text-xs text-gray-500">
              <span>RAM used: {metrics.memory_used_mb?.toFixed(0)} MB</span>
              <span>Disk used: {metrics.disk_used_gb?.toFixed(1)} GB</span>
            </div>
          )}
        </div>

        {/* Alarms & Status */}
        <div className="bg-gray-800 rounded-lg p-6">
          <h2 className="text-sm font-semibold text-gray-300 mb-4 uppercase tracking-wide">Alarm Status</h2>
          {alarms.length === 0 ? (
            <div className="flex items-center gap-2 text-green-400">
              <span className="w-2 h-2 bg-green-400 rounded-full inline-block" />
              <span className="text-sm">All systems nominal</span>
            </div>
          ) : (
            <div className="space-y-2">
              {alarms.slice(0, 5).map(a => (
                <div key={a.id} className="flex items-center justify-between bg-red-900/20 border border-red-800 rounded p-3">
                  <div>
                    <p className="text-sm text-white">{a.name}</p>
                    <p className="text-xs text-gray-400">{a.metric_name} {a.comparison} {a.threshold}</p>
                  </div>
                  <span className="text-xs text-red-400 font-medium">ALARM</span>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      {/* Quick Links */}
      <div className="bg-gray-800 rounded-lg p-6">
        <h2 className="text-sm font-semibold text-gray-300 mb-4 uppercase tracking-wide">Quick Actions</h2>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          {[
            { label: 'Launch Instance', href: '/ec2', color: 'bg-blue-600 hover:bg-blue-700' },
            { label: 'Create Bucket', href: '/s3', color: 'bg-green-600 hover:bg-green-700' },
            { label: 'Deploy Function', href: '/lambda', color: 'bg-purple-600 hover:bg-purple-700' },
            { label: 'Add Domain', href: '/route53', color: 'bg-yellow-600 hover:bg-yellow-700' },
          ].map(({ label, href, color }) => (
            <a key={href} href={href} className={`${color} text-white text-sm text-center py-2.5 px-4 rounded transition-colors`}>
              {label}
            </a>
          ))}
        </div>
      </div>
    </div>
  );
}
