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

const KEY_TYPE_OPTIONS = [
  { value: 'custom', label: 'Custom' },
  { value: 'supabase_url', label: 'Supabase URL', defaultKey: 'VITE_SUPABASE_URL' },
  { value: 'supabase_anon_key', label: 'Supabase Anon Key', defaultKey: 'VITE_SUPABASE_ANON_KEY' },
  { value: 'ai_api_key', label: 'AI API Key', defaultKey: '' },
];

const TAB_LABELS = {
  'overview': 'Overview',
  'env-vars': 'Env Vars',
  'files': 'Files',
  'build-log': 'Build Log',
  'runtime-log': 'Runtime Log',
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

  // Env vars state
  const [envVars, setEnvVars] = useState([]);
  const [envLoading, setEnvLoading] = useState(false);
  const [envSaving, setEnvSaving] = useState(false);
  const [validating, setValidating] = useState({});
  const [validationResults, setValidationResults] = useState({});
  const [showValues, setShowValues] = useState({});

  const fetchDep = () => api.get(`/deploy/projects/${id}`).then(r => setDep(r.data)).catch(() => {});
  const fetchLogs = () => api.get(`/deploy/projects/${id}/logs`).then(r => setLogs(r.data)).catch(() => {});
  const fetchFiles = () => {
    setFilesLoading(true);
    api.get(`/deploy/projects/${id}/files`).then(r => setFiles(Array.isArray(r.data) ? r.data : [])).catch(() => setFiles([])).finally(() => setFilesLoading(false));
  };
  const fetchEnvVars = () => {
    setEnvLoading(true);
    api.get(`/deploy/projects/${id}/env`)
      .then(r => {
        const vars = (r.data.env_vars || []).map(ev => ({
          ...ev,
          value: '',
          placeholder: ev.has_value ? ev.display_value : '',
        }));
        setEnvVars(vars);
      })
      .catch(() => setEnvVars([]))
      .finally(() => setEnvLoading(false));
  };

  useEffect(() => {
    fetchDep();
    fetchLogs();
    fetchFiles();
    fetchEnvVars();
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

  // ── Env var handlers (immutable updates) ──

  const addEnvVar = () => {
    setEnvVars(prev => [...prev, { key: '', value: '', key_type: 'custom' }]);
  };

  const updateEnvVar = (index, field, newValue) => {
    setEnvVars(prev => prev.map((item, i) => {
      if (i !== index) return item;
      const updated = { ...item, [field]: newValue };
      // Auto-fill key name when selecting a preset type
      if (field === 'key_type') {
        const preset = KEY_TYPE_OPTIONS.find(o => o.value === newValue);
        if (preset && preset.defaultKey) {
          return { ...updated, key: preset.defaultKey };
        }
      }
      return updated;
    }));
    // Clear validation result when value changes
    if (field === 'value' || field === 'key') {
      setValidationResults(prev => {
        const next = { ...prev };
        delete next[index];
        return next;
      });
    }
  };

  const removeEnvVar = (index) => {
    setEnvVars(prev => prev.filter((_, i) => i !== index));
    setValidationResults(prev => {
      const next = { ...prev };
      delete next[index];
      return next;
    });
  };

  const saveEnvVars = async () => {
    setEnvSaving(true);
    try {
      await api.put(`/deploy/projects/${id}/env`, {
        env_vars: envVars.map(ev => ({
          key: ev.key.trim(),
          value: ev.value,
          key_type: ev.key_type,
        })),
      });
      toast.success('Environment variables saved. Redeploy to apply changes.');
      fetchEnvVars();
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Failed to save');
    } finally {
      setEnvSaving(false);
    }
  };

  const validateEnvVar = async (index) => {
    const ev = envVars[index];
    if (!ev.value && !ev.has_value) {
      toast.error('Enter a value first');
      return;
    }
    setValidating(prev => ({ ...prev, [index]: true }));
    try {
      const payload = {
        key: ev.key,
        value: ev.value,
        key_type: ev.key_type,
      };
      // For anon key validation, pass the Supabase URL from the current session
      if (ev.key_type === 'supabase_anon_key') {
        const urlVar = envVars.find(v => v.key === 'VITE_SUPABASE_URL');
        if (urlVar && urlVar.value) {
          payload.supabase_url = urlVar.value;
        }
      }
      const res = await api.post(`/deploy/projects/${id}/env/validate`, payload);
      setValidationResults(prev => ({ ...prev, [index]: res.data }));
      if (res.data.valid) {
        toast.success(res.data.message);
      } else {
        toast.error(res.data.message);
      }
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Validation failed');
    } finally {
      setValidating(prev => ({ ...prev, [index]: false }));
    }
  };

  const toggleShowValue = (index) => {
    setShowValues(prev => ({ ...prev, [index]: !prev[index] }));
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
        {Object.keys(TAB_LABELS).map(t => (
          <button key={t} onClick={() => { setTab(t); if (t === 'files') fetchFiles(); if (t === 'env-vars') fetchEnvVars(); }}
            className={`px-4 py-2 text-sm transition-colors ${tab === t ? 'text-blue-400 border-b-2 border-blue-400' : 'text-gray-400 hover:text-white'}`}>
            {TAB_LABELS[t]}
            {t === 'env-vars' && dep.env_vars_count > 0 && (
              <span className="ml-1.5 text-xs bg-gray-700 text-gray-300 px-1.5 py-0.5 rounded-full">{dep.env_vars_count}</span>
            )}
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
            ['Project Type', dep.project_label || dep.project_type || '\u2014'],
            ['Port', dep.port || '\u2014'],
            ['Created', new Date(dep.created_at).toLocaleString()],
            ['Updated', new Date(dep.updated_at).toLocaleString()],
            ['Env Vars', dep.env_vars_count || '0'],
          ].map(([label, value]) => (
            <div key={label} className="bg-gray-800 rounded-lg p-4">
              <p className="text-xs text-gray-500 mb-1">{label}</p>
              <p className="text-sm text-white font-mono">{value}</p>
            </div>
          ))}
          </div>
        </>
      )}

      {/* Env Vars Tab */}
      {tab === 'env-vars' && (
        <div className="space-y-4">
          <div className="flex items-center justify-between">
            <div>
              <h3 className="text-sm font-medium text-white">Environment Variables</h3>
              <p className="text-xs text-gray-500 mt-0.5">Set API keys, URLs, and secrets. Redeploy after saving to apply.</p>
            </div>
            <button onClick={addEnvVar}
              className="bg-blue-600 hover:bg-blue-700 text-white px-3 py-1.5 rounded text-sm transition-colors">
              Add Variable
            </button>
          </div>

          {envLoading ? (
            <div className="text-gray-500 text-center py-8 text-sm">Loading...</div>
          ) : envVars.length === 0 ? (
            <div className="bg-gray-800 rounded-lg p-8 text-center">
              <p className="text-gray-400 mb-2">No environment variables</p>
              <p className="text-gray-500 text-sm mb-4">Add variables like API keys and Supabase credentials</p>
              <button onClick={addEnvVar}
                className="bg-blue-600 hover:bg-blue-700 text-white px-4 py-2 rounded text-sm">
                Add First Variable
              </button>
            </div>
          ) : (
            <>
              <div className="space-y-3">
                {envVars.map((ev, idx) => {
                  const vResult = validationResults[idx];
                  const isValidating = validating[idx];
                  const isPresetKey = ev.key_type !== 'custom' && KEY_TYPE_OPTIONS.find(o => o.value === ev.key_type)?.defaultKey;

                  return (
                    <div key={idx} className="bg-gray-800 rounded-lg p-4 space-y-3">
                      <div className="flex items-start gap-3">
                        {/* Key Type */}
                        <div className="w-44 shrink-0">
                          <label className="block text-xs text-gray-500 mb-1">Type</label>
                          <select
                            value={ev.key_type}
                            onChange={e => updateEnvVar(idx, 'key_type', e.target.value)}
                            className="w-full px-2 py-1.5 bg-gray-700 border border-gray-600 rounded text-white text-sm focus:outline-none focus:border-blue-500"
                          >
                            {KEY_TYPE_OPTIONS.map(opt => (
                              <option key={opt.value} value={opt.value}>{opt.label}</option>
                            ))}
                          </select>
                        </div>

                        {/* Key Name */}
                        <div className="flex-1 min-w-0">
                          <label className="block text-xs text-gray-500 mb-1">Key</label>
                          <input
                            value={ev.key}
                            onChange={e => updateEnvVar(idx, 'key', e.target.value)}
                            readOnly={!!isPresetKey}
                            placeholder="VARIABLE_NAME"
                            className={`w-full px-2 py-1.5 bg-gray-700 border border-gray-600 rounded text-white text-sm font-mono focus:outline-none focus:border-blue-500 ${isPresetKey ? 'text-gray-400 cursor-not-allowed' : ''}`}
                          />
                        </div>

                        {/* Value */}
                        <div className="flex-1 min-w-0">
                          <label className="block text-xs text-gray-500 mb-1">Value</label>
                          <div className="relative">
                            <input
                              type={showValues[idx] ? 'text' : 'password'}
                              value={ev.value}
                              onChange={e => updateEnvVar(idx, 'value', e.target.value)}
                              placeholder={ev.placeholder || 'Enter value...'}
                              className="w-full px-2 py-1.5 pr-8 bg-gray-700 border border-gray-600 rounded text-white text-sm font-mono focus:outline-none focus:border-blue-500"
                            />
                            <button
                              type="button"
                              onClick={() => toggleShowValue(idx)}
                              className="absolute right-2 top-1/2 -translate-y-1/2 text-gray-400 hover:text-white text-xs"
                            >
                              {showValues[idx] ? 'Hide' : 'Show'}
                            </button>
                          </div>
                        </div>

                        {/* Actions */}
                        <div className="flex items-end gap-1.5 shrink-0 pb-0.5">
                          {ev.key_type !== 'custom' && (
                            <button
                              onClick={() => validateEnvVar(idx)}
                              disabled={isValidating || !ev.value}
                              className="px-2.5 py-1.5 bg-purple-600 hover:bg-purple-700 text-white rounded text-xs disabled:opacity-50 transition-colors"
                            >
                              {isValidating ? 'Testing...' : 'Validate'}
                            </button>
                          )}
                          <button
                            onClick={() => removeEnvVar(idx)}
                            className="px-2 py-1.5 bg-red-900/50 hover:bg-red-800/50 text-red-400 rounded text-xs transition-colors"
                          >
                            Remove
                          </button>
                        </div>
                      </div>

                      {/* Validation Result */}
                      {vResult && (
                        <div className={`flex items-center gap-2 text-xs px-3 py-2 rounded ${
                          vResult.valid
                            ? 'bg-green-900/30 text-green-400 border border-green-800/50'
                            : 'bg-red-900/30 text-red-400 border border-red-800/50'
                        }`}>
                          <span>{vResult.valid ? '\u2713' : '\u2717'}</span>
                          <span>{vResult.message}</span>
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>

              {/* Save Button */}
              <div className="flex items-center justify-between pt-2">
                <p className="text-xs text-gray-500">
                  Changes require a redeploy to take effect.
                  {dep.project_label && (dep.project_label.includes('Vite') || dep.project_label.includes('React') || dep.project_label.includes('Vue'))
                    ? ' VITE_ prefixed vars are injected at build time.'
                    : ' Vars are injected as runtime environment variables.'
                  }
                </p>
                <button
                  onClick={saveEnvVars}
                  disabled={envSaving}
                  className="bg-green-600 hover:bg-green-700 text-white px-4 py-2 rounded text-sm disabled:opacity-50 transition-colors"
                >
                  {envSaving ? 'Saving...' : 'Save Variables'}
                </button>
              </div>
            </>
          )}
        </div>
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
