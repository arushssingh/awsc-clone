import { useState, useEffect } from 'react';
import { Link } from 'react-router-dom';
import api from '../api';

export default function S3() {
  const [buckets, setBuckets] = useState([]);
  const [showCreate, setShowCreate] = useState(false);
  const [name, setName] = useState('');
  const [loading, setLoading] = useState(true);

  const fetchBuckets = () => {
    api.get('/s3/buckets')
      .then(res => setBuckets(res.data))
      .catch(() => {})
      .finally(() => setLoading(false));
  };

  useEffect(() => { fetchBuckets(); }, []);

  const createBucket = async (e) => {
    e.preventDefault();
    try {
      await api.post('/s3/buckets', { name });
      setShowCreate(false);
      setName('');
      fetchBuckets();
    } catch (err) {
      alert(err.response?.data?.detail || 'Failed to create bucket');
    }
  };

  const deleteBucket = async (bucketName) => {
    if (!confirm(`Delete bucket "${bucketName}"?`)) return;
    try {
      await api.delete(`/s3/buckets/${bucketName}`);
      fetchBuckets();
    } catch (err) {
      alert(err.response?.data?.detail || 'Failed to delete bucket');
    }
  };

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-bold text-white">S3 Buckets</h1>
        <button onClick={() => setShowCreate(true)}
          className="bg-blue-600 hover:bg-blue-700 text-white px-4 py-2 rounded text-sm transition-colors">
          Create Bucket
        </button>
      </div>

      {showCreate && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
          <div className="bg-gray-800 rounded-lg p-6 w-full max-w-md mx-4">
            <h2 className="text-lg font-semibold text-white mb-4">Create Bucket</h2>
            <form onSubmit={createBucket}>
              <input value={name} onChange={e => setName(e.target.value)} required placeholder="bucket-name"
                className="w-full px-3 py-2 bg-gray-700 border border-gray-600 rounded text-white text-sm mb-4 focus:outline-none focus:border-blue-500" />
              <div className="flex gap-3">
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
              <th className="text-left px-4 py-3 text-sm font-medium text-gray-400">Region</th>
              <th className="text-left px-4 py-3 text-sm font-medium text-gray-400">Created</th>
              <th className="text-left px-4 py-3 text-sm font-medium text-gray-400">Actions</th>
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr><td colSpan="4" className="px-4 py-8 text-center text-gray-500">Loading...</td></tr>
            ) : buckets.length === 0 ? (
              <tr><td colSpan="4" className="px-4 py-8 text-center text-gray-500">No buckets yet.</td></tr>
            ) : (
              buckets.map((b) => (
                <tr key={b.name} className="border-b border-gray-700/50 hover:bg-gray-700/30">
                  <td className="px-4 py-3 text-sm">
                    <Link to={`/s3/${b.name}`} className="text-blue-400 hover:underline">{b.name}</Link>
                  </td>
                  <td className="px-4 py-3 text-sm text-gray-400">{b.region}</td>
                  <td className="px-4 py-3 text-sm text-gray-400">{new Date(b.created_at).toLocaleDateString()}</td>
                  <td className="px-4 py-3 text-sm">
                    <button onClick={() => deleteBucket(b.name)} className="text-red-400 hover:text-red-300 text-xs">Delete</button>
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
