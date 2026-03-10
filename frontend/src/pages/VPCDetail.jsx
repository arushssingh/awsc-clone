import { useState, useEffect } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import api from '../api';

export default function VPCDetail() {
  const { id } = useParams();
  const navigate = useNavigate();
  const [vpc, setVpc] = useState(null);

  useEffect(() => {
    api.get(`/vpc/vpcs/${id}`)
      .then(res => setVpc(res.data))
      .catch(() => navigate('/vpc'));
  }, [id, navigate]);

  if (!vpc) return <div className="text-gray-400">Loading...</div>;

  return (
    <div>
      <div className="flex items-center gap-4 mb-6">
        <button onClick={() => navigate('/vpc')} className="text-gray-400 hover:text-white">&larr; Back</button>
        <h1 className="text-2xl font-bold text-white">{vpc.name}</h1>
        <span className="bg-green-500/20 text-green-400 text-xs px-2 py-1 rounded">{vpc.state}</span>
      </div>

      <div className="bg-gray-800 rounded-lg p-6 mb-6">
        <h2 className="text-lg font-semibold text-white mb-4">Details</h2>
        <div className="grid grid-cols-2 gap-4">
          {Object.entries({
            'VPC ID': vpc.id,
            'CIDR Block': vpc.cidr_block,
            'State': vpc.state,
            'Docker Network ID': vpc.docker_network_id || '--',
            'Created': new Date(vpc.created_at).toLocaleString(),
          }).map(([k, v]) => (
            <div key={k}>
              <p className="text-xs text-gray-500">{k}</p>
              <p className="text-sm text-white font-mono">{v}</p>
            </div>
          ))}
        </div>
      </div>

      <div className="bg-gray-800 rounded-lg p-6">
        <h2 className="text-lg font-semibold text-white mb-4">Connected Instances</h2>
        {vpc.instances && vpc.instances.length > 0 ? (
          <div className="space-y-2">
            {vpc.instances.map(inst => (
              <div key={inst.id} className="bg-gray-900 rounded p-3 flex items-center justify-between">
                <div>
                  <span className="text-white text-sm">{inst.name}</span>
                  <span className="text-gray-500 text-xs ml-2">{inst.private_ip}</span>
                </div>
                <span className={`px-2 py-1 rounded text-xs ${
                  inst.state === 'running' ? 'bg-green-500/20 text-green-400' : 'bg-gray-500/20 text-gray-400'
                }`}>{inst.state}</span>
              </div>
            ))}
          </div>
        ) : (
          <p className="text-gray-400 text-sm">No instances connected to this VPC.</p>
        )}
      </div>
    </div>
  );
}
