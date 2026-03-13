import { useState, useEffect } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import api from '../api';
import { useToast } from '../components/Toast';

const STATE_COLORS = {
  running: 'bg-green-500/20 text-green-400',
  stopped: 'bg-red-500/20 text-red-400',
  failed: 'bg-red-500/20 text-red-400',
};

export default function EC2Detail() {
  const { id } = useParams();
  const navigate = useNavigate();
  const toast = useToast();

  const [instance, setInstance] = useState(null);
  const [logs, setLogs] = useState('');
  const [tab, setTab] = useState('overview');

  const [copied, setCopied] = useState(null);
  const [tunnelLoading, setTunnelLoading] = useState(false);
  const [tunnelError, setTunnelError] = useState(null);

  const fetchInstance = () =>
    api.get(`/ec2/instances/${id}`)
      .then(res => setInstance(res.data))
      .catch(() => navigate('/ec2'));

  useEffect(() => {
    fetchInstance();
  }, [id]);

  useEffect(() => {
    if (tab === 'logs' && instance) {
      const fetchLogs = () => {
        api.get(`/ec2/instances/${id}/logs`)
          .then(res => setLogs(res.data.logs || ''))
          .catch(() => {});
      };
      fetchLogs();
      const interval = setInterval(fetchLogs, 5000);
      return () => clearInterval(interval);
    }
  }, [tab, id]);

  const copyLink = (url) => {
    navigator.clipboard.writeText(url);
    setCopied(url);
    setTimeout(() => setCopied(null), 2000);
  };

  const startTunnel = async () => {
    setTunnelLoading(true);
    setTunnelError(null);
    try {
      await api.post(`/ec2/instances/${id}/tunnel/start`);
      await fetchInstance();
    } catch (err) {
      setTunnelError(err.response?.data?.detail || 'Failed to start tunnel');
    } finally { setTunnelLoading(false); }
  };

  const stopTunnel = async () => {
    setTunnelLoading(true);
    try {
      await api.delete(`/ec2/instances/${id}/tunnel/stop`);
      await fetchInstance();
    } catch (err) {
      setTunnelError(err.response?.data?.detail || 'Failed to stop tunnel');
    } finally { setTunnelLoading(false); }
  };

  if (!instance) return <div className="text-gray-400 p-8">Loading...</div>;

  const publicUrls = Object.entries(instance.public_urls || {});
  const allTabs = ['overview', 'website', 'logs'];
  const tabLabels = { overview: 'Overview', website: 'Website', logs: 'Runtime Log' };

  return (
    <div>
      <div className="flex items-center gap-4 mb-6">
        <button onClick={() => navigate('/ec2')} className="text-gray-400 hover:text-white">&larr; Back</button>
        <h1 className="text-2xl font-bold text-white">{instance.name}</h1>
        <span className={`px-2 py-1 rounded text-xs font-medium ${STATE_COLORS[instance.state] || 'bg-gray-500/20 text-gray-400'}`}>
          {instance.state}
        </span>
      </div>

      <div className="flex gap-1 mb-4 overflow-x-auto">
        {allTabs.map(t => (
          <button key={t} onClick={() => setTab(t)}
            className={`px-4 py-2 text-sm rounded-t transition-colors whitespace-nowrap ${
              tab === t ? 'bg-gray-800 text-white' : 'bg-gray-900 text-gray-400 hover:text-white'
            }`}>
            {tabLabels[t]}
          </button>
        ))}
      </div>

      <div className="bg-gray-800 rounded-lg p-6">

        {/* ── Overview ── */}
        {tab === 'overview' && (
          <div className="grid grid-cols-2 gap-4">
            {Object.entries({
              'Instance ID': instance.id,
              'Image': instance.image,
              'Type': instance.instance_type,
              'State': instance.state,
              'Private IP': instance.private_ip || '--',
              'CPU Limit': `${instance.cpu_limit} cores`,
              'Memory Limit': `${instance.memory_limit} MB`,
              'VPC ID': instance.vpc_id || '--',
              'Created': new Date(instance.created_at).toLocaleString(),
            }).map(([k, v]) => (
              <div key={k}>
                <p className="text-xs text-gray-500">{k}</p>
                <p className="text-sm text-white font-mono">{v}</p>
              </div>
            ))}
          </div>
        )}

        {/* ── Website ── */}
        {tab === 'website' && (
          <div className="space-y-6">
            <div>
              <h3 className="text-sm font-semibold text-gray-300 mb-3 uppercase tracking-wide">Shareable Links</h3>
              {publicUrls.length === 0 ? (
                <p className="text-gray-500 text-sm">No ports exposed. Add port mappings when launching an instance.</p>
              ) : (
                <div className="space-y-2">
                  {publicUrls.map(([port, url]) => (
                    <div key={port} className="flex items-center gap-3 bg-gray-900 rounded-lg p-3">
                      <div className="flex-1">
                        <p className="text-xs text-gray-500 mb-0.5">Port {port} (direct)</p>
                        <a href={url} target="_blank" rel="noreferrer"
                          className="text-blue-400 hover:text-blue-300 text-sm font-mono break-all">{url}</a>
                      </div>
                      <button onClick={() => copyLink(url)}
                        className={`px-3 py-1.5 rounded text-xs whitespace-nowrap ${
                          copied === url ? 'bg-green-600 text-white' : 'bg-gray-700 hover:bg-gray-600 text-gray-300'
                        }`}>
                        {copied === url ? 'Copied!' : 'Copy'}
                      </button>
                      <a href={url} target="_blank" rel="noreferrer"
                        className="px-3 py-1.5 bg-blue-600 hover:bg-blue-700 text-white rounded text-xs whitespace-nowrap">Open</a>
                    </div>
                  ))}
                </div>
              )}
            </div>

            {/* Tunnel */}
            <div>
              <h3 className="text-sm font-semibold text-gray-300 mb-3 uppercase tracking-wide">Cloudflare Tunnel</h3>
              {instance.state !== 'running' ? (
                <p className="text-yellow-400 text-sm">Instance must be running to start a tunnel.</p>
              ) : instance.tunnel_url ? (
                <div className="space-y-3">
                  <div className="flex items-center gap-3 bg-gray-900 rounded-lg p-3 border border-orange-500/30">
                    <div className="w-2 h-2 bg-orange-400 rounded-full animate-pulse" />
                    <div className="flex-1 min-w-0">
                      <p className="text-xs text-gray-500 mb-0.5">Public URL</p>
                      <a href={instance.tunnel_url} target="_blank" rel="noreferrer"
                        className="text-orange-400 hover:text-orange-300 text-sm font-mono break-all">{instance.tunnel_url}</a>
                    </div>
                    <button onClick={() => copyLink(instance.tunnel_url)}
                      className={`px-3 py-1.5 rounded text-xs whitespace-nowrap ${
                        copied === instance.tunnel_url ? 'bg-green-600 text-white' : 'bg-gray-700 hover:bg-gray-600 text-gray-300'
                      }`}>
                      {copied === instance.tunnel_url ? 'Copied!' : 'Copy'}
                    </button>
                    <a href={instance.tunnel_url} target="_blank" rel="noreferrer"
                      className="px-3 py-1.5 bg-orange-600 hover:bg-orange-700 text-white rounded text-xs whitespace-nowrap">Open</a>
                  </div>
                  <div className="flex gap-2">
                    <button onClick={startTunnel} disabled={tunnelLoading}
                      className="px-4 py-2 bg-orange-600 hover:bg-orange-500 text-white rounded text-sm disabled:opacity-50">
                      {tunnelLoading ? 'Generating...' : 'New URL'}
                    </button>
                    <button onClick={stopTunnel} disabled={tunnelLoading}
                      className="px-4 py-2 bg-red-700 hover:bg-red-600 text-white rounded text-sm disabled:opacity-50">Stop Tunnel</button>
                  </div>
                </div>
              ) : (
                <div className="space-y-3">
                  <p className="text-gray-400 text-sm">
                    Generate a free <span className="text-orange-400 font-medium">trycloudflare.com</span> URL.
                  </p>
                  <button onClick={startTunnel} disabled={tunnelLoading}
                    className="px-4 py-2 bg-orange-600 hover:bg-orange-500 text-white rounded text-sm font-medium disabled:opacity-50">
                    {tunnelLoading ? (
                      <span className="flex items-center gap-2">
                        <span className="w-3 h-3 border border-white border-t-transparent rounded-full animate-spin" />
                        Starting (~15s)...
                      </span>
                    ) : 'Start Cloudflare Tunnel'}
                  </button>
                  {tunnelError && (
                    <p className="text-red-400 text-sm bg-red-900/20 border border-red-700 rounded p-2">{tunnelError}</p>
                  )}
                </div>
              )}
            </div>
          </div>
        )}

        {/* ── Runtime Log ── */}
        {tab === 'logs' && (
          <div>
            <h3 className="text-sm font-semibold text-gray-300 mb-3 uppercase tracking-wide">Runtime Log</h3>
            <pre className="text-sm text-green-400 bg-gray-900 p-4 rounded max-h-96 overflow-auto font-mono whitespace-pre-wrap">
              {logs || 'No logs available'}
            </pre>
          </div>
        )}
      </div>
    </div>
  );
}
