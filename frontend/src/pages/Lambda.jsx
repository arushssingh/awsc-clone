import { useState, useEffect } from 'react';
import { Link } from 'react-router-dom';
import api from '../api';
import { useToast } from '../components/Toast';

export default function Lambda() {
  const toast = useToast();
  const [functions, setFunctions] = useState([]);
  const [showCreate, setShowCreate] = useState(false);
  const [loading, setLoading] = useState(true);
  const [form, setForm] = useState({
    name: '', runtime: 'python3.11', handler: 'handler.handler', timeout: 30, memory_limit: 128,
  });
  const [codeFile, setCodeFile] = useState(null);

  const fetchFunctions = () => {
    api.get('/lambda/functions')
      .then(res => setFunctions(Array.isArray(res.data) ? res.data : []))
      .catch(() => setFunctions([]))
      .finally(() => setLoading(false));
  };

  useEffect(() => { fetchFunctions(); }, []);

  const create = async (e) => {
    e.preventDefault();
    if (!codeFile) { toast.error('Please select a code ZIP file'); return; }
    const formData = new FormData();
    formData.append('metadata', JSON.stringify(form));
    formData.append('code', codeFile);
    try {
      await api.post('/lambda/functions', formData);
      setShowCreate(false);
      fetchFunctions();
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Failed to create function');
    }
  };

  const deleteFunc = async (id) => {
    if (!confirm('Delete this function?')) return;
    try {
      await api.delete(`/lambda/functions/${id}`);
      fetchFunctions();
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Failed to delete');
    }
  };

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-bold text-white">Lambda Functions</h1>
        <button onClick={() => setShowCreate(true)}
          className="bg-blue-600 hover:bg-blue-700 text-white px-4 py-2 rounded text-sm transition-colors">
          Create Function
        </button>
      </div>

      {showCreate && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
          <div className="bg-gray-800 rounded-lg p-6 w-full max-w-lg mx-4">
            <h2 className="text-lg font-semibold text-white mb-4">Create Function</h2>
            <form onSubmit={create} className="space-y-3">
              <div>
                <label className="block text-sm text-gray-300 mb-1">Name</label>
                <input value={form.name} onChange={e => setForm({...form, name: e.target.value})} required
                  className="w-full px-3 py-2 bg-gray-700 border border-gray-600 rounded text-white text-sm focus:outline-none focus:border-blue-500" />
              </div>
              <div>
                <label className="block text-sm text-gray-300 mb-1">Runtime</label>
                <select value={form.runtime} onChange={e => setForm({...form, runtime: e.target.value})}
                  className="w-full px-3 py-2 bg-gray-700 border border-gray-600 rounded text-white text-sm focus:outline-none focus:border-blue-500">
                  <option value="python3.11">Python 3.11</option>
                  <option value="node20">Node.js 20</option>
                </select>
              </div>
              <div>
                <label className="block text-sm text-gray-300 mb-1">Handler</label>
                <input value={form.handler} onChange={e => setForm({...form, handler: e.target.value})}
                  className="w-full px-3 py-2 bg-gray-700 border border-gray-600 rounded text-white text-sm focus:outline-none focus:border-blue-500" />
              </div>
              <div>
                <label className="block text-sm text-gray-300 mb-1">Code (ZIP)</label>
                <input type="file" accept=".zip" onChange={e => setCodeFile(e.target.files[0])}
                  className="w-full text-sm text-gray-400" />
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="block text-sm text-gray-300 mb-1">Timeout (s)</label>
                  <input type="number" value={form.timeout} onChange={e => setForm({...form, timeout: parseInt(e.target.value)})}
                    className="w-full px-3 py-2 bg-gray-700 border border-gray-600 rounded text-white text-sm focus:outline-none focus:border-blue-500" />
                </div>
                <div>
                  <label className="block text-sm text-gray-300 mb-1">Memory (MB)</label>
                  <input type="number" value={form.memory_limit} onChange={e => setForm({...form, memory_limit: parseInt(e.target.value)})}
                    className="w-full px-3 py-2 bg-gray-700 border border-gray-600 rounded text-white text-sm focus:outline-none focus:border-blue-500" />
                </div>
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
              <th className="text-left px-4 py-3 text-sm font-medium text-gray-400">Runtime</th>
              <th className="text-left px-4 py-3 text-sm font-medium text-gray-400">Memory</th>
              <th className="text-left px-4 py-3 text-sm font-medium text-gray-400">Timeout</th>
              <th className="text-left px-4 py-3 text-sm font-medium text-gray-400">Actions</th>
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr><td colSpan="5" className="px-4 py-8 text-center text-gray-500">Loading...</td></tr>
            ) : functions.length === 0 ? (
              <tr><td colSpan="5" className="px-4 py-8 text-center text-gray-500">No functions yet.</td></tr>
            ) : (
              functions.map((fn) => (
                <tr key={fn.id} className="border-b border-gray-700/50 hover:bg-gray-700/30">
                  <td className="px-4 py-3 text-sm">
                    <Link to={`/lambda/${fn.id}`} className="text-blue-400 hover:underline">{fn.name}</Link>
                  </td>
                  <td className="px-4 py-3 text-sm text-gray-400">{fn.runtime}</td>
                  <td className="px-4 py-3 text-sm text-gray-400">{fn.memory_limit} MB</td>
                  <td className="px-4 py-3 text-sm text-gray-400">{fn.timeout}s</td>
                  <td className="px-4 py-3 text-sm">
                    <button onClick={() => deleteFunc(fn.id)} className="text-red-400 hover:text-red-300 text-xs">Delete</button>
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
