import { useState, useEffect } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import api from '../api';
import { useToast } from '../components/Toast';

const STATE_COLORS = {
  running: 'bg-green-500/20 text-green-400',
  pending: 'bg-yellow-500/20 text-yellow-400',
  stopping: 'bg-yellow-500/20 text-yellow-400',
  stopped: 'bg-red-500/20 text-red-400',
  terminated: 'bg-gray-500/20 text-gray-400',
  building: 'bg-blue-500/20 text-blue-400',
  failed: 'bg-red-500/20 text-red-400',
};

const GH_ICON = (cls = 'w-4 h-4') => (
  <svg viewBox="0 0 24 24" className={`${cls} fill-current`}><path d="M12 0C5.374 0 0 5.373 0 12c0 5.302 3.438 9.8 8.207 11.387.599.111.793-.261.793-.577v-2.234c-3.338.726-4.033-1.416-4.033-1.416-.546-1.387-1.333-1.756-1.333-1.756-1.089-.745.083-.729.083-.729 1.205.084 1.839 1.237 1.839 1.237 1.07 1.834 2.807 1.304 3.492.997.107-.775.418-1.305.762-1.604-2.665-.305-5.467-1.334-5.467-5.931 0-1.311.469-2.381 1.236-3.221-.124-.303-.535-1.524.117-3.176 0 0 1.008-.322 3.301 1.23A11.509 11.509 0 0 1 12 5.803c1.02.005 2.047.138 3.006.404 2.291-1.552 3.297-1.23 3.297-1.23.653 1.653.242 2.874.118 3.176.77.84 1.235 1.911 1.235 3.221 0 4.609-2.807 5.624-5.479 5.929.43.372.823 1.102.823 2.222v3.293c0 .319.192.694.801.576C20.566 21.797 24 17.3 24 12c0-6.627-5.373-12-12-12z" /></svg>
);

export default function EC2() {
  const toast = useToast();
  const [searchParams, setSearchParams] = useSearchParams();

  const [instances, setInstances] = useState([]);
  const [loading, setLoading] = useState(true);

  const fetchAll = () => {
    api.get('/ec2/instances').then(r => setInstances(Array.isArray(r.data) ? r.data : [])).catch(() => setInstances([])).finally(() => setLoading(false));
  };

  // Launch modal
  const [showLaunch, setShowLaunch] = useState(false);
  const [form, setForm] = useState({
    name: '', image: 'nginx:alpine', instance_type: 't2.micro',
    port_mappings: '{"80": 0}', environment: '{}',
  });

  const INPUT = "w-full px-3 py-2 bg-gray-700 border border-gray-600 rounded text-white text-sm focus:outline-none focus:border-blue-500";

  const handleDockerLaunch = async (e) => {
    e.preventDefault();
    try {
      await api.post('/ec2/instances', {
        ...form,
        port_mappings: JSON.parse(form.port_mappings || '{}'),
        environment: JSON.parse(form.environment || '{}'),
        vpc_id: null, command: null,
      });
      setShowLaunch(false);
      setForm({ name: '', image: 'nginx:alpine', instance_type: 't2.micro', port_mappings: '{"80": 0}', environment: '{}' });
      fetchAll();
      toast.success('Instance launched!');
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Launch failed');
    }
  };

  const instanceAction = async (id, act) => {
    try {
      if (act === 'terminate') await api.delete(`/ec2/instances/${id}`);
      else await api.post(`/ec2/instances/${id}/${act}`);
      fetchAll();
    } catch (err) { toast.error(err.response?.data?.detail || `Failed to ${act}`); }
  };

  useEffect(() => {
    fetchAll();
    if (searchParams.get('github') === 'connected') {
      toast.success('GitHub connected! Open an instance and go to the Deploy tab.');
      setSearchParams({});
    }
    const interval = setInterval(fetchAll, 5000);
    return () => clearInterval(interval);
  }, []);

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-bold text-white">EC2 Instances</h1>
        <button onClick={() => setShowLaunch(true)}
          className="bg-blue-600 hover:bg-blue-700 text-white px-4 py-2 rounded text-sm">
          Launch Instance
        </button>
      </div>

      {/* Launch Modal */}
      {showLaunch && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
          <div className="bg-gray-800 rounded-lg p-6 w-full max-w-lg mx-4">
            <h2 className="text-lg font-semibold text-white mb-4">Launch Docker Instance</h2>
            <form onSubmit={handleDockerLaunch} className="space-y-3">
              <div>
                <label className="block text-sm text-gray-300 mb-1">Name</label>
                <input value={form.name} onChange={e => setForm({...form, name: e.target.value})} required className={INPUT} placeholder="my-server" />
              </div>
              <div>
                <label className="block text-sm text-gray-300 mb-1">Docker Image</label>
                <input value={form.image} onChange={e => setForm({...form, image: e.target.value})} required className={INPUT} />
              </div>
              <div>
                <label className="block text-sm text-gray-300 mb-1">Instance Type</label>
                <select value={form.instance_type} onChange={e => setForm({...form, instance_type: e.target.value})} className={INPUT}>
                  <option value="t2.nano">t2.nano (0.25 CPU, 128MB)</option>
                  <option value="t2.micro">t2.micro (0.5 CPU, 256MB)</option>
                  <option value="t2.small">t2.small (1.0 CPU, 512MB)</option>
                  <option value="t2.medium">t2.medium (1.0 CPU, 1024MB)</option>
                </select>
              </div>
              <div>
                <label className="block text-sm text-gray-300 mb-1">Port Mappings (JSON)</label>
                <input value={form.port_mappings} onChange={e => setForm({...form, port_mappings: e.target.value})} className={`${INPUT} font-mono`} placeholder='{"80": 0}' />
                <p className="text-xs text-gray-500 mt-1">Use 0 to auto-assign. E.g. {"{"}"80": 0{"}"} exposes port 80.</p>
              </div>
              <div>
                <label className="block text-sm text-gray-300 mb-1">Environment (JSON)</label>
                <input value={form.environment} onChange={e => setForm({...form, environment: e.target.value})} className={`${INPUT} font-mono`} placeholder='{"KEY": "value"}' />
              </div>
              <div className="bg-blue-900/20 border border-blue-700/40 rounded p-3 text-xs text-blue-300">
                After launching, open the instance and use the <strong>Deploy</strong> tab to upload code from GitHub or a ZIP file.
              </div>
              <div className="flex gap-3 pt-2">
                <button type="submit" className="bg-blue-600 hover:bg-blue-700 text-white px-4 py-2 rounded text-sm">Launch</button>
                <button type="button" onClick={() => setShowLaunch(false)} className="bg-gray-600 hover:bg-gray-700 text-white px-4 py-2 rounded text-sm">Cancel</button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* Instance List */}
      {loading ? (
        <div className="text-gray-500 text-center py-8">Loading...</div>
      ) : instances.length === 0 ? (
        <div className="bg-gray-800 rounded-lg p-12 text-center">
          <p className="text-gray-400 mb-2">No instances yet</p>
          <p className="text-gray-500 text-sm">Launch a Docker container, then deploy code from GitHub or upload a ZIP</p>
        </div>
      ) : (
        <div className="bg-gray-800 rounded-lg overflow-hidden">
          <table className="w-full">
            <thead>
              <tr className="border-b border-gray-700">
                <th className="text-left px-4 py-3 text-sm font-medium text-gray-400">Name</th>
                <th className="text-left px-4 py-3 text-sm font-medium text-gray-400">State</th>
                <th className="text-left px-4 py-3 text-sm font-medium text-gray-400">Source</th>
                <th className="text-left px-4 py-3 text-sm font-medium text-gray-400">Type</th>
                <th className="text-left px-4 py-3 text-sm font-medium text-gray-400">Actions</th>
              </tr>
            </thead>
            <tbody>
              {instances.map(item => (
                <tr key={item.id} className="border-b border-gray-700/50 hover:bg-gray-700/30">
                  <td className="px-4 py-3 text-sm">
                    <Link to={`/ec2/${item.id}`} className="text-blue-400 hover:underline">{item.name}</Link>
                  </td>
                  <td className="px-4 py-3 text-sm">
                    <span className={`px-2 py-0.5 rounded text-xs font-medium ${STATE_COLORS[item.state] || 'text-gray-400'}`}>
                      {item.state === 'building' && <span className="inline-block w-2 h-2 bg-blue-400 rounded-full animate-pulse mr-1" />}
                      {item.state}
                    </span>
                  </td>
                  <td className="px-4 py-3 text-sm text-gray-400 text-xs">
                    {item.github_repo ? (
                      <span className="flex items-center gap-1">
                        {GH_ICON('w-3 h-3 text-gray-500')}
                        <span className="font-mono">{item.github_repo}</span>
                      </span>
                    ) : item.project_label ? (
                      <span className="text-purple-300">{item.project_label}</span>
                    ) : (
                      <span className="font-mono">{item.image}</span>
                    )}
                  </td>
                  <td className="px-4 py-3 text-sm">
                    <span className="text-xs bg-gray-700 text-gray-300 px-2 py-0.5 rounded">{item.instance_type}</span>
                  </td>
                  <td className="px-4 py-3 text-sm space-x-2">
                    {item.state === 'stopped' && (
                      <button onClick={() => instanceAction(item.id, 'start')} className="text-green-400 hover:text-green-300 text-xs">Start</button>
                    )}
                    {item.state === 'running' && (
                      <>
                        <button onClick={() => instanceAction(item.id, 'stop')} className="text-yellow-400 hover:text-yellow-300 text-xs">Stop</button>
                        <button onClick={() => instanceAction(item.id, 'reboot')} className="text-blue-400 hover:text-blue-300 text-xs">Reboot</button>
                      </>
                    )}
                    {item.state !== 'terminated' && (
                      <button onClick={() => { if (confirm('Terminate?')) instanceAction(item.id, 'terminate'); }}
                        className="text-red-400 hover:text-red-300 text-xs">Terminate</button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
