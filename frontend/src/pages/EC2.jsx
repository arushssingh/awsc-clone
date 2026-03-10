import { useState, useEffect } from 'react';
import { Link } from 'react-router-dom';
import api from '../api';

const STATE_COLORS = {
  running: 'bg-green-500/20 text-green-400',
  pending: 'bg-yellow-500/20 text-yellow-400',
  stopping: 'bg-yellow-500/20 text-yellow-400',
  stopped: 'bg-red-500/20 text-red-400',
  terminated: 'bg-gray-500/20 text-gray-400',
};

export default function EC2() {
  const [instances, setInstances] = useState([]);
  const [showLaunch, setShowLaunch] = useState(false);
  const [loading, setLoading] = useState(true);
  const [form, setForm] = useState({
    name: '', image: 'nginx:alpine', instance_type: 't2.micro',
    vpc_id: '', port_mappings: '{"80": 0}', environment: '{}', command: '',
  });

  const fetchInstances = () => {
    api.get('/ec2/instances')
      .then(res => setInstances(res.data))
      .catch(() => {})
      .finally(() => setLoading(false));
  };

  useEffect(() => { fetchInstances(); }, []);

  const launch = async (e) => {
    e.preventDefault();
    try {
      const body = {
        ...form,
        port_mappings: JSON.parse(form.port_mappings || '{}'),
        environment: JSON.parse(form.environment || '{}'),
        vpc_id: form.vpc_id || null,
        command: form.command || null,
      };
      await api.post('/ec2/instances', body);
      setShowLaunch(false);
      fetchInstances();
    } catch (err) {
      alert(err.response?.data?.detail || 'Failed to launch');
    }
  };

  const action = async (id, act) => {
    try {
      if (act === 'terminate') {
        await api.delete(`/ec2/instances/${id}`);
      } else {
        await api.post(`/ec2/instances/${id}/${act}`);
      }
      fetchInstances();
    } catch (err) {
      alert(err.response?.data?.detail || `Failed to ${act}`);
    }
  };

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-bold text-white">EC2 Instances</h1>
        <button
          onClick={() => setShowLaunch(true)}
          className="bg-blue-600 hover:bg-blue-700 text-white px-4 py-2 rounded text-sm transition-colors"
        >
          Launch Instance
        </button>
      </div>

      {/* Launch Modal */}
      {showLaunch && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
          <div className="bg-gray-800 rounded-lg p-6 w-full max-w-lg mx-4">
            <h2 className="text-lg font-semibold text-white mb-4">Launch Instance</h2>
            <form onSubmit={launch} className="space-y-3">
              <div>
                <label className="block text-sm text-gray-300 mb-1">Name</label>
                <input value={form.name} onChange={e => setForm({...form, name: e.target.value})} required
                  className="w-full px-3 py-2 bg-gray-700 border border-gray-600 rounded text-white text-sm focus:outline-none focus:border-blue-500" />
              </div>
              <div>
                <label className="block text-sm text-gray-300 mb-1">Image</label>
                <input value={form.image} onChange={e => setForm({...form, image: e.target.value})} required
                  className="w-full px-3 py-2 bg-gray-700 border border-gray-600 rounded text-white text-sm focus:outline-none focus:border-blue-500" />
              </div>
              <div>
                <label className="block text-sm text-gray-300 mb-1">Instance Type</label>
                <select value={form.instance_type} onChange={e => setForm({...form, instance_type: e.target.value})}
                  className="w-full px-3 py-2 bg-gray-700 border border-gray-600 rounded text-white text-sm focus:outline-none focus:border-blue-500">
                  <option value="t2.nano">t2.nano (0.25 CPU, 128MB)</option>
                  <option value="t2.micro">t2.micro (0.5 CPU, 256MB)</option>
                  <option value="t2.small">t2.small (1.0 CPU, 512MB)</option>
                  <option value="t2.medium">t2.medium (1.0 CPU, 1024MB)</option>
                </select>
              </div>
              <div>
                <label className="block text-sm text-gray-300 mb-1">Port Mappings (JSON)</label>
                <input value={form.port_mappings} onChange={e => setForm({...form, port_mappings: e.target.value})}
                  className="w-full px-3 py-2 bg-gray-700 border border-gray-600 rounded text-white text-sm font-mono focus:outline-none focus:border-blue-500"
                  placeholder='{"80": 0}' />
              </div>
              <div>
                <label className="block text-sm text-gray-300 mb-1">Environment (JSON)</label>
                <input value={form.environment} onChange={e => setForm({...form, environment: e.target.value})}
                  className="w-full px-3 py-2 bg-gray-700 border border-gray-600 rounded text-white text-sm font-mono focus:outline-none focus:border-blue-500"
                  placeholder='{"KEY": "value"}' />
              </div>
              <div className="flex gap-3 pt-2">
                <button type="submit" className="bg-blue-600 hover:bg-blue-700 text-white px-4 py-2 rounded text-sm transition-colors">Launch</button>
                <button type="button" onClick={() => setShowLaunch(false)} className="bg-gray-600 hover:bg-gray-700 text-white px-4 py-2 rounded text-sm transition-colors">Cancel</button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* Instance Table */}
      <div className="bg-gray-800 rounded-lg overflow-hidden">
        <table className="w-full">
          <thead>
            <tr className="border-b border-gray-700">
              <th className="text-left px-4 py-3 text-sm font-medium text-gray-400">Name</th>
              <th className="text-left px-4 py-3 text-sm font-medium text-gray-400">ID</th>
              <th className="text-left px-4 py-3 text-sm font-medium text-gray-400">State</th>
              <th className="text-left px-4 py-3 text-sm font-medium text-gray-400">Type</th>
              <th className="text-left px-4 py-3 text-sm font-medium text-gray-400">Image</th>
              <th className="text-left px-4 py-3 text-sm font-medium text-gray-400">Actions</th>
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr><td colSpan="6" className="px-4 py-8 text-center text-gray-500">Loading...</td></tr>
            ) : instances.length === 0 ? (
              <tr><td colSpan="6" className="px-4 py-8 text-center text-gray-500">No instances. Launch one to get started.</td></tr>
            ) : (
              instances.map((inst) => (
                <tr key={inst.id} className="border-b border-gray-700/50 hover:bg-gray-700/30">
                  <td className="px-4 py-3 text-sm text-white">
                    <Link to={`/ec2/${inst.id}`} className="text-blue-400 hover:underline">{inst.name}</Link>
                  </td>
                  <td className="px-4 py-3 text-sm text-gray-400 font-mono">{inst.id.slice(0, 8)}</td>
                  <td className="px-4 py-3 text-sm">
                    <span className={`px-2 py-1 rounded text-xs font-medium ${STATE_COLORS[inst.state] || 'text-gray-400'}`}>
                      {inst.state}
                    </span>
                  </td>
                  <td className="px-4 py-3 text-sm text-gray-300">{inst.instance_type}</td>
                  <td className="px-4 py-3 text-sm text-gray-400">{inst.image}</td>
                  <td className="px-4 py-3 text-sm space-x-2">
                    {inst.state === 'stopped' && (
                      <button onClick={() => action(inst.id, 'start')} className="text-green-400 hover:text-green-300 text-xs">Start</button>
                    )}
                    {inst.state === 'running' && (
                      <>
                        <button onClick={() => action(inst.id, 'stop')} className="text-yellow-400 hover:text-yellow-300 text-xs">Stop</button>
                        <button onClick={() => action(inst.id, 'reboot')} className="text-blue-400 hover:text-blue-300 text-xs">Reboot</button>
                      </>
                    )}
                    {inst.state !== 'terminated' && (
                      <button onClick={() => { if (confirm('Terminate this instance?')) action(inst.id, 'terminate'); }}
                        className="text-red-400 hover:text-red-300 text-xs">Terminate</button>
                    )}
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
