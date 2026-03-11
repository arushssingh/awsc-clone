import { useState, useEffect, useRef } from 'react';
import { useParams, Link } from 'react-router-dom';
import api from '../api';
import { useToast } from '../components/Toast';

const STATUS_COLORS = {
  uploading: 'bg-yellow-500/20 text-yellow-400',
  queued: 'bg-yellow-500/20 text-yellow-400',
  building: 'bg-blue-500/20 text-blue-400',
  running: 'bg-green-500/20 text-green-400',
  failed: 'bg-red-500/20 text-red-400',
  stopped: 'bg-gray-500/20 text-gray-400',
};

export default function DeployDetail() {
  const { id } = useParams();
  const toast = useToast();
  const [dep, setDep] = useState(null);
  const [logs, setLogs] = useState({ build_log: '', runtime_log: '' });
  const [tab, setTab] = useState('overview');
  const [actionLoading, setActionLoading] = useState('');
  const [tunnelLoading, setTunnelLoading] = useState(false);
  const [files, setFiles] = useState([]);
  const [filesLoading, setFilesLoading] = useState(false);
  const logEndRef = useRef(null);

  const fetchDep = () => api.get(`/deploy/projects/${id}`).then(r => setDep(r.data)).catch(() => {});
  const fetchLogs = () => api.get(`/deploy/projects/${id}/logs`).then(r => setLogs(r.data)).catch(() => {});
  const fetchFiles = () => {
    setFilesLoading(true);
    api.get(`/deploy/projects/${id}/files`).then(r => setFiles(r.data)).catch(() => {}).finally(() => setFilesLoading(false));
  };

  useEffect(() => {
    fetchDep();
    fetchLogs();
    fetchFiles();
    const interval = setInterval(() => { fetchDep(); fetchLogs(); }, 4000);
    return () => clearInterval(interval);
  }, [id]);

  useEffect(() => {
    if (logEndRef.current) logEndRef.current.scrollIntoView({ behavior: 'smooth' });
  }, [logs]);

  const startTunnel = async () => {
    setTunnelLoading(true);
    try {
      await api.post(`/deploy/projects/${id}/tunnel/start`);
      toast.success('Tunnel started');
      fetchDep();
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Failed to start tunnel');
    } finally {
      setTunnelLoading(false);
    }
  };

  const stopTunnel = async () => {
    setTunnelLoading(true);
    try {
      await api.delete(`/deploy/projects/${id}/tunnel/stop`);
      toast.success('Tunnel stopped');
      fetchDep();
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Failed to stop tunnel');
    } finally {
      setTunnelLoading(false);
    }
  };

  const doAction = async (action, label) => {
    setActionLoading(action);
    try {
      await api.post(`/deploy/projects/${id}/${action}`);
      toast.success(`${label} initiated`);
      fetchDep();
    } catch (err) {
      toast.error(err.response?.data?.detail || `Failed to ${action}`);
    } finally {
      setActionLoading('');
    }
  };

  if (!dep) return <div className="text-gray-500 text-center py-8">Loading...</div>;

  const isBuilding = dep.status === 'building' || dep.status === 'uploading' || dep.status === 'queued';

  return (
    <div>
      <div className="flex items-center gap-2 text-sm text-gray-400 mb-4">
        <Link to="/ec2" className="hover:text-white">EC2</Link>
        <span>/</span>
        <span className="text-white">{dep.name}</span>
      </div>

      <div className="flex items-center justify-between mb-6">
        <div className="flex items-center gap-3">
          <h1 className="text-2xl font-bold text-white">{dep.name}</h1>
          <span className={`px-2 py-0.5 rounded text-xs font-medium ${STATUS_COLORS[dep.status] || 'bg-gray-500/20 text-gray-400'}`}>
            {dep.status === 'building' && <span className="inline-block w-2 h-2 bg-blue-400 rounded-full animate-pulse mr-1" />}
            {dep.status}
          </span>
          {dep.project_label && (
            <span className="text-xs text-gray-500 bg-gray-700 px-2 py-0.5 rounded">{dep.project_label}</span>
          )}
        </div>
        <div className="flex items-center gap-2">
          {dep.status === 'running' && (
            <button onClick={() => doAction('stop', 'Stop')} disabled={!!actionLoading}
              className="bg-yellow-600 hover:bg-yellow-700 text-white px-3 py-1.5 rounded text-sm disabled:opacity-50">
              {actionLoading === 'stop' ? 'Stopping...' : 'Stop'}
            </button>
          )}
          {dep.status === 'stopped' && (
            <button onClick={() => doAction('start', 'Start')} disabled={!!actionLoading}
              className="bg-green-600 hover:bg-green-700 text-white px-3 py-1.5 rounded text-sm disabled:opacity-50">
              {actionLoading === 'start' ? 'Starting...' : 'Start'}
            </button>
          )}
          {(dep.status === 'running' || dep.status === 'stopped' || dep.status === 'failed') && (
            <button onClick={() => doAction('redeploy', 'Redeploy')} disabled={!!actionLoading}
              className="bg-blue-600 hover:bg-blue-700 text-white px-3 py-1.5 rounded text-sm disabled:opacity-50">
              {actionLoading === 'redeploy' ? 'Redeploying...' : 'Redeploy'}
            </button>
          )}
        </div>
      </div>

      {/* URL Banner */}
      {dep.url && dep.status === 'running' && (
        <div className="bg-green-900/30 border border-green-700 rounded-lg p-3 mb-6 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <span className="text-green-400 text-sm font-medium">Live at:</span>
            <a href={dep.url} target="_blank" rel="noreferrer" className="text-green-300 hover:text-green-200 font-mono text-sm">{dep.url}</a>
          </div>
          <button onClick={() => { navigator.clipboard.writeText(dep.url); toast.success('URL copied'); }}
            className="text-green-400 hover:text-green-300 text-xs bg-green-900/50 px-2 py-1 rounded">Copy</button>
        </div>
      )}

      {/* Cloudflare Tunnel */}
      {dep.status === 'running' && (
        <div className="bg-orange-900/20 border border-orange-700/50 rounded-lg p-4 mb-6">
          <div className="flex items-center justify-between mb-2">
            <div>
              <p className="text-sm font-medium text-orange-300">Cloudflare Tunnel</p>
              <p className="text-xs text-gray-500">Share publicly via trycloudflare.com</p>
            </div>
            <div className="flex items-center gap-2">
              {dep.tunnel_url && (
                <button onClick={startTunnel} disabled={tunnelLoading}
                  className="text-xs bg-orange-800/50 hover:bg-orange-700/50 text-orange-300 px-2 py-1 rounded disabled:opacity-50">
                  New URL
                </button>
              )}
              {dep.tunnel_url ? (
                <button onClick={stopTunnel} disabled={tunnelLoading}
                  className="text-xs bg-red-900/50 hover:bg-red-800/50 text-red-400 px-3 py-1 rounded disabled:opacity-50">
                  {tunnelLoading ? 'Stopping...' : 'Stop Tunnel'}
                </button>
              ) : (
                <button onClick={startTunnel} disabled={tunnelLoading}
                  className="text-xs bg-orange-600 hover:bg-orange-700 text-white px-3 py-1 rounded disabled:opacity-50">
                  {tunnelLoading ? 'Starting...' : 'Start Tunnel'}
                </button>
              )}
            </div>
          </div>
          {dep.tunnel_url && (
            <div className="flex items-center gap-2 mt-2 bg-orange-900/30 rounded px-3 py-2">
              <a href={dep.tunnel_url} target="_blank" rel="noreferrer"
                className="text-orange-300 hover:text-orange-200 font-mono text-sm flex-1 truncate">{dep.tunnel_url}</a>
              <button onClick={() => { navigator.clipboard.writeText(dep.tunnel_url); toast.success('Copied'); }}
                className="text-orange-400 hover:text-orange-300 text-xs shrink-0">Copy</button>
            </div>
          )}
        </div>
      )}

      {/* Tabs */}
      <div className="flex gap-1 mb-4 border-b border-gray-700">
        {['overview', 'files', 'build-log', 'runtime-log'].map(t => (
          <button key={t} onClick={() => { setTab(t); if (t === 'files') fetchFiles(); }}
            className={`px-4 py-2 text-sm transition-colors ${tab === t ? 'text-blue-400 border-b-2 border-blue-400' : 'text-gray-400 hover:text-white'}`}>
            {t === 'overview' ? 'Overview' : t === 'files' ? 'Files' : t === 'build-log' ? 'Build Log' : 'Runtime Log'}
          </button>
        ))}
      </div>

      {/* Overview Tab */}
      {tab === 'overview' && (
        <>
          {dep.github_repo && (
            <div className="bg-gray-800/60 border border-gray-700 rounded-lg p-4 mb-4">
              <p className="text-xs text-gray-500 mb-2 font-medium uppercase tracking-wide">GitHub Source</p>
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-3">
                  <svg viewBox="0 0 24 24" className="w-5 h-5 fill-gray-300 shrink-0"><path d="M12 0C5.374 0 0 5.373 0 12c0 5.302 3.438 9.8 8.207 11.387.599.111.793-.261.793-.577v-2.234c-3.338.726-4.033-1.416-4.033-1.416-.546-1.387-1.333-1.756-1.333-1.756-1.089-.745.083-.729.083-.729 1.205.084 1.839 1.237 1.839 1.237 1.07 1.834 2.807 1.304 3.492.997.107-.775.418-1.305.762-1.604-2.665-.305-5.467-1.334-5.467-5.931 0-1.311.469-2.381 1.236-3.221-.124-.303-.535-1.524.117-3.176 0 0 1.008-.322 3.301 1.23A11.509 11.509 0 0 1 12 5.803c1.02.005 2.047.138 3.006.404 2.291-1.552 3.297-1.23 3.297-1.23.653 1.653.242 2.874.118 3.176.77.84 1.235 1.911 1.235 3.221 0 4.609-2.807 5.624-5.479 5.929.43.372.823 1.102.823 2.222v3.293c0 .319.192.694.801.576C20.566 21.797 24 17.3 24 12c0-6.627-5.373-12-12-12z"/></svg>
                  <div>
                    <a href={`https://github.com/${dep.github_repo}`} target="_blank" rel="noreferrer"
                      className="text-sm text-white hover:text-blue-400 font-mono">{dep.github_repo}</a>
                    <span className="text-gray-500 mx-2">@</span>
                    <span className="text-sm text-gray-300">{dep.github_branch}</span>
                  </div>
                </div>
                <div className="flex items-center gap-2">
                  {dep.github_webhook_id ? (
                    <span className="text-xs text-green-400 bg-green-900/30 px-2 py-0.5 rounded">Auto-deploy on push</span>
                  ) : (
                    <span className="text-xs text-gray-500 bg-gray-700 px-2 py-0.5 rounded">Webhooks not registered</span>
                  )}
                </div>
              </div>
            </div>
          )}
          <div className="grid grid-cols-2 gap-4">
          {[
            ['Status', dep.status],
            ['Project Type', dep.project_label || dep.project_type || '—'],
            ['Port', dep.port || '—'],
            ['Created', new Date(dep.created_at).toLocaleString()],
            ['Updated', new Date(dep.updated_at).toLocaleString()],
            ['Container ID', dep.docker_container_id ? dep.docker_container_id.slice(0, 12) : '—'],
          ].map(([label, value]) => (
            <div key={label} className="bg-gray-800 rounded-lg p-4">
              <p className="text-xs text-gray-500 mb-1">{label}</p>
              <p className="text-sm text-white font-mono">{value}</p>
            </div>
          ))}
          </div>
        </>
      )}

      {/* Files Tab */}
      {tab === 'files' && (
        <div className="bg-gray-900 rounded-lg overflow-hidden">
          {filesLoading ? (
            <div className="text-gray-500 text-center py-8 text-sm">Loading files...</div>
          ) : files.length === 0 ? (
            <div className="text-gray-500 text-center py-8 text-sm">No project files found.</div>
          ) : (
            <table className="w-full">
              <thead>
                <tr className="border-b border-gray-700">
                  <th className="text-left px-4 py-2 text-xs font-medium text-gray-500">File</th>
                  <th className="text-right px-4 py-2 text-xs font-medium text-gray-500 w-24">Size</th>
                </tr>
              </thead>
              <tbody>
                {files.map(f => (
                  <tr key={f.path} className="border-b border-gray-800 hover:bg-gray-800/50">
                    <td className="px-4 py-1.5 text-sm text-gray-300 font-mono">{f.path}</td>
                    <td className="px-4 py-1.5 text-xs text-gray-500 text-right">
                      {f.size < 1024 ? `${f.size} B` : f.size < 1048576 ? `${(f.size / 1024).toFixed(1)} KB` : `${(f.size / 1048576).toFixed(1)} MB`}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          {files.length >= 500 && (
            <p className="text-xs text-gray-600 text-center py-2">Showing first 500 files</p>
          )}
        </div>
      )}

      {/* Build Log Tab */}
      {tab === 'build-log' && (
        <div className="bg-gray-900 rounded-lg p-4 max-h-[600px] overflow-y-auto font-mono text-xs text-gray-300">
          {isBuilding && <div className="text-blue-400 mb-2 animate-pulse">Building...</div>}
          <pre className="whitespace-pre-wrap">{logs.build_log || 'No build log available.'}</pre>
          <div ref={logEndRef} />
        </div>
      )}

      {/* Runtime Log Tab */}
      {tab === 'runtime-log' && (
        <div className="bg-gray-900 rounded-lg p-4 max-h-[600px] overflow-y-auto font-mono text-xs text-gray-300">
          <pre className="whitespace-pre-wrap">{logs.runtime_log || 'No runtime logs available.'}</pre>
          <div ref={logEndRef} />
        </div>
      )}
    </div>
  );
}
