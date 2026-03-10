import { useState, useEffect } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import api from '../api';
import { useToast } from '../components/Toast';

export default function S3Bucket() {
  const toast = useToast();
  const { name } = useParams();
  const navigate = useNavigate();
  const [objects, setObjects] = useState([]);
  const [prefix, setPrefix] = useState('');
  const [loading, setLoading] = useState(true);

  const fetchObjects = (p = prefix) => {
    setLoading(true);
    api.get(`/s3/buckets/${name}/objects`, { params: { prefix: p, delimiter: '/' } })
      .then(res => setObjects(res.data))
      .catch(() => {})
      .finally(() => setLoading(false));
  };

  useEffect(() => { fetchObjects(); }, [name]);

  const uploadFile = async (e) => {
    const file = e.target.files[0];
    if (!file) return;
    const formData = new FormData();
    formData.append('file', file);
    const key = prefix + file.name;
    try {
      await api.put(`/s3/buckets/${name}/objects/${encodeURIComponent(key)}`, formData);
      fetchObjects();
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Upload failed');
    }
  };

  const download = async (key) => {
    try {
      const res = await api.get(`/s3/buckets/${name}/objects/${encodeURIComponent(key)}`);
      window.open(res.data.url, '_blank');
    } catch (err) {
      toast.error('Download failed');
    }
  };

  const deleteObject = async (key) => {
    if (!confirm(`Delete "${key}"?`)) return;
    try {
      await api.delete(`/s3/buckets/${name}/objects/${encodeURIComponent(key)}`);
      fetchObjects();
    } catch (err) {
      toast.error('Delete failed');
    }
  };

  const navigatePrefix = (p) => {
    setPrefix(p);
    fetchObjects(p);
  };

  const breadcrumbs = prefix.split('/').filter(Boolean);

  return (
    <div>
      <div className="flex items-center gap-4 mb-6">
        <button onClick={() => navigate('/s3')} className="text-gray-400 hover:text-white">&larr; Buckets</button>
        <h1 className="text-2xl font-bold text-white">{name}</h1>
      </div>

      {/* Breadcrumbs */}
      <div className="flex items-center gap-1 mb-4 text-sm">
        <button onClick={() => navigatePrefix('')} className="text-blue-400 hover:underline">/</button>
        {breadcrumbs.map((part, i) => {
          const path = breadcrumbs.slice(0, i + 1).join('/') + '/';
          return (
            <span key={i}>
              <span className="text-gray-500">/</span>
              <button onClick={() => navigatePrefix(path)} className="text-blue-400 hover:underline">{part}</button>
            </span>
          );
        })}
      </div>

      <div className="mb-4">
        <label className="bg-blue-600 hover:bg-blue-700 text-white px-4 py-2 rounded text-sm cursor-pointer transition-colors">
          Upload File
          <input type="file" onChange={uploadFile} className="hidden" />
        </label>
      </div>

      <div className="bg-gray-800 rounded-lg overflow-hidden">
        <table className="w-full">
          <thead>
            <tr className="border-b border-gray-700">
              <th className="text-left px-4 py-3 text-sm font-medium text-gray-400">Key</th>
              <th className="text-left px-4 py-3 text-sm font-medium text-gray-400">Size</th>
              <th className="text-left px-4 py-3 text-sm font-medium text-gray-400">Modified</th>
              <th className="text-left px-4 py-3 text-sm font-medium text-gray-400">Actions</th>
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr><td colSpan="4" className="px-4 py-8 text-center text-gray-500">Loading...</td></tr>
            ) : objects.length === 0 ? (
              <tr><td colSpan="4" className="px-4 py-8 text-center text-gray-500">Empty</td></tr>
            ) : (
              objects.map((obj) => (
                <tr key={obj.key} className="border-b border-gray-700/50 hover:bg-gray-700/30">
                  <td className="px-4 py-3 text-sm">
                    {obj.is_prefix ? (
                      <button onClick={() => navigatePrefix(obj.key)} className="text-blue-400 hover:underline">{obj.key}</button>
                    ) : (
                      <span className="text-white">{obj.key.split('/').pop()}</span>
                    )}
                  </td>
                  <td className="px-4 py-3 text-sm text-gray-400">{obj.size || '--'}</td>
                  <td className="px-4 py-3 text-sm text-gray-400">{obj.last_modified || '--'}</td>
                  <td className="px-4 py-3 text-sm space-x-2">
                    {!obj.is_prefix && (
                      <>
                        <button onClick={() => download(obj.key)} className="text-blue-400 hover:text-blue-300 text-xs">Download</button>
                        <button onClick={() => deleteObject(obj.key)} className="text-red-400 hover:text-red-300 text-xs">Delete</button>
                      </>
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
