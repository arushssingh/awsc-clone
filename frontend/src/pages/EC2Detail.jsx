import { useState, useEffect, useRef } from 'react';
import { useParams, useNavigate, useSearchParams } from 'react-router-dom';
import api from '../api';
import { useToast } from '../components/Toast';

const GH_ICON = (cls = 'w-4 h-4') => (
  <svg viewBox="0 0 24 24" className={`${cls} fill-current`}><path d="M12 0C5.374 0 0 5.373 0 12c0 5.302 3.438 9.8 8.207 11.387.599.111.793-.261.793-.577v-2.234c-3.338.726-4.033-1.416-4.033-1.416-.546-1.387-1.333-1.756-1.333-1.756-1.089-.745.083-.729.083-.729 1.205.084 1.839 1.237 1.839 1.237 1.07 1.834 2.807 1.304 3.492.997.107-.775.418-1.305.762-1.604-2.665-.305-5.467-1.334-5.467-5.931 0-1.311.469-2.381 1.236-3.221-.124-.303-.535-1.524.117-3.176 0 0 1.008-.322 3.301 1.23A11.509 11.509 0 0 1 12 5.803c1.02.005 2.047.138 3.006.404 2.291-1.552 3.297-1.23 3.297-1.23.653 1.653.242 2.874.118 3.176.77.84 1.235 1.911 1.235 3.221 0 4.609-2.807 5.624-5.479 5.929.43.372.823 1.102.823 2.222v3.293c0 .319.192.694.801.576C20.566 21.797 24 17.3 24 12c0-6.627-5.373-12-12-12z" /></svg>
);

const STATE_COLORS = {
  running: 'bg-green-500/20 text-green-400',
  building: 'bg-blue-500/20 text-blue-400',
  stopped: 'bg-red-500/20 text-red-400',
  failed: 'bg-red-500/20 text-red-400',
};

function formatSize(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export default function EC2Detail() {
  const { id } = useParams();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const toast = useToast();

  const [instance, setInstance] = useState(null);
  const [logs, setLogs] = useState('');
  const [buildLog, setBuildLog] = useState('');
  const [files, setFiles] = useState([]);
  const [filesLoading, setFilesLoading] = useState(false);
  const [tab, setTab] = useState('overview');

  // Copy link
  const [copied, setCopied] = useState(null);

  // Tunnel
  const [tunnelLoading, setTunnelLoading] = useState(false);
  const [tunnelError, setTunnelError] = useState(null);

  // Deploy tab
  const [deployMode, setDeployMode] = useState('github');
  const [deploying, setDeploying] = useState(false);

  // GitHub
  const [githubStatus, setGithubStatus] = useState({ connected: false, login: null });
  const [githubRepos, setGithubRepos] = useState([]);
  const [repoSearch, setRepoSearch] = useState('');
  const [reposLoading, setReposLoading] = useState(false);
  const [branches, setBranches] = useState([]);
  const [selectedRepo, setSelectedRepo] = useState(null);
  const [selectedBranch, setSelectedBranch] = useState('');

  // ZIP
  const [zipFile, setZipFile] = useState(null);
  const fileRef = useRef(null);

  const fetchInstance = () =>
    api.get(`/ec2/instances/${id}`)
      .then(res => setInstance(res.data))
      .catch(() => navigate('/ec2'));

  const fetchFiles = async () => {
    setFilesLoading(true);
    try {
      const res = await api.get(`/ec2/instances/${id}/files`);
      setFiles(res.data);
    } catch {} finally { setFilesLoading(false); }
  };

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
    setSelectedRepo(null);
  };

  const selectRepo = async (repo) => {
    setSelectedRepo(repo);
    setSelectedBranch(repo.default_branch);
    setBranches([]);
    const [owner, name] = repo.full_name.split('/');
    try { const r = await api.get(`/github/repos/${owner}/${name}/branches`); setBranches(r.data); } catch {}
  };

  // Deploy GitHub into THIS instance
  const handleGithubDeploy = async (e) => {
    e.preventDefault();
    if (!selectedRepo) { toast.error('Select a repository'); return; }
    setDeploying(true);
    const fd = new FormData();
    fd.append('github_repo', selectedRepo.full_name);
    fd.append('github_branch', selectedBranch || selectedRepo.default_branch);
    try {
      await api.post(`/ec2/instances/${id}/deploy/github`, fd);
      toast.success('Build started! Check Build Log tab.');
      setTab('buildlog');
      fetchInstance();
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Deploy failed');
    } finally { setDeploying(false); }
  };

  // Deploy ZIP into THIS instance
  const handleZipDeploy = async (e) => {
    e.preventDefault();
    if (!zipFile) { toast.error('Select a ZIP file'); return; }
    setDeploying(true);
    const fd = new FormData();
    fd.append('file', zipFile);
    try {
      await api.post(`/ec2/instances/${id}/deploy/zip`, fd);
      toast.success('Build started! Check Build Log tab.');
      setTab('buildlog');
      fetchInstance();
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Deploy failed');
    } finally { setDeploying(false); }
  };

  // Initial load
  useEffect(() => {
    fetchInstance();
    fetchGithubStatus();
    if (searchParams.get('github') === 'connected') {
      toast.success('GitHub connected!');
      setSearchParams({});
      setTab('deploy');
    }
  }, [id]);

  // Auto-refresh instance while building
  useEffect(() => {
    if (instance && (instance.state === 'building')) {
      const interval = setInterval(fetchInstance, 3000);
      return () => clearInterval(interval);
    }
  }, [instance?.state]);

  // Fetch logs when on log tabs
  useEffect(() => {
    if (tab === 'logs' && instance) {
      const fetchLogs = () => {
        api.get(`/ec2/instances/${id}/logs`)
          .then(res => { setLogs(res.data.logs || ''); setBuildLog(res.data.build_log || ''); })
          .catch(() => {});
      };
      fetchLogs();
      const interval = setInterval(fetchLogs, 5000);
      return () => clearInterval(interval);
    }
    if (tab === 'buildlog' && instance) {
      const fetchLogs = () => {
        api.get(`/ec2/instances/${id}/logs`)
          .then(res => setBuildLog(res.data.build_log || ''))
          .catch(() => {});
      };
      fetchLogs();
      const interval = setInterval(fetchLogs, 3000);
      return () => clearInterval(interval);
    }
  }, [tab, id, instance?.state]);

  // Fetch files when on files tab
  useEffect(() => {
    if (tab === 'files') fetchFiles();
  }, [tab]);

  const copyLink = (url) => {
    navigator.clipboard.writeText(url);
    setCopied(url);
    setTimeout(() => setCopied(null), 2000);
  };

  const startTunnel = async () => {
    setTunnelLoading(true);
    setTunnelError(null);
    try {
      await api.post(`/ec2/instances/${id}/tunnel/start`);
      await fetchInstance();
    } catch (err) {
      setTunnelError(err.response?.data?.detail || 'Failed to start tunnel');
    } finally { setTunnelLoading(false); }
  };

  const stopTunnel = async () => {
    setTunnelLoading(true);
    try {
      await api.delete(`/ec2/instances/${id}/tunnel/stop`);
      await fetchInstance();
    } catch (err) {
      setTunnelError(err.response?.data?.detail || 'Failed to stop tunnel');
    } finally { setTunnelLoading(false); }
  };

  if (!instance) return <div className="text-gray-400 p-8">Loading...</div>;

  const publicUrls = Object.entries(instance.public_urls || {});
  const allTabs = ['overview', 'website', 'deploy', 'files', 'buildlog', 'logs'];
  const tabLabels = { overview: 'Overview', website: 'Website', deploy: 'Deploy', files: 'Files', buildlog: 'Build Log', logs: 'Runtime Log' };
  const INPUT = "w-full px-3 py-2 bg-gray-700 border border-gray-600 rounded text-white text-sm focus:outline-none focus:border-blue-500";
  const filteredRepos = githubRepos.filter(r =>
    r.full_name.toLowerCase().includes(repoSearch.toLowerCase())
  );

  return (
    <div>
      <div className="flex items-center gap-4 mb-6">
        <button onClick={() => navigate('/ec2')} className="text-gray-400 hover:text-white">&larr; Back</button>
        <h1 className="text-2xl font-bold text-white">{instance.name}</h1>
        <span className={`px-2 py-1 rounded text-xs font-medium ${STATE_COLORS[instance.state] || 'bg-gray-500/20 text-gray-400'}`}>
          {instance.state === 'building' && <span className="inline-block w-2 h-2 bg-blue-400 rounded-full animate-pulse mr-1" />}
          {instance.state}
        </span>
        {instance.project_label && (
          <span className="text-xs bg-purple-900/40 text-purple-300 px-2 py-0.5 rounded">{instance.project_label}</span>
        )}
        {instance.github_repo && (
          <span className="flex items-center gap-1 text-xs text-gray-400">
            {GH_ICON('w-3 h-3 text-gray-500')}
            {instance.github_repo}
          </span>
        )}
      </div>

      <div className="flex gap-1 mb-4 overflow-x-auto">
        {allTabs.map(t => (
          <button key={t} onClick={() => setTab(t)}
            className={`px-4 py-2 text-sm rounded-t transition-colors whitespace-nowrap ${
              tab === t ? 'bg-gray-800 text-white' : 'bg-gray-900 text-gray-400 hover:text-white'
            }`}>
            {tabLabels[t]}
          </button>
        ))}
      </div>

      <div className="bg-gray-800 rounded-lg p-6">

        {/* ── Overview ── */}
        {tab === 'overview' && (
          <div className="grid grid-cols-2 gap-4">
            {Object.entries({
              'Instance ID': instance.id,
              'Image': instance.image,
              'Type': instance.instance_type,
              'State': instance.state,
              'Private IP': instance.private_ip || '--',
              'CPU Limit': `${instance.cpu_limit} cores`,
              'Memory Limit': `${instance.memory_limit} MB`,
              'VPC ID': instance.vpc_id || '--',
              ...(instance.github_repo ? { 'GitHub Repo': instance.github_repo, 'Branch': instance.github_branch } : {}),
              ...(instance.project_label ? { 'Project Type': instance.project_label } : {}),
              'Created': new Date(instance.created_at).toLocaleString(),
            }).map(([k, v]) => (
              <div key={k}>
                <p className="text-xs text-gray-500">{k}</p>
                <p className="text-sm text-white font-mono">{v}</p>
              </div>
            ))}
          </div>
        )}

        {/* ── Website ── */}
        {tab === 'website' && (
          <div className="space-y-6">
            <div>
              <h3 className="text-sm font-semibold text-gray-300 mb-3 uppercase tracking-wide">Shareable Links</h3>
              {instance.instance_url && (
                <div className="flex items-center gap-3 bg-gray-900 rounded-lg p-3 mb-2 border border-green-700/30">
                  <div className="flex-1">
                    <p className="text-xs text-green-500 mb-0.5">Website URL</p>
                    <a href={instance.instance_url} target="_blank" rel="noreferrer"
                      className="text-green-400 hover:text-green-300 text-sm font-mono break-all">{instance.instance_url}</a>
                  </div>
                  <button onClick={() => copyLink(instance.instance_url)}
                    className={`px-3 py-1.5 rounded text-xs whitespace-nowrap ${
                      copied === instance.instance_url ? 'bg-green-600 text-white' : 'bg-gray-700 hover:bg-gray-600 text-gray-300'
                    }`}>
                    {copied === instance.instance_url ? 'Copied!' : 'Copy'}
                  </button>
                  <a href={instance.instance_url} target="_blank" rel="noreferrer"
                    className="px-3 py-1.5 bg-green-600 hover:bg-green-700 text-white rounded text-xs whitespace-nowrap">Open</a>
                </div>
              )}
              {publicUrls.length === 0 && !instance.instance_url ? (
                <p className="text-gray-500 text-sm">No ports exposed.</p>
              ) : publicUrls.length > 0 && (
                <div className="space-y-2">
                  {publicUrls.map(([port, url]) => (
                    <div key={port} className="flex items-center gap-3 bg-gray-900 rounded-lg p-3">
                      <div className="flex-1">
                        <p className="text-xs text-gray-500 mb-0.5">Port {port} (direct)</p>
                        <a href={url} target="_blank" rel="noreferrer"
                          className="text-blue-400 hover:text-blue-300 text-sm font-mono break-all">{url}</a>
                      </div>
                      <button onClick={() => copyLink(url)}
                        className={`px-3 py-1.5 rounded text-xs whitespace-nowrap ${
                          copied === url ? 'bg-green-600 text-white' : 'bg-gray-700 hover:bg-gray-600 text-gray-300'
                        }`}>
                        {copied === url ? 'Copied!' : 'Copy'}
                      </button>
                      <a href={url} target="_blank" rel="noreferrer"
                        className="px-3 py-1.5 bg-blue-600 hover:bg-blue-700 text-white rounded text-xs whitespace-nowrap">Open</a>
                    </div>
                  ))}
                </div>
              )}
            </div>

            {/* Tunnel */}
            <div>
              <h3 className="text-sm font-semibold text-gray-300 mb-3 uppercase tracking-wide">Cloudflare Tunnel</h3>
              {instance.state !== 'running' ? (
                <p className="text-yellow-400 text-sm">Instance must be running to start a tunnel.</p>
              ) : instance.tunnel_url ? (
                <div className="space-y-3">
                  <div className="flex items-center gap-3 bg-gray-900 rounded-lg p-3 border border-orange-500/30">
                    <div className="w-2 h-2 bg-orange-400 rounded-full animate-pulse" />
                    <div className="flex-1 min-w-0">
                      <p className="text-xs text-gray-500 mb-0.5">Public URL</p>
                      <a href={instance.tunnel_url} target="_blank" rel="noreferrer"
                        className="text-orange-400 hover:text-orange-300 text-sm font-mono break-all">{instance.tunnel_url}</a>
                    </div>
                    <button onClick={() => copyLink(instance.tunnel_url)}
                      className={`px-3 py-1.5 rounded text-xs whitespace-nowrap ${
                        copied === instance.tunnel_url ? 'bg-green-600 text-white' : 'bg-gray-700 hover:bg-gray-600 text-gray-300'
                      }`}>
                      {copied === instance.tunnel_url ? 'Copied!' : 'Copy'}
                    </button>
                    <a href={instance.tunnel_url} target="_blank" rel="noreferrer"
                      className="px-3 py-1.5 bg-orange-600 hover:bg-orange-700 text-white rounded text-xs whitespace-nowrap">Open</a>
                  </div>
                  <div className="flex gap-2">
                    <button onClick={startTunnel} disabled={tunnelLoading}
                      className="px-4 py-2 bg-orange-600 hover:bg-orange-500 text-white rounded text-sm disabled:opacity-50">
                      {tunnelLoading ? 'Generating...' : 'New URL'}
                    </button>
                    <button onClick={stopTunnel} disabled={tunnelLoading}
                      className="px-4 py-2 bg-red-700 hover:bg-red-600 text-white rounded text-sm disabled:opacity-50">Stop Tunnel</button>
                  </div>
                </div>
              ) : (
                <div className="space-y-3">
                  <p className="text-gray-400 text-sm">
                    Generate a free <span className="text-orange-400 font-medium">trycloudflare.com</span> URL.
                  </p>
                  <button onClick={startTunnel} disabled={tunnelLoading}
                    className="px-4 py-2 bg-orange-600 hover:bg-orange-500 text-white rounded text-sm font-medium disabled:opacity-50">
                    {tunnelLoading ? (
                      <span className="flex items-center gap-2">
                        <span className="w-3 h-3 border border-white border-t-transparent rounded-full animate-spin" />
                        Starting (~15s)...
                      </span>
                    ) : 'Start Cloudflare Tunnel'}
                  </button>
                  {tunnelError && (
                    <p className="text-red-400 text-sm bg-red-900/20 border border-red-700 rounded p-2">{tunnelError}</p>
                  )}
                </div>
              )}
            </div>
          </div>
        )}

        {/* ── Deploy ── */}
        {tab === 'deploy' && (
          <div className="space-y-6 max-w-lg">
            <div>
              <h3 className="text-sm font-semibold text-gray-300 mb-1 uppercase tracking-wide">Deploy Code</h3>
              <p className="text-xs text-gray-500 mb-4">Build and deploy code into this instance. Replaces the current container.</p>
            </div>

            {instance.github_repo && (
              <div className="bg-green-900/20 border border-green-700/40 rounded p-3 text-sm text-green-300 mb-4">
                Currently deployed from <span className="font-mono font-medium">{instance.github_repo}</span> ({instance.github_branch}).
                Auto-redeploys on push.
              </div>
            )}

            <div className="flex gap-1 border-b border-gray-700 mb-4">
              {[['github', 'GitHub Repo'], ['zip', 'Upload ZIP']].map(([key, label]) => (
                <button key={key} onClick={() => setDeployMode(key)}
                  className={`px-4 py-2 text-sm transition-colors ${deployMode === key ? 'text-blue-400 border-b-2 border-blue-400' : 'text-gray-400 hover:text-white'}`}>
                  {key === 'github' && GH_ICON('w-4 h-4 inline mr-1')}{label}
                </button>
              ))}
            </div>

            {/* GitHub */}
            {deployMode === 'github' && (
              <div>
                {!githubStatus.connected ? (
                  <div className="text-center py-8 border border-gray-700 rounded-lg">
                    <div className="flex justify-center mb-3 text-gray-500">{GH_ICON('w-10 h-10')}</div>
                    <p className="text-gray-300 font-medium mb-1">Connect GitHub</p>
                    <p className="text-gray-500 text-xs mb-4">Authorize access to your repositories</p>
                    <button onClick={connectGithub}
                      className="bg-gray-700 hover:bg-gray-600 text-white px-5 py-2 rounded text-sm flex items-center gap-2 mx-auto">
                      {GH_ICON()} Connect GitHub Account
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
                      <label className="block text-sm text-gray-300 mb-1">Repository</label>
                      <input value={repoSearch} onChange={e => setRepoSearch(e.target.value)}
                        placeholder="Search repositories..." className={`${INPUT} mb-2`} />
                      {reposLoading ? (
                        <p className="text-xs text-gray-500 text-center py-3">Loading repos...</p>
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

                    <button type="submit" disabled={deploying || !selectedRepo}
                      className="bg-blue-600 hover:bg-blue-700 text-white px-5 py-2 rounded text-sm disabled:opacity-50">
                      {deploying ? 'Deploying...' : 'Deploy to this Instance'}
                    </button>
                  </form>
                )}
              </div>
            )}

            {/* ZIP */}
            {deployMode === 'zip' && (
              <form onSubmit={handleZipDeploy} className="space-y-4">
                <div>
                  <label className="block text-sm text-gray-300 mb-1">Project ZIP</label>
                  <div onClick={() => fileRef.current?.click()}
                    className="border-2 border-dashed border-gray-600 hover:border-gray-400 rounded-lg p-6 text-center cursor-pointer transition-colors">
                    <input ref={fileRef} type="file" accept=".zip" className="hidden"
                      onChange={e => setZipFile(e.target.files[0])} />
                    {zipFile ? (
                      <p className="text-sm text-green-400">{zipFile.name} ({(zipFile.size / 1024 / 1024).toFixed(1)} MB)</p>
                    ) : (
                      <>
                        <p className="text-sm text-gray-400 mb-1">Click to select ZIP file</p>
                        <p className="text-xs text-gray-500">React, Vue, Next.js, Python, Static HTML, or Dockerfile</p>
                      </>
                    )}
                  </div>
                </div>
                <button type="submit" disabled={deploying || !zipFile}
                  className="bg-blue-600 hover:bg-blue-700 text-white px-5 py-2 rounded text-sm disabled:opacity-50">
                  {deploying ? 'Uploading...' : 'Deploy to this Instance'}
                </button>
              </form>
            )}
          </div>
        )}

        {/* ── Files ── */}
        {tab === 'files' && (
          <div>
            <h3 className="text-sm font-semibold text-gray-300 mb-3 uppercase tracking-wide">Project Source Files</h3>
            {filesLoading ? (
              <p className="text-gray-500 text-sm">Loading...</p>
            ) : files.length === 0 ? (
              <p className="text-gray-500 text-sm">No project files. Deploy code first using the Deploy tab.</p>
            ) : (
              <div className="overflow-auto max-h-96 border border-gray-700 rounded">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b border-gray-700 bg-gray-900">
                      <th className="text-left px-3 py-2 text-gray-400 font-medium">Path</th>
                      <th className="text-right px-3 py-2 text-gray-400 font-medium w-24">Size</th>
                    </tr>
                  </thead>
                  <tbody>
                    {files.map(f => (
                      <tr key={f.path} className="border-b border-gray-700/50 hover:bg-gray-700/20">
                        <td className="px-3 py-1.5 text-gray-300 font-mono text-xs">{f.path}</td>
                        <td className="px-3 py-1.5 text-gray-500 text-xs text-right">{formatSize(f.size)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        )}

        {/* ── Build Log ── */}
        {tab === 'buildlog' && (
          <div>
            <div className="flex items-center justify-between mb-3">
              <h3 className="text-sm font-semibold text-gray-300 uppercase tracking-wide">Build Log</h3>
              {instance.state === 'building' && (
                <span className="flex items-center gap-2 text-xs text-blue-400">
                  <span className="w-2 h-2 bg-blue-400 rounded-full animate-pulse" />
                  Building...
                </span>
              )}
            </div>
            <pre className="text-sm text-yellow-300 bg-gray-900 p-4 rounded max-h-96 overflow-auto font-mono whitespace-pre-wrap">
              {instance.build_log || buildLog || 'No build log yet. Deploy code first.'}
            </pre>
          </div>
        )}

        {/* ── Runtime Log ── */}
        {tab === 'logs' && (
          <div>
            <h3 className="text-sm font-semibold text-gray-300 mb-3 uppercase tracking-wide">Runtime Log</h3>
            <pre className="text-sm text-green-400 bg-gray-900 p-4 rounded max-h-96 overflow-auto font-mono whitespace-pre-wrap">
              {logs || 'No logs available'}
            </pre>
          </div>
        )}
      </div>
    </div>
  );
}
