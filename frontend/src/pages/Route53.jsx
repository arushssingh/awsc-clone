import { useState, useEffect } from 'react';
import api from '../api';
import { useToast } from '../components/Toast';

const STATE_COLORS = {
  active: 'bg-green-500/20 text-green-400',
  pending: 'bg-yellow-500/20 text-yellow-400',
  error: 'bg-red-500/20 text-red-400',
};

export default function Route53() {
  const toast = useToast();
  const [domains, setDomains] = useState([]);
  const [instances, setInstances] = useState([]);
  const [showAdd, setShowAdd] = useState(false);
  const [loading, setLoading] = useState(true);
  const [form, setForm] = useState({
    domain: '', target_type: 'instance', target_id: '', target_address: '', ssl_enabled: true,
  });

  const fetchDomains = () => {
    api.get('/route53/domains')
      .then(res => setDomains(res.data))
      .catch(() => {})
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    fetchDomains();
    api.get('/ec2/instances').then(res => setInstances(res.data)).catch(() => {});
  }, []);

  const addDomain = async (e) => {
    e.preventDefault();
    try {
      const body = {
        ...form,
        target_id: form.target_type === 'instance' ? form.target_id : null,
        target_address: form.target_type === 'external' ? form.target_address : null,
      };
      await api.post('/route53/domains', body);
      setShowAdd(false);
      fetchDomains();
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Failed to add domain');
    }
  };

  const verify = async (id) => {
    try {
      const res = await api.post(`/route53/domains/${id}/verify`);
      toast.success(res.data.message || 'DNS verification complete');
      fetchDomains();
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Verification failed');
    }
  };

  const deleteDomain = async (id) => {
    if (!confirm('Remove this domain?')) return;
    try {
      await api.delete(`/route53/domains/${id}`);
      fetchDomains();
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Failed to delete');
    }
  };

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-bold text-white">Route 53 - Domains</h1>
        <button onClick={() => setShowAdd(true)}
          className="bg-blue-600 hover:bg-blue-700 text-white px-4 py-2 rounded text-sm transition-colors">
          Add Domain
        </button>
      </div>

      {showAdd && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
          <div className="bg-gray-800 rounded-lg p-6 w-full max-w-lg mx-4">
            <h2 className="text-lg font-semibold text-white mb-4">Add Domain</h2>
            <form onSubmit={addDomain} className="space-y-3">
              <div>
                <label className="block text-sm text-gray-300 mb-1">Domain</label>
                <input value={form.domain} onChange={e => setForm({...form, domain: e.target.value})} required
                  placeholder="myapp.example.com"
                  className="w-full px-3 py-2 bg-gray-700 border border-gray-600 rounded text-white text-sm focus:outline-none focus:border-blue-500" />
              </div>
              <div>
                <label className="block text-sm text-gray-300 mb-1">Target Type</label>
                <select value={form.target_type} onChange={e => setForm({...form, target_type: e.target.value})}
                  className="w-full px-3 py-2 bg-gray-700 border border-gray-600 rounded text-white text-sm focus:outline-none focus:border-blue-500">
                  <option value="instance">EC2 Instance</option>
                  <option value="external">External URL</option>
                </select>
              </div>
              {form.target_type === 'instance' && (
                <div>
                  <label className="block text-sm text-gray-300 mb-1">Instance</label>
                  <select value={form.target_id} onChange={e => setForm({...form, target_id: e.target.value})} required
                    className="w-full px-3 py-2 bg-gray-700 border border-gray-600 rounded text-white text-sm focus:outline-none focus:border-blue-500">
                    <option value="">Select instance...</option>
                    {instances.filter(i => i.state === 'running').map(i => (
                      <option key={i.id} value={i.id}>{i.name} ({i.id.slice(0, 8)})</option>
                    ))}
                  </select>
                </div>
              )}
              {form.target_type === 'external' && (
                <div>
                  <label className="block text-sm text-gray-300 mb-1">URL</label>
                  <input value={form.target_address} onChange={e => setForm({...form, target_address: e.target.value})}
                    placeholder="http://localhost:3000"
                    className="w-full px-3 py-2 bg-gray-700 border border-gray-600 rounded text-white text-sm focus:outline-none focus:border-blue-500" />
                </div>
              )}
              <div className="bg-gray-900 rounded p-3 text-xs text-gray-400">
                After adding, point your domain's A record to your server's public IP. Caddy will auto-obtain an SSL certificate.
              </div>
              <div className="flex gap-3 pt-2">
                <button type="submit" className="bg-blue-600 hover:bg-blue-700 text-white px-4 py-2 rounded text-sm">Add Domain</button>
                <button type="button" onClick={() => setShowAdd(false)} className="bg-gray-600 hover:bg-gray-700 text-white px-4 py-2 rounded text-sm">Cancel</button>
              </div>
            </form>
          </div>
        </div>
      )}

      <div className="bg-gray-800 rounded-lg overflow-hidden">
        <table className="w-full">
          <thead>
            <tr className="border-b border-gray-700">
              <th className="text-left px-4 py-3 text-sm font-medium text-gray-400">Domain</th>
              <th className="text-left px-4 py-3 text-sm font-medium text-gray-400">Target</th>
              <th className="text-left px-4 py-3 text-sm font-medium text-gray-400">SSL</th>
              <th className="text-left px-4 py-3 text-sm font-medium text-gray-400">State</th>
              <th className="text-left px-4 py-3 text-sm font-medium text-gray-400">Actions</th>
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr><td colSpan="5" className="px-4 py-8 text-center text-gray-500">Loading...</td></tr>
            ) : domains.length === 0 ? (
              <tr><td colSpan="5" className="px-4 py-8 text-center text-gray-500">No domains configured.</td></tr>
            ) : (
              domains.map((d) => (
                <tr key={d.id} className="border-b border-gray-700/50 hover:bg-gray-700/30">
                  <td className="px-4 py-3 text-sm text-white font-mono">{d.domain}</td>
                  <td className="px-4 py-3 text-sm text-gray-400">{d.target_address || d.target_id?.slice(0, 8) || '--'}</td>
                  <td className="px-4 py-3 text-sm">
                    {d.ssl_enabled ? (
                      <span className="text-green-400">Enabled</span>
                    ) : (
                      <span className="text-gray-500">Disabled</span>
                    )}
                  </td>
                  <td className="px-4 py-3 text-sm">
                    <span className={`px-2 py-1 rounded text-xs font-medium ${STATE_COLORS[d.state] || 'text-gray-400'}`}>
                      {d.state}
                    </span>
                  </td>
                  <td className="px-4 py-3 text-sm space-x-2">
                    <button onClick={() => verify(d.id)} className="text-blue-400 hover:text-blue-300 text-xs">Verify DNS</button>
                    <button onClick={() => deleteDomain(d.id)} className="text-red-400 hover:text-red-300 text-xs">Delete</button>
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
