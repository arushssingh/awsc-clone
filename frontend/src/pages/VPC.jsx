import { useState, useEffect } from 'react';
import { Link } from 'react-router-dom';
import api from '../api';
import { useToast } from '../components/Toast';

export default function VPC() {
  const toast = useToast();
  const [vpcs, setVpcs] = useState([]);
  const [showCreate, setShowCreate] = useState(false);
  const [loading, setLoading] = useState(true);
  const [form, setForm] = useState({ name: '', cidr_block: '10.0.0.0/16' });

  const fetchVpcs = () => {
    api.get('/vpc/vpcs')
      .then(res => setVpcs(res.data))
      .catch(() => {})
      .finally(() => setLoading(false));
  };

  useEffect(() => { fetchVpcs(); }, []);

  const create = async (e) => {
    e.preventDefault();
    try {
      await api.post('/vpc/vpcs', form);
      setShowCreate(false);
      setForm({ name: '', cidr_block: '10.0.0.0/16' });
      fetchVpcs();
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Failed to create VPC');
    }
  };

  const deleteVpc = async (id) => {
    if (!confirm('Delete this VPC?')) return;
    try {
      await api.delete(`/vpc/vpcs/${id}`);
      fetchVpcs();
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Failed to delete VPC');
    }
  };

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-bold text-white">VPC - Virtual Private Cloud</h1>
        <button onClick={() => setShowCreate(true)}
          className="bg-blue-600 hover:bg-blue-700 text-white px-4 py-2 rounded text-sm transition-colors">
          Create VPC
        </button>
      </div>

      {showCreate && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
          <div className="bg-gray-800 rounded-lg p-6 w-full max-w-md mx-4">
            <h2 className="text-lg font-semibold text-white mb-4">Create VPC</h2>
            <form onSubmit={create} className="space-y-3">
              <div>
                <label className="block text-sm text-gray-300 mb-1">Name</label>
                <input value={form.name} onChange={e => setForm({...form, name: e.target.value})} required
                  className="w-full px-3 py-2 bg-gray-700 border border-gray-600 rounded text-white text-sm focus:outline-none focus:border-blue-500" />
              </div>
              <div>
                <label className="block text-sm text-gray-300 mb-1">CIDR Block</label>
                <input value={form.cidr_block} onChange={e => setForm({...form, cidr_block: e.target.value})}
                  className="w-full px-3 py-2 bg-gray-700 border border-gray-600 rounded text-white text-sm font-mono focus:outline-none focus:border-blue-500" />
              </div>
              <div className="flex gap-3 pt-2">
                <button type="submit" className="bg-blue-600 hover:bg-blue-700 text-white px-4 py-2 rounded text-sm">Create</button>
                <button type="button" onClick={() => setShowCreate(false)} className="bg-gray-600 hover:bg-gray-700 text-white px-4 py-2 rounded text-sm">Cancel</button>
              </div>
            </form>
          </div>
        </div>
      )}

      <div className="bg-gray-800 rounded-lg overflow-hidden">
        <table className="w-full">
          <thead>
            <tr className="border-b border-gray-700">
              <th className="text-left px-4 py-3 text-sm font-medium text-gray-400">Name</th>
              <th className="text-left px-4 py-3 text-sm font-medium text-gray-400">ID</th>
              <th className="text-left px-4 py-3 text-sm font-medium text-gray-400">CIDR Block</th>
              <th className="text-left px-4 py-3 text-sm font-medium text-gray-400">State</th>
              <th className="text-left px-4 py-3 text-sm font-medium text-gray-400">Actions</th>
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr><td colSpan="5" className="px-4 py-8 text-center text-gray-500">Loading...</td></tr>
            ) : vpcs.length === 0 ? (
              <tr><td colSpan="5" className="px-4 py-8 text-center text-gray-500">No VPCs created.</td></tr>
            ) : (
              vpcs.map((vpc) => (
                <tr key={vpc.id} className="border-b border-gray-700/50 hover:bg-gray-700/30">
                  <td className="px-4 py-3 text-sm">
                    <Link to={`/vpc/${vpc.id}`} className="text-blue-400 hover:underline">{vpc.name}</Link>
                  </td>
                  <td className="px-4 py-3 text-sm text-gray-400 font-mono">{vpc.id.slice(0, 8)}</td>
                  <td className="px-4 py-3 text-sm text-gray-300 font-mono">{vpc.cidr_block}</td>
                  <td className="px-4 py-3 text-sm">
                    <span className="bg-green-500/20 text-green-400 px-2 py-1 rounded text-xs">{vpc.state}</span>
                  </td>
                  <td className="px-4 py-3 text-sm">
                    <button onClick={() => deleteVpc(vpc.id)} className="text-red-400 hover:text-red-300 text-xs">Delete</button>
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
