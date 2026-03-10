import { useState, useEffect } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import api from '../api';

export default function EC2Detail() {
  const { id } = useParams();
  const navigate = useNavigate();
  const [instance, setInstance] = useState(null);
  const [logs, setLogs] = useState('');
  const [tab, setTab] = useState('overview');

  useEffect(() => {
    api.get(`/ec2/instances/${id}`)
      .then(res => setInstance(res.data))
      .catch(() => navigate('/ec2'));
  }, [id, navigate]);

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

  if (!instance) return <div className="text-gray-400">Loading...</div>;

  const tabs = ['overview', 'logs', 'monitoring'];

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
              'Port Mappings': JSON.stringify(instance.port_mappings || {}),
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

        {tab === 'logs' && (
          <pre className="text-sm text-green-400 bg-gray-900 p-4 rounded max-h-96 overflow-auto font-mono whitespace-pre-wrap">
            {logs || 'No logs available'}
          </pre>
        )}

        {tab === 'monitoring' && (
          <p className="text-gray-400">Monitoring will be available after CloudWatch is configured.</p>
        )}
      </div>
    </div>
  );
}
