import { useState, useEffect, useRef } from 'react';
import { Link } from 'react-router-dom';
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

export default function Deploy() {
  const toast = useToast();
  const [deployments, setDeployments] = useState([]);
  const [loading, setLoading] = useState(true);
  const [showCreate, setShowCreate] = useState(false);
  const [name, setName] = useState('');
  const [zipFile, setZipFile] = useState(null);
  const [deploying, setDeploying] = useState(false);
  const fileRef = useRef(null);

  const fetchDeployments = () => {
    api.get('/deploy/projects')
      .then(res => setDeployments(res.data))
      .catch(() => {})
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    fetchDeployments();
    const interval = setInterval(fetchDeployments, 5000);
    return () => clearInterval(interval);
  }, []);

  const handleDeploy = async (e) => {
    e.preventDefault();
    if (!zipFile) { toast.error('Please select a ZIP file'); return; }
    setDeploying(true);
    const formData = new FormData();
    formData.append('name', name);
    formData.append('file', zipFile);
    try {
      await api.post('/deploy/projects', formData);
      setShowCreate(false);
      setName('');
      setZipFile(null);
      fetchDeployments();
      toast.success('Deployment started! Building...');
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Failed to deploy');
    } finally {
      setDeploying(false);
    }
  };

  const deleteDeployment = async (id) => {
    if (!confirm('Delete this deployment and all its data?')) return;
    try {
      await api.delete(`/deploy/projects/${id}`);
      fetchDeployments();
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Failed to delete');
    }
  };

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold text-white">Deploy</h1>
          <p className="text-sm text-gray-400 mt-1">Deploy any web project — React, Next.js, Vue, Python, Node.js, or static HTML</p>
        </div>
        <button onClick={() => setShowCreate(true)}
          className="bg-blue-600 hover:bg-blue-700 text-white px-4 py-2 rounded text-sm transition-colors">
          New Deployment
        </button>
      </div>

      {showCreate && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
          <div className="bg-gray-800 rounded-lg p-6 w-full max-w-md mx-4">
            <h2 className="text-lg font-semibold text-white mb-4">New Deployment</h2>
            <form onSubmit={handleDeploy} className="space-y-4">
              <div>
                <label className="block text-sm text-gray-300 mb-1">Project Name</label>
                <input value={name} onChange={e => setName(e.target.value)} required placeholder="my-website"
                  className="w-full px-3 py-2 bg-gray-700 border border-gray-600 rounded text-white text-sm focus:outline-none focus:border-blue-500" />
              </div>
              <div>
                <label className="block text-sm text-gray-300 mb-1">Project ZIP</label>
                <div
                  onClick={() => fileRef.current?.click()}
                  className="border-2 border-dashed border-gray-600 hover:border-gray-400 rounded-lg p-4 text-center cursor-pointer transition-colors">
                  <input ref={fileRef} type="file" accept=".zip" className="hidden"
                    onChange={e => setZipFile(e.target.files[0])} />
                  {zipFile ? (
                    <p className="text-sm text-green-400">{zipFile.name} ({(zipFile.size / 1024 / 1024).toFixed(1)} MB)</p>
                  ) : (
                    <p className="text-sm text-gray-400">Click to select ZIP file</p>
                  )}
                </div>
              </div>
              <div className="bg-gray-900 rounded p-3">
                <p className="text-xs text-gray-400 font-medium mb-1">Supported project types:</p>
                <p className="text-xs text-gray-500">React, Next.js, Vue, Angular, Svelte, Vite, Node.js, Python (Flask/FastAPI), Static HTML, Custom Dockerfile</p>
              </div>
              <div className="flex gap-3">
                <button type="submit" disabled={deploying}
                  className="bg-blue-600 hover:bg-blue-700 text-white px-4 py-2 rounded text-sm disabled:opacity-50">
                  {deploying ? 'Uploading...' : 'Deploy'}
                </button>
                <button type="button" onClick={() => { setShowCreate(false); setZipFile(null); }}
                  className="bg-gray-600 hover:bg-gray-700 text-white px-4 py-2 rounded text-sm">Cancel</button>
              </div>
            </form>
          </div>
        </div>
      )}

      {loading ? (
        <div className="text-gray-500 text-center py-8">Loading...</div>
      ) : deployments.length === 0 ? (
        <div className="bg-gray-800 rounded-lg p-12 text-center">
          <p className="text-gray-400 mb-2">No deployments yet</p>
          <p className="text-gray-500 text-sm">Upload a ZIP of your project to deploy it automatically</p>
        </div>
      ) : (
        <div className="grid gap-4">
          {deployments.map(dep => (
            <div key={dep.id} className="bg-gray-800 rounded-lg p-4 hover:bg-gray-800/80 transition-colors">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-3">
                  <Link to={`/deploy/${dep.id}`} className="text-white font-medium hover:text-blue-400 transition-colors">
                    {dep.name}
                  </Link>
                  <span className={`px-2 py-0.5 rounded text-xs font-medium ${STATUS_COLORS[dep.status] || 'bg-gray-500/20 text-gray-400'}`}>
                    {dep.status === 'building' && <span className="inline-block w-2 h-2 bg-blue-400 rounded-full animate-pulse mr-1" />}
                    {dep.status}
                  </span>
                  {dep.project_label && (
                    <span className="text-xs text-gray-500 bg-gray-700 px-2 py-0.5 rounded">{dep.project_label}</span>
                  )}
                </div>
                <div className="flex items-center gap-3">
                  {dep.url && dep.status === 'running' && (
                    <a href={dep.url} target="_blank" rel="noreferrer"
                      className="text-xs text-blue-400 hover:text-blue-300 font-mono">{dep.url}</a>
                  )}
                  <Link to={`/deploy/${dep.id}`} className="text-gray-400 hover:text-white text-xs">Details</Link>
                  <button onClick={() => deleteDeployment(dep.id)} className="text-red-400 hover:text-red-300 text-xs">Delete</button>
                </div>
              </div>
              <div className="flex items-center gap-4 mt-2 text-xs text-gray-500">
                <span>{new Date(dep.created_at).toLocaleString()}</span>
                {dep.port && <span>Port: {dep.port}</span>}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
