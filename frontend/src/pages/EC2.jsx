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
  uploading: 'bg-yellow-500/20 text-yellow-400',
  queued: 'bg-yellow-500/20 text-yellow-400',
  building: 'bg-blue-500/20 text-blue-400',
  failed: 'bg-red-500/20 text-red-400',
};

const GH_ICON = (cls = 'w-4 h-4') => (
  <svg viewBox="0 0 24 24" className={`${cls} fill-current`}><path d="M12 0C5.374 0 0 5.373 0 12c0 5.302 3.438 9.8 8.207 11.387.599.111.793-.261.793-.577v-2.234c-3.338.726-4.033-1.416-4.033-1.416-.546-1.387-1.333-1.756-1.333-1.756-1.089-.745.083-.729.083-.729 1.205.084 1.839 1.237 1.839 1.237 1.07 1.834 2.807 1.304 3.492.997.107-.775.418-1.305.762-1.604-2.665-.305-5.467-1.334-5.467-5.931 0-1.311.469-2.381 1.236-3.221-.124-.303-.535-1.524.117-3.176 0 0 1.008-.322 3.301 1.23A11.509 11.509 0 0 1 12 5.803c1.02.005 2.047.138 3.006.404 2.291-1.552 3.297-1.23 3.297-1.23.653 1.653.242 2.874.118 3.176.77.84 1.235 1.911 1.235 3.221 0 4.609-2.807 5.624-5.479 5.929.43.372.823 1.102.823 2.222v3.293c0 .319.192.694.801.576C20.566 21.797 24 17.3 24 12c0-6.627-5.373-12-12-12z" /></svg>
);

export default function EC2() {
  const toast = useToast();
  const [searchParams, setSearchParams] = useSearchParams();

  // ── Data ─────────────────────────────────────────────────────────
  const [instances, setInstances] = useState([]);
  const [deployments, setDeployments] = useState([]);
  const [loading, setLoading] = useState(true);

  const fetchAll = () => {
    Promise.all([
      api.get('/ec2/instances').then(r => r.data).catch(() => []),
      api.get('/deploy/projects').then(r => r.data).catch(() => []),
    ]).then(([inst, dep]) => {
      setInstances(inst);
      setDeployments(dep);
    }).finally(() => setLoading(false));
  };

  // ── Launch Modal ─────────────────────────────────────────────────
  const [showLaunch, setShowLaunch] = useState(false);
  const [launchMode, setLaunchMode] = useState('github'); // 'github' | 'zip' | 'docker'

  // GitHub state
  const [githubStatus, setGithubStatus] = useState({ connected: false, login: null });
  const [githubRepos, setGithubRepos] = useState([]);
  const [repoSearch, setRepoSearch] = useState('');
  const [reposLoading, setReposLoading] = useState(false);
  const [branches, setBranches] = useState([]);
  const [selectedRepo, setSelectedRepo] = useState(null);
  const [selectedBranch, setSelectedBranch] = useState('');

  // ZIP state
  const [depName, setDepName] = useState('');
  const [zipFile, setZipFile] = useState(null);
  const fileRef = useRef(null);

  // Docker state
  const [form, setForm] = useState({
    name: '', image: 'nginx:alpine', instance_type: 't2.micro',
    port_mappings: '{"80": 0}', environment: '{}',
  });

  const [deploying, setDeploying] = useState(false);

  // ── GitHub helpers ───────────────────────────────────────────────
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
    } catch {} finally { setReposLoading(false); }
  };

  const connectGithub = async () => {
    try {
      const res = await api.get('/github/auth/url');
      window.location.href = res.data.url;
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Failed to connect GitHub');
    }
  };

  const disconnectGithub = async () => {
    try { await api.delete('/github/disconnect'); } catch {}
    setGithubStatus({ connected: false, login: null });
    setGithubRepos([]);
  };

  const selectRepo = async (repo) => {
    setSelectedRepo(repo);
    setSelectedBranch(repo.default_branch);
    setBranches([]);
    const [owner, name] = repo.full_name.split('/');
    try { const r = await api.get(`/github/repos/${owner}/${name}/branches`); setBranches(r.data); } catch {}
  };

  // ── Deploy / Launch actions ─────────────────────────────────────
  const handleGithubDeploy = async (e) => {
    e.preventDefault();
    if (!selectedRepo) { toast.error('Select a repository'); return; }
    setDeploying(true);
    const fd = new FormData();
    fd.append('name', depName || selectedRepo.name);
    fd.append('github_repo', selectedRepo.full_name);
    fd.append('github_branch', selectedBranch || selectedRepo.default_branch);
    try {
      await api.post('/deploy/projects/github', fd);
      closeLaunch();
      fetchAll();
      toast.success('GitHub deploy started!');
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Deploy failed');
    } finally { setDeploying(false); }
  };

  const handleZipDeploy = async (e) => {
    e.preventDefault();
    if (!zipFile) { toast.error('Select a ZIP file'); return; }
    setDeploying(true);
    const fd = new FormData();
    fd.append('name', depName);
    fd.append('file', zipFile);
    try {
      await api.post('/deploy/projects', fd);
      closeLaunch();
      fetchAll();
      toast.success('Deploy started! Building...');
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Deploy failed');
    } finally { setDeploying(false); }
  };

  const handleDockerLaunch = async (e) => {
    e.preventDefault();
    try {
      await api.post('/ec2/instances', {
        ...form,
        port_mappings: JSON.parse(form.port_mappings || '{}'),
        environment: JSON.parse(form.environment || '{}'),
        vpc_id: null, command: null,
      });
      closeLaunch();
      fetchAll();
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Launch failed');
    }
  };

  const closeLaunch = () => {
    setShowLaunch(false);
    setDepName('');
    setZipFile(null);
    setSelectedRepo(null);
    setSelectedBranch('');
    setBranches([]);
    setRepoSearch('');
    setLaunchMode('github');
    setForm({ name: '', image: 'nginx:alpine', instance_type: 't2.micro', port_mappings: '{"80": 0}', environment: '{}' });
  };

  const instanceAction = async (id, act) => {
    try {
      if (act === 'terminate') await api.delete(`/ec2/instances/${id}`);
      else await api.post(`/ec2/instances/${id}/${act}`);
      fetchAll();
    } catch (err) { toast.error(err.response?.data?.detail || `Failed to ${act}`); }
  };

  const deleteDeployment = async (id) => {
    if (!confirm('Delete this deployment?')) return;
    try { await api.delete(`/deploy/projects/${id}`); fetchAll(); }
    catch (err) { toast.error(err.response?.data?.detail || 'Delete failed'); }
  };

  // ── Effects ─────────────────────────────────────────────────────
  useEffect(() => {
    fetchAll();
    fetchGithubStatus();
    if (searchParams.get('github') === 'connected') {
      toast.success('GitHub connected!');
      setSearchParams({});
    }
    const interval = setInterval(fetchAll, 5000);
    return () => clearInterval(interval);
  }, []);

  const filteredRepos = githubRepos.filter(r =>
    r.full_name.toLowerCase().includes(repoSearch.toLowerCase())
  );

  // ── Unified list ────────────────────────────────────────────────
  const allItems = [
    ...instances.map(i => ({ ...i, _kind: 'instance', _time: i.created_at })),
    ...deployments.map(d => ({ ...d, _kind: 'deploy', _time: d.created_at, state: d.status })),
  ].sort((a, b) => new Date(b._time) - new Date(a._time));

  const INPUT = "w-full px-3 py-2 bg-gray-700 border border-gray-600 rounded text-white text-sm focus:outline-none focus:border-blue-500";

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-bold text-white">EC2 Instances</h1>
        <button onClick={() => setShowLaunch(true)}
          className="bg-blue-600 hover:bg-blue-700 text-white px-4 py-2 rounded text-sm">
          Launch Instance
        </button>
      </div>

      {/* ── Launch Modal ── */}
      {showLaunch && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
          <div className="bg-gray-800 rounded-lg p-6 w-full max-w-lg mx-4 max-h-[90vh] overflow-y-auto">
            <h2 className="text-lg font-semibold text-white mb-4">Launch Instance</h2>

            {/* Source tabs */}
            <div className="flex gap-1 mb-4 border-b border-gray-700">
              {[['github', 'GitHub Repo'], ['zip', 'Upload ZIP'], ['docker', 'Docker Image']].map(([key, label]) => (
                <button key={key} onClick={() => setLaunchMode(key)}
                  className={`px-3 py-1.5 text-sm transition-colors ${launchMode === key ? 'text-blue-400 border-b-2 border-blue-400' : 'text-gray-400 hover:text-white'}`}>
                  {label}
                </button>
              ))}
            </div>

            {/* ── GitHub ── */}
            {launchMode === 'github' && (
              <div className="space-y-4">
                {!githubStatus.connected ? (
                  <div className="text-center py-6">
                    <p className="text-gray-400 mb-1">Connect your GitHub account</p>
                    <p className="text-gray-500 text-xs mb-4">to deploy directly from any repository</p>
                    <button onClick={connectGithub}
                      className="bg-gray-700 hover:bg-gray-600 text-white px-4 py-2 rounded text-sm flex items-center gap-2 mx-auto">
                      {GH_ICON()} Connect GitHub
                    </button>
                  </div>
                ) : (
                  <form onSubmit={handleGithubDeploy} className="space-y-4">
                    <div className="flex items-center justify-between bg-gray-900 rounded p-2 px-3">
                      <div className="flex items-center gap-2">
                        {GH_ICON('w-4 h-4 text-white')}
                        <span className="text-sm text-gray-300">Connected as <span className="text-white font-medium">{githubStatus.login}</span></span>
                      </div>
                      <button type="button" onClick={disconnectGithub} className="text-xs text-gray-500 hover:text-red-400">Disconnect</button>
                    </div>

                    <div>
                      <label className="block text-sm text-gray-300 mb-1">Name</label>
                      <input value={depName} onChange={e => setDepName(e.target.value)}
                        placeholder={selectedRepo ? selectedRepo.name : 'auto from repo'} className={INPUT} />
                    </div>

                    <div>
                      <label className="block text-sm text-gray-300 mb-1">Repository</label>
                      <input value={repoSearch} onChange={e => setRepoSearch(e.target.value)}
                        placeholder="Search repositories..." className={`${INPUT} mb-2`} />
                      {reposLoading ? (
                        <p className="text-xs text-gray-500 text-center py-2">Loading repos...</p>
                      ) : (
                        <div className="max-h-48 overflow-y-auto border border-gray-600 rounded">
                          {filteredRepos.length === 0 ? (
                            <p className="text-xs text-gray-500 text-center py-3">No repositories found</p>
                          ) : filteredRepos.map(repo => (
                            <button key={repo.full_name} type="button" onClick={() => selectRepo(repo)}
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
                        <select value={selectedBranch} onChange={e => setSelectedBranch(e.target.value)} className={INPUT}>
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
                        {deploying ? 'Deploying...' : 'Deploy'}
                      </button>
                      <button type="button" onClick={closeLaunch}
                        className="bg-gray-600 hover:bg-gray-700 text-white px-4 py-2 rounded text-sm">Cancel</button>
                    </div>
                  </form>
                )}
              </div>
            )}

            {/* ── ZIP Upload ── */}
            {launchMode === 'zip' && (
              <form onSubmit={handleZipDeploy} className="space-y-4">
                <div>
                  <label className="block text-sm text-gray-300 mb-1">Project Name</label>
                  <input value={depName} onChange={e => setDepName(e.target.value)} required placeholder="my-website" className={INPUT} />
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
                  <p className="text-xs text-gray-500">React, Next.js, Vue, Angular, Svelte, Node.js, Python, Static HTML, Dockerfile</p>
                </div>
                <div className="flex gap-3">
                  <button type="submit" disabled={deploying}
                    className="bg-blue-600 hover:bg-blue-700 text-white px-4 py-2 rounded text-sm disabled:opacity-50">
                    {deploying ? 'Uploading...' : 'Deploy'}
                  </button>
                  <button type="button" onClick={closeLaunch}
                    className="bg-gray-600 hover:bg-gray-700 text-white px-4 py-2 rounded text-sm">Cancel</button>
                </div>
              </form>
            )}

            {/* ── Docker Image ── */}
            {launchMode === 'docker' && (
              <form onSubmit={handleDockerLaunch} className="space-y-3">
                <div>
                  <label className="block text-sm text-gray-300 mb-1">Name</label>
                  <input value={form.name} onChange={e => setForm({...form, name: e.target.value})} required className={INPUT} />
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
                </div>
                <div>
                  <label className="block text-sm text-gray-300 mb-1">Environment (JSON)</label>
                  <input value={form.environment} onChange={e => setForm({...form, environment: e.target.value})} className={`${INPUT} font-mono`} placeholder='{"KEY": "value"}' />
                </div>
                <div className="flex gap-3 pt-2">
                  <button type="submit" className="bg-blue-600 hover:bg-blue-700 text-white px-4 py-2 rounded text-sm">Launch</button>
                  <button type="button" onClick={closeLaunch} className="bg-gray-600 hover:bg-gray-700 text-white px-4 py-2 rounded text-sm">Cancel</button>
                </div>
              </form>
            )}
          </div>
        </div>
      )}

      {/* ── Unified List ── */}
      {loading ? (
        <div className="text-gray-500 text-center py-8">Loading...</div>
      ) : allItems.length === 0 ? (
        <div className="bg-gray-800 rounded-lg p-12 text-center">
          <p className="text-gray-400 mb-2">No instances yet</p>
          <p className="text-gray-500 text-sm">Deploy from GitHub, upload a ZIP, or launch a Docker container</p>
        </div>
      ) : (
        <div className="bg-gray-800 rounded-lg overflow-hidden">
          <table className="w-full">
            <thead>
              <tr className="border-b border-gray-700">
                <th className="text-left px-4 py-3 text-sm font-medium text-gray-400">Name</th>
                <th className="text-left px-4 py-3 text-sm font-medium text-gray-400">Type</th>
                <th className="text-left px-4 py-3 text-sm font-medium text-gray-400">State</th>
                <th className="text-left px-4 py-3 text-sm font-medium text-gray-400">Source</th>
                <th className="text-left px-4 py-3 text-sm font-medium text-gray-400">Actions</th>
              </tr>
            </thead>
            <tbody>
              {allItems.map(item => item._kind === 'instance' ? (
                /* ── Instance Row ── */
                <tr key={`i-${item.id}`} className="border-b border-gray-700/50 hover:bg-gray-700/30">
                  <td className="px-4 py-3 text-sm">
                    <Link to={`/ec2/${item.id}`} className="text-blue-400 hover:underline">{item.name}</Link>
                  </td>
                  <td className="px-4 py-3 text-sm">
                    <span className="text-xs bg-gray-700 text-gray-300 px-2 py-0.5 rounded">Docker</span>
                  </td>
                  <td className="px-4 py-3 text-sm">
                    <span className={`px-2 py-0.5 rounded text-xs font-medium ${STATE_COLORS[item.state] || 'text-gray-400'}`}>
                      {item.state}
                    </span>
                  </td>
                  <td className="px-4 py-3 text-sm text-gray-400 font-mono text-xs">{item.image}</td>
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
              ) : (
                /* ── Deployment Row ── */
                <tr key={`d-${item.id}`} className="border-b border-gray-700/50 hover:bg-gray-700/30">
                  <td className="px-4 py-3 text-sm">
                    <Link to={`/ec2/deploy/${item.id}`} className="text-blue-400 hover:underline">{item.name}</Link>
                  </td>
                  <td className="px-4 py-3 text-sm">
                    <span className="text-xs bg-purple-900/40 text-purple-300 px-2 py-0.5 rounded">
                      {item.project_label || 'Deploy'}
                    </span>
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
                    ) : (
                      <span className="text-gray-500">ZIP upload</span>
                    )}
                  </td>
                  <td className="px-4 py-3 text-sm space-x-2">
                    {item.url && item.state === 'running' && (
                      <a href={item.url} target="_blank" rel="noreferrer" className="text-green-400 hover:text-green-300 text-xs">Open</a>
                    )}
                    <Link to={`/ec2/deploy/${item.id}`} className="text-gray-400 hover:text-white text-xs">Details</Link>
                    <button onClick={() => deleteDeployment(item.id)} className="text-red-400 hover:text-red-300 text-xs">Delete</button>
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
