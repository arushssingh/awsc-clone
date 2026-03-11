import { useState, useEffect, useRef } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import api from '../api';
import { useToast } from '../components/Toast';

const STATE_COLORS = {
  running: 'bg-green-500/20 text-green-400',
  pending: 'bg-yellow-500/20 text-yellow-400',
  stopping: 'bg-yellow-500/20 text-yellow-400',
  stopped: 'bg-red-500/20 text-red-400',
  terminated: 'bg-gray-500/20 text-gray-400',
};

const DEPLOY_STATUS_COLORS = {
  uploading: 'bg-yellow-500/20 text-yellow-400',
  queued: 'bg-yellow-500/20 text-yellow-400',
  building: 'bg-blue-500/20 text-blue-400',
  running: 'bg-green-500/20 text-green-400',
  failed: 'bg-red-500/20 text-red-400',
  stopped: 'bg-gray-500/20 text-gray-400',
};

export default function EC2() {
  const toast = useToast();
  const [searchParams, setSearchParams] = useSearchParams();
  const [tab, setTab] = useState('instances');

  // ── Instances ──────────────────────────────────────────────────────
  const [instances, setInstances] = useState([]);
  const [instLoading, setInstLoading] = useState(true);
  const [showLaunch, setShowLaunch] = useState(false);
  const [form, setForm] = useState({
    name: '', image: 'nginx:alpine', instance_type: 't2.micro',
    vpc_id: '', port_mappings: '{"80": 0}', environment: '{}', command: '',
  });

  const fetchInstances = () => {
    api.get('/ec2/instances')
      .then(res => setInstances(res.data))
      .catch(() => {})
      .finally(() => setInstLoading(false));
  };

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
      toast.error(err.response?.data?.detail || 'Failed to launch');
    }
  };

  const instanceAction = async (id, act) => {
    try {
      if (act === 'terminate') await api.delete(`/ec2/instances/${id}`);
      else await api.post(`/ec2/instances/${id}/${act}`);
      fetchInstances();
    } catch (err) {
      toast.error(err.response?.data?.detail || `Failed to ${act}`);
    }
  };

  // ── GitHub ─────────────────────────────────────────────────────────
  const [githubStatus, setGithubStatus] = useState({ connected: false, login: null });
  const [githubRepos, setGithubRepos] = useState([]);
  const [repoSearch, setRepoSearch] = useState('');
  const [reposLoading, setReposLoading] = useState(false);
  const [branches, setBranches] = useState([]);
  const [selectedRepo, setSelectedRepo] = useState(null);
  const [selectedBranch, setSelectedBranch] = useState('');

  const fetchGithubStatus = async () => {
    try {
      const res = await api.get('/github/status');
      setGithubStatus(res.data);
      if (res.data.connected) fetchGithubRepos();
    } catch {}
  };

  const fetchGithubRepos = async () => {
    setReposLoading(true);
    try {
      const res = await api.get('/github/repos');
      setGithubRepos(res.data);
    } catch (err) {
      toast.error('Failed to load GitHub repos');
    } finally {
      setReposLoading(false);
    }
  };

  const connectGithub = async () => {
    try {
      const res = await api.get('/github/auth/url');
      window.location.href = res.data.url;
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Failed to initiate GitHub OAuth');
    }
  };

  const disconnectGithub = async () => {
    try {
      await api.delete('/github/disconnect');
      setGithubStatus({ connected: false, login: null });
      setGithubRepos([]);
    } catch {}
  };

  const selectRepo = async (repo) => {
    setSelectedRepo(repo);
    setSelectedBranch(repo.default_branch);
    setBranches([]);
    const [owner, repoName] = repo.full_name.split('/');
    try {
      const res = await api.get(`/github/repos/${owner}/${repoName}/branches`);
      setBranches(res.data);
    } catch {}
  };

  // ── Deployments ────────────────────────────────────────────────────
  const [deployments, setDeployments] = useState([]);
  const [depLoading, setDepLoading] = useState(true);
  const [showCreate, setShowCreate] = useState(false);
  const [deploySource, setDeploySource] = useState('zip'); // 'zip' | 'github'
  const [depName, setDepName] = useState('');
  const [zipFile, setZipFile] = useState(null);
  const [deploying, setDeploying] = useState(false);
  const fileRef = useRef(null);

  const fetchDeployments = () => {
    api.get('/deploy/projects')
      .then(res => setDeployments(res.data))
      .catch(() => {})
      .finally(() => setDepLoading(false));
  };

  const handleZipDeploy = async (e) => {
    e.preventDefault();
    if (!zipFile) { toast.error('Please select a ZIP file'); return; }
    setDeploying(true);
    const formData = new FormData();
    formData.append('name', depName);
    formData.append('file', zipFile);
    try {
      await api.post('/deploy/projects', formData);
      setShowCreate(false);
      resetCreateForm();
      fetchDeployments();
      toast.success('Deployment started! Building...');
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Failed to deploy');
    } finally {
      setDeploying(false);
    }
  };

  const handleGithubDeploy = async (e) => {
    e.preventDefault();
    if (!selectedRepo) { toast.error('Please select a repository'); return; }
    setDeploying(true);
    const formData = new FormData();
    formData.append('name', depName || selectedRepo.name);
    formData.append('github_repo', selectedRepo.full_name);
    formData.append('github_branch', selectedBranch || selectedRepo.default_branch);
    try {
      await api.post('/deploy/projects/github', formData);
      setShowCreate(false);
      resetCreateForm();
      fetchDeployments();
      toast.success('GitHub deployment started! Cloning & building...');
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Failed to deploy from GitHub');
    } finally {
      setDeploying(false);
    }
  };

  const resetCreateForm = () => {
    setDepName('');
    setZipFile(null);
    setSelectedRepo(null);
    setSelectedBranch('');
    setBranches([]);
    setRepoSearch('');
    setDeploySource('zip');
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

  // ── Effects ────────────────────────────────────────────────────────
  useEffect(() => {
    fetchInstances();
    fetchDeployments();
    fetchGithubStatus();

    // Handle GitHub OAuth callback redirect
    if (searchParams.get('github') === 'connected') {
      toast.success('GitHub connected!');
      setTab('deployments');
      setSearchParams({});
    }

    const interval = setInterval(() => {
      fetchInstances();
      fetchDeployments();
    }, 5000);
    return () => clearInterval(interval);
  }, []);

  const filteredRepos = githubRepos.filter(r =>
    r.full_name.toLowerCase().includes(repoSearch.toLowerCase())
  );

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-bold text-white">EC2</h1>
        {tab === 'instances' ? (
          <button onClick={() => setShowLaunch(true)}
            className="bg-blue-600 hover:bg-blue-700 text-white px-4 py-2 rounded text-sm transition-colors">
            Launch Instance
          </button>
        ) : (
          <button onClick={() => setShowCreate(true)}
            className="bg-blue-600 hover:bg-blue-700 text-white px-4 py-2 rounded text-sm transition-colors">
            New Deployment
          </button>
        )}
      </div>

      {/* Tabs */}
      <div className="flex gap-1 mb-6 border-b border-gray-700">
        {[['instances', 'Instances'], ['deployments', 'Deployments']].map(([key, label]) => (
          <button key={key} onClick={() => setTab(key)}
            className={`px-4 py-2 text-sm transition-colors ${tab === key ? 'text-blue-400 border-b-2 border-blue-400' : 'text-gray-400 hover:text-white'}`}>
            {label}
            {key === 'deployments' && deployments.filter(d => d.status === 'building').length > 0 && (
              <span className="ml-2 inline-block w-2 h-2 bg-blue-400 rounded-full animate-pulse" />
            )}
          </button>
        ))}
      </div>

      {/* ── Launch Instance Modal ── */}
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
                <button type="submit" className="bg-blue-600 hover:bg-blue-700 text-white px-4 py-2 rounded text-sm">Launch</button>
                <button type="button" onClick={() => setShowLaunch(false)} className="bg-gray-600 hover:bg-gray-700 text-white px-4 py-2 rounded text-sm">Cancel</button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* ── New Deployment Modal ── */}
      {showCreate && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
          <div className="bg-gray-800 rounded-lg p-6 w-full max-w-lg mx-4 max-h-[90vh] overflow-y-auto">
            <h2 className="text-lg font-semibold text-white mb-4">New Deployment</h2>

            {/* Source tabs */}
            <div className="flex gap-1 mb-4 border-b border-gray-700">
              <button onClick={() => setDeploySource('zip')}
                className={`px-3 py-1.5 text-sm transition-colors ${deploySource === 'zip' ? 'text-blue-400 border-b-2 border-blue-400' : 'text-gray-400 hover:text-white'}`}>
                Upload ZIP
              </button>
              <button onClick={() => setDeploySource('github')}
                className={`px-3 py-1.5 text-sm transition-colors ${deploySource === 'github' ? 'text-blue-400 border-b-2 border-blue-400' : 'text-gray-400 hover:text-white'}`}>
                GitHub Repository
              </button>
            </div>

            {/* ZIP Deploy */}
            {deploySource === 'zip' && (
              <form onSubmit={handleZipDeploy} className="space-y-4">
                <div>
                  <label className="block text-sm text-gray-300 mb-1">Project Name</label>
                  <input value={depName} onChange={e => setDepName(e.target.value)} required placeholder="my-website"
                    className="w-full px-3 py-2 bg-gray-700 border border-gray-600 rounded text-white text-sm focus:outline-none focus:border-blue-500" />
                </div>
                <div>
                  <label className="block text-sm text-gray-300 mb-1">Project ZIP</label>
                  <div onClick={() => fileRef.current?.click()}
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
                  <p className="text-xs text-gray-400 font-medium mb-1">Auto-detected & built:</p>
                  <p className="text-xs text-gray-500">React, Next.js, Vue, Angular, Svelte, Vite, Node.js, Python (Flask/FastAPI), Static HTML, Custom Dockerfile</p>
                </div>
                <div className="flex gap-3">
                  <button type="submit" disabled={deploying}
                    className="bg-blue-600 hover:bg-blue-700 text-white px-4 py-2 rounded text-sm disabled:opacity-50">
                    {deploying ? 'Uploading...' : 'Deploy'}
                  </button>
                  <button type="button" onClick={() => { setShowCreate(false); resetCreateForm(); }}
                    className="bg-gray-600 hover:bg-gray-700 text-white px-4 py-2 rounded text-sm">Cancel</button>
                </div>
              </form>
            )}

            {/* GitHub Deploy */}
            {deploySource === 'github' && (
              <div className="space-y-4">
                {!githubStatus.connected ? (
                  <div className="text-center py-6">
                    <p className="text-gray-400 mb-1">Connect your GitHub account</p>
                    <p className="text-gray-500 text-xs mb-4">to deploy directly from any repository</p>
                    <button onClick={connectGithub}
                      className="bg-gray-700 hover:bg-gray-600 text-white px-4 py-2 rounded text-sm flex items-center gap-2 mx-auto">
                      <svg viewBox="0 0 24 24" className="w-4 h-4 fill-current"><path d="M12 0C5.374 0 0 5.373 0 12c0 5.302 3.438 9.8 8.207 11.387.599.111.793-.261.793-.577v-2.234c-3.338.726-4.033-1.416-4.033-1.416-.546-1.387-1.333-1.756-1.333-1.756-1.089-.745.083-.729.083-.729 1.205.084 1.839 1.237 1.839 1.237 1.07 1.834 2.807 1.304 3.492.997.107-.775.418-1.305.762-1.604-2.665-.305-5.467-1.334-5.467-5.931 0-1.311.469-2.381 1.236-3.221-.124-.303-.535-1.524.117-3.176 0 0 1.008-.322 3.301 1.23A11.509 11.509 0 0 1 12 5.803c1.02.005 2.047.138 3.006.404 2.291-1.552 3.297-1.23 3.297-1.23.653 1.653.242 2.874.118 3.176.77.84 1.235 1.911 1.235 3.221 0 4.609-2.807 5.624-5.479 5.929.43.372.823 1.102.823 2.222v3.293c0 .319.192.694.801.576C20.566 21.797 24 17.3 24 12c0-6.627-5.373-12-12-12z"/></svg>
                      Connect GitHub
                    </button>
                  </div>
                ) : (
                  <form onSubmit={handleGithubDeploy} className="space-y-4">
                    <div className="flex items-center justify-between bg-gray-900 rounded p-2 px-3">
                      <div className="flex items-center gap-2">
                        <svg viewBox="0 0 24 24" className="w-4 h-4 fill-white"><path d="M12 0C5.374 0 0 5.373 0 12c0 5.302 3.438 9.8 8.207 11.387.599.111.793-.261.793-.577v-2.234c-3.338.726-4.033-1.416-4.033-1.416-.546-1.387-1.333-1.756-1.333-1.756-1.089-.745.083-.729.083-.729 1.205.084 1.839 1.237 1.839 1.237 1.07 1.834 2.807 1.304 3.492.997.107-.775.418-1.305.762-1.604-2.665-.305-5.467-1.334-5.467-5.931 0-1.311.469-2.381 1.236-3.221-.124-.303-.535-1.524.117-3.176 0 0 1.008-.322 3.301 1.23A11.509 11.509 0 0 1 12 5.803c1.02.005 2.047.138 3.006.404 2.291-1.552 3.297-1.23 3.297-1.23.653 1.653.242 2.874.118 3.176.77.84 1.235 1.911 1.235 3.221 0 4.609-2.807 5.624-5.479 5.929.43.372.823 1.102.823 2.222v3.293c0 .319.192.694.801.576C20.566 21.797 24 17.3 24 12c0-6.627-5.373-12-12-12z"/></svg>
                        <span className="text-sm text-gray-300">Connected as <span className="text-white font-medium">{githubStatus.login}</span></span>
                      </div>
                      <button type="button" onClick={disconnectGithub} className="text-xs text-gray-500 hover:text-red-400">Disconnect</button>
                    </div>

                    <div>
                      <label className="block text-sm text-gray-300 mb-1">Deployment Name</label>
                      <input value={depName} onChange={e => setDepName(e.target.value)}
                        placeholder={selectedRepo ? selectedRepo.name : 'auto from repo'}
                        className="w-full px-3 py-2 bg-gray-700 border border-gray-600 rounded text-white text-sm focus:outline-none focus:border-blue-500" />
                    </div>

                    <div>
                      <label className="block text-sm text-gray-300 mb-1">Repository</label>
                      <input value={repoSearch} onChange={e => setRepoSearch(e.target.value)}
                        placeholder="Search repositories..."
                        className="w-full px-3 py-2 bg-gray-700 border border-gray-600 rounded text-white text-sm focus:outline-none focus:border-blue-500 mb-2" />
                      {reposLoading ? (
                        <p className="text-xs text-gray-500 text-center py-2">Loading repos...</p>
                      ) : (
                        <div className="max-h-48 overflow-y-auto border border-gray-600 rounded">
                          {filteredRepos.length === 0 ? (
                            <p className="text-xs text-gray-500 text-center py-3">No repositories found</p>
                          ) : filteredRepos.map(repo => (
                            <button key={repo.full_name} type="button"
                              onClick={() => selectRepo(repo)}
                              className={`w-full text-left px-3 py-2 text-sm hover:bg-gray-700 flex items-center justify-between ${selectedRepo?.full_name === repo.full_name ? 'bg-blue-600/20 text-blue-300' : 'text-gray-300'}`}>
                              <span>{repo.full_name}</span>
                              {repo.private && <span className="text-xs text-gray-500 ml-2">Private</span>}
                            </button>
                          ))}
                        </div>
                      )}
                    </div>

                    {selectedRepo && (
                      <div>
                        <label className="block text-sm text-gray-300 mb-1">Branch</label>
                        <select value={selectedBranch} onChange={e => setSelectedBranch(e.target.value)}
                          className="w-full px-3 py-2 bg-gray-700 border border-gray-600 rounded text-white text-sm focus:outline-none focus:border-blue-500">
                          {branches.length > 0 ? branches.map(b => (
                            <option key={b} value={b}>{b}</option>
                          )) : (
                            <option value={selectedRepo.default_branch}>{selectedRepo.default_branch}</option>
                          )}
                        </select>
                      </div>
                    )}

                    <div className="flex gap-3">
                      <button type="submit" disabled={deploying || !selectedRepo}
                        className="bg-blue-600 hover:bg-blue-700 text-white px-4 py-2 rounded text-sm disabled:opacity-50">
                        {deploying ? 'Deploying...' : 'Deploy from GitHub'}
                      </button>
                      <button type="button" onClick={() => { setShowCreate(false); resetCreateForm(); }}
                        className="bg-gray-600 hover:bg-gray-700 text-white px-4 py-2 rounded text-sm">Cancel</button>
                    </div>
                  </form>
                )}
              </div>
            )}
          </div>
        </div>
      )}

      {/* ── Instances Tab ── */}
      {tab === 'instances' && (
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
              {instLoading ? (
                <tr><td colSpan="6" className="px-4 py-8 text-center text-gray-500">Loading...</td></tr>
              ) : instances.length === 0 ? (
                <tr><td colSpan="6" className="px-4 py-8 text-center text-gray-500">No instances. Launch one to get started.</td></tr>
              ) : instances.map((inst) => (
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
                      <button onClick={() => instanceAction(inst.id, 'start')} className="text-green-400 hover:text-green-300 text-xs">Start</button>
                    )}
                    {inst.state === 'running' && (
                      <>
                        <button onClick={() => instanceAction(inst.id, 'stop')} className="text-yellow-400 hover:text-yellow-300 text-xs">Stop</button>
                        <button onClick={() => instanceAction(inst.id, 'reboot')} className="text-blue-400 hover:text-blue-300 text-xs">Reboot</button>
                      </>
                    )}
                    {inst.state !== 'terminated' && (
                      <button onClick={() => { if (confirm('Terminate this instance?')) instanceAction(inst.id, 'terminate'); }}
                        className="text-red-400 hover:text-red-300 text-xs">Terminate</button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* ── Deployments Tab ── */}
      {tab === 'deployments' && (
        depLoading ? (
          <div className="text-gray-500 text-center py-8">Loading...</div>
        ) : deployments.length === 0 ? (
          <div className="bg-gray-800 rounded-lg p-12 text-center">
            <p className="text-gray-400 mb-2">No deployments yet</p>
            <p className="text-gray-500 text-sm">Upload a ZIP or deploy directly from a GitHub repository</p>
          </div>
        ) : (
          <div className="grid gap-4">
            {deployments.map(dep => (
              <div key={dep.id} className="bg-gray-800 rounded-lg p-4 hover:bg-gray-800/80 transition-colors">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-3 min-w-0">
                    <Link to={`/ec2/deploy/${dep.id}`} className="text-white font-medium hover:text-blue-400 transition-colors truncate">
                      {dep.name}
                    </Link>
                    <span className={`px-2 py-0.5 rounded text-xs font-medium shrink-0 ${DEPLOY_STATUS_COLORS[dep.status] || 'bg-gray-500/20 text-gray-400'}`}>
                      {dep.status === 'building' && <span className="inline-block w-2 h-2 bg-blue-400 rounded-full animate-pulse mr-1" />}
                      {dep.status}
                    </span>
                    {dep.project_label && (
                      <span className="text-xs text-gray-500 bg-gray-700 px-2 py-0.5 rounded shrink-0">{dep.project_label}</span>
                    )}
                    {dep.github_repo && (
                      <span className="text-xs text-gray-500 shrink-0">
                        <svg viewBox="0 0 24 24" className="w-3 h-3 fill-gray-500 inline mr-1"><path d="M12 0C5.374 0 0 5.373 0 12c0 5.302 3.438 9.8 8.207 11.387.599.111.793-.261.793-.577v-2.234c-3.338.726-4.033-1.416-4.033-1.416-.546-1.387-1.333-1.756-1.333-1.756-1.089-.745.083-.729.083-.729 1.205.084 1.839 1.237 1.839 1.237 1.07 1.834 2.807 1.304 3.492.997.107-.775.418-1.305.762-1.604-2.665-.305-5.467-1.334-5.467-5.931 0-1.311.469-2.381 1.236-3.221-.124-.303-.535-1.524.117-3.176 0 0 1.008-.322 3.301 1.23A11.509 11.509 0 0 1 12 5.803c1.02.005 2.047.138 3.006.404 2.291-1.552 3.297-1.23 3.297-1.23.653 1.653.242 2.874.118 3.176.77.84 1.235 1.911 1.235 3.221 0 4.609-2.807 5.624-5.479 5.929.43.372.823 1.102.823 2.222v3.293c0 .319.192.694.801.576C20.566 21.797 24 17.3 24 12c0-6.627-5.373-12-12-12z"/></svg>
                        {dep.github_repo}@{dep.github_branch}
                      </span>
                    )}
                  </div>
                  <div className="flex items-center gap-3 shrink-0 ml-3">
                    {dep.url && dep.status === 'running' && (
                      <a href={dep.url} target="_blank" rel="noreferrer"
                        className="text-xs text-blue-400 hover:text-blue-300 font-mono hidden lg:block">{dep.url}</a>
                    )}
                    {dep.tunnel_url && dep.status === 'running' && (
                      <a href={dep.tunnel_url} target="_blank" rel="noreferrer"
                        className="text-xs text-orange-400 hover:text-orange-300 hidden xl:block">tunnel</a>
                    )}
                    <Link to={`/ec2/deploy/${dep.id}`} className="text-gray-400 hover:text-white text-xs">Details</Link>
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
        )
      )}
    </div>
  );
}
