import { useState, useEffect, useRef } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import api from '../api';

export default function EC2Detail() {
  const { id } = useParams();
  const navigate = useNavigate();
  const [instance, setInstance] = useState(null);
  const [logs, setLogs] = useState('');
  const [tab, setTab] = useState('overview');

  // Upload state
  const [uploading, setUploading] = useState(false);
  const [uploadMsg, setUploadMsg] = useState(null);
  const [dragOver, setDragOver] = useState(false);
  const fileInputRef = useRef(null);

  // Copy link feedback
  const [copied, setCopied] = useState(null);

  // Tunnel state
  const [tunnelLoading, setTunnelLoading] = useState(false);
  const [tunnelError, setTunnelError] = useState(null);

  const fetchInstance = () =>
    api.get(`/ec2/instances/${id}`)
      .then(res => setInstance(res.data))
      .catch(() => navigate('/ec2'));

  useEffect(() => { fetchInstance(); }, [id]);

  useEffect(() => {
    if (tab === 'logs' && instance) {
      const fetchLogs = () => {
        api.get(`/ec2/instances/${id}/logs`)
          .then(res => setLogs(res.data.logs || ''))
          .catch(() => {});
      };
      fetchLogs();
      const interval = setInterval(fetchLogs, 5000);
      return () => clearInterval(interval);
    }
  }, [tab, id, instance]);

  const handleUpload = async (fileList) => {
    if (!fileList || fileList.length === 0) return;
    setUploading(true);
    setUploadMsg(null);
    const formData = new FormData();
    for (const file of fileList) {
      formData.append('files', file);
    }
    try {
      const res = await api.post(`/ec2/instances/${id}/upload`, formData, {
        headers: { 'Content-Type': 'multipart/form-data' },
      });
      setUploadMsg({ ok: true, text: res.data.detail });
    } catch (err) {
      setUploadMsg({ ok: false, text: err.response?.data?.detail || 'Upload failed' });
    } finally {
      setUploading(false);
    }
  };

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
      await fetchInstance(); // refresh to get tunnel_url
    } catch (err) {
      setTunnelError(err.response?.data?.detail || 'Failed to start tunnel');
    } finally {
      setTunnelLoading(false);
    }
  };

  const stopTunnel = async () => {
    setTunnelLoading(true);
    try {
      await api.delete(`/ec2/instances/${id}/tunnel/stop`);
      await fetchInstance();
    } catch (err) {
      setTunnelError(err.response?.data?.detail || 'Failed to stop tunnel');
    } finally {
      setTunnelLoading(false);
    }
  };

  if (!instance) return <div className="text-gray-400 p-8">Loading...</div>;

  const publicUrls = Object.entries(instance.public_urls || {});
  const tabs = ['overview', 'website', 'logs', 'monitoring'];

  return (
    <div>
      <div className="flex items-center gap-4 mb-6">
        <button onClick={() => navigate('/ec2')} className="text-gray-400 hover:text-white">&larr; Back</button>
        <h1 className="text-2xl font-bold text-white">{instance.name}</h1>
        <span className={`px-2 py-1 rounded text-xs font-medium ${
          instance.state === 'running' ? 'bg-green-500/20 text-green-400' :
          instance.state === 'stopped' ? 'bg-red-500/20 text-red-400' :
          'bg-gray-500/20 text-gray-400'
        }`}>{instance.state}</span>
      </div>

      <div className="flex gap-1 mb-4">
        {tabs.map(t => (
          <button key={t} onClick={() => setTab(t)}
            className={`px-4 py-2 text-sm rounded-t transition-colors ${
              tab === t ? 'bg-gray-800 text-white' : 'bg-gray-900 text-gray-400 hover:text-white'
            }`}>
            {t.charAt(0).toUpperCase() + t.slice(1)}
          </button>
        ))}
      </div>

      <div className="bg-gray-800 rounded-lg p-6">

        {/* Overview Tab */}
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
              'Created': new Date(instance.created_at).toLocaleString(),
            }).map(([k, v]) => (
              <div key={k}>
                <p className="text-xs text-gray-500">{k}</p>
                <p className="text-sm text-white font-mono">{v}</p>
              </div>
            ))}
          </div>
        )}

        {/* Website Tab */}
        {tab === 'website' && (
          <div className="space-y-6">

            {/* Shareable Links */}
            <div>
              <h3 className="text-sm font-semibold text-gray-300 mb-3 uppercase tracking-wide">Shareable Links</h3>
              {publicUrls.length === 0 ? (
                <p className="text-gray-500 text-sm">No ports exposed. Launch with port mappings like {"{"}"80": 0{"}"} to get a public URL.</p>
              ) : (
                <div className="space-y-2">
                  {publicUrls.map(([port, url]) => (
                    <div key={port} className="flex items-center gap-3 bg-gray-900 rounded-lg p-3">
                      <div className="flex-1">
                        <p className="text-xs text-gray-500 mb-0.5">Port {port}</p>
                        <a href={url} target="_blank" rel="noreferrer"
                          className="text-blue-400 hover:text-blue-300 text-sm font-mono break-all">{url}</a>
                      </div>
                      <button onClick={() => copyLink(url)}
                        className={`px-3 py-1.5 rounded text-xs transition-colors whitespace-nowrap ${
                          copied === url ? 'bg-green-600 text-white' : 'bg-gray-700 hover:bg-gray-600 text-gray-300'
                        }`}>
                        {copied === url ? 'Copied!' : 'Copy Link'}
                      </button>
                      <a href={url} target="_blank" rel="noreferrer"
                        className="px-3 py-1.5 bg-blue-600 hover:bg-blue-700 text-white rounded text-xs whitespace-nowrap">
                        Open
                      </a>
                    </div>
                  ))}
                </div>
              )}
            </div>

            {/* Cloudflare Tunnel */}
            <div>
              <h3 className="text-sm font-semibold text-gray-300 mb-3 uppercase tracking-wide">Cloudflare Tunnel (Public Internet)</h3>
              {instance.state !== 'running' ? (
                <p className="text-yellow-400 text-sm">Instance must be running to create a tunnel.</p>
              ) : instance.tunnel_url ? (
                <div className="space-y-3">
                  <div className="flex items-center gap-3 bg-gray-900 rounded-lg p-3 border border-orange-500/30">
                    <div className="flex-shrink-0 w-2 h-2 bg-orange-400 rounded-full animate-pulse" />
                    <div className="flex-1 min-w-0">
                      <p className="text-xs text-gray-500 mb-0.5">Public URL (anyone can open this)</p>
                      <a href={instance.tunnel_url} target="_blank" rel="noreferrer"
                        className="text-orange-400 hover:text-orange-300 text-sm font-mono break-all">
                        {instance.tunnel_url}
                      </a>
                    </div>
                    <button onClick={() => copyLink(instance.tunnel_url)}
                      className={`px-3 py-1.5 rounded text-xs transition-colors whitespace-nowrap ${
                        copied === instance.tunnel_url ? 'bg-green-600 text-white' : 'bg-gray-700 hover:bg-gray-600 text-gray-300'
                      }`}>
                      {copied === instance.tunnel_url ? 'Copied!' : 'Copy'}
                    </button>
                    <a href={instance.tunnel_url} target="_blank" rel="noreferrer"
                      className="px-3 py-1.5 bg-orange-600 hover:bg-orange-700 text-white rounded text-xs whitespace-nowrap">
                      Open
                    </a>
                  </div>
                  <button onClick={stopTunnel} disabled={tunnelLoading}
                    className="px-4 py-2 bg-red-700 hover:bg-red-600 text-white rounded text-sm disabled:opacity-50">
                    {tunnelLoading ? 'Stopping...' : 'Stop Tunnel'}
                  </button>
                </div>
              ) : (
                <div className="space-y-3">
                  <p className="text-gray-400 text-sm">
                    Generate a free <span className="text-orange-400 font-medium">trycloudflare.com</span> URL that anyone can use to visit your website — no account or domain needed.
                  </p>
                  <div className="flex items-start gap-3">
                    <button onClick={startTunnel} disabled={tunnelLoading || instance.state !== 'running'}
                      className="px-4 py-2 bg-orange-600 hover:bg-orange-500 text-white rounded text-sm font-medium disabled:opacity-50 whitespace-nowrap">
                      {tunnelLoading ? (
                        <span className="flex items-center gap-2">
                          <span className="w-3 h-3 border border-white border-t-transparent rounded-full animate-spin" />
                          Starting (~15s)...
                        </span>
                      ) : 'Start Cloudflare Tunnel'}
                    </button>
                    {!Object.keys(instance.port_mappings || {}).includes('80') && (
                      <p className="text-yellow-400 text-xs mt-2">
                        Tunnel requires port 80 to be mapped. Launch a new instance with port 80 exposed.
                      </p>
                    )}
                  </div>
                  {tunnelError && (
                    <p className="text-red-400 text-sm bg-red-900/20 border border-red-700 rounded p-2">{tunnelError}</p>
                  )}
                  <p className="text-xs text-gray-600">Requires <code className="bg-gray-900 px-1 rounded">cloudflared</code> installed on the server.</p>
                </div>
              )}
            </div>

            {/* File Upload */}
            <div>
              <h3 className="text-sm font-semibold text-gray-300 mb-3 uppercase tracking-wide">Upload Website Files</h3>
              {instance.state !== 'running' ? (
                <p className="text-yellow-400 text-sm">Instance must be running to upload files.</p>
              ) : (
                <>
                  <div
                    onDragOver={e => { e.preventDefault(); setDragOver(true); }}
                    onDragLeave={() => setDragOver(false)}
                    onDrop={e => {
                      e.preventDefault();
                      setDragOver(false);
                      handleUpload(e.dataTransfer.files);
                    }}
                    onClick={() => fileInputRef.current?.click()}
                    className={`border-2 border-dashed rounded-lg p-8 text-center cursor-pointer transition-colors ${
                      dragOver ? 'border-blue-400 bg-blue-900/20' : 'border-gray-600 hover:border-gray-400'
                    }`}
                  >
                    <input
                      ref={fileInputRef}
                      type="file"
                      multiple
                      className="hidden"
                      onChange={e => handleUpload(e.target.files)}
                    />
                    {uploading ? (
                      <div className="text-blue-400">
                        <div className="animate-spin w-8 h-8 border-2 border-blue-400 border-t-transparent rounded-full mx-auto mb-2" />
                        Uploading...
                      </div>
                    ) : (
                      <>
                        <p className="text-gray-300 text-sm font-medium">Drop files here or click to select</p>
                        <p className="text-gray-500 text-xs mt-1">HTML, CSS, JavaScript, images — select multiple files at once</p>
                      </>
                    )}
                  </div>

                  {uploadMsg && (
                    <div className={`mt-3 p-3 rounded text-sm ${
                      uploadMsg.ok ? 'bg-green-900/30 border border-green-700 text-green-300' : 'bg-red-900/30 border border-red-700 text-red-300'
                    }`}>
                      {uploadMsg.text}
                      {uploadMsg.ok && publicUrls.length > 0 && (
                        <span> — <a href={publicUrls[0][1]} target="_blank" rel="noreferrer" className="underline">View your site</a></span>
                      )}
                    </div>
                  )}

                  <p className="text-xs text-gray-500 mt-3">
                    Files are uploaded to <code className="bg-gray-900 px-1 rounded">/usr/share/nginx/html/</code> inside the container.
                    Make sure your main file is named <code className="bg-gray-900 px-1 rounded">index.html</code>.
                  </p>
                </>
              )}
            </div>
          </div>
        )}

        {/* Logs Tab */}
        {tab === 'logs' && (
          <pre className="text-sm text-green-400 bg-gray-900 p-4 rounded max-h-96 overflow-auto font-mono whitespace-pre-wrap">
            {logs || 'No logs available'}
          </pre>
        )}

        {/* Monitoring Tab */}
        {tab === 'monitoring' && (
          <p className="text-gray-400">Live stats available via CloudWatch → Per Instance metrics.</p>
        )}
      </div>
    </div>
  );
}
