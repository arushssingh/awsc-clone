import { useState, useEffect } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import api from '../api';

export default function LambdaDetail() {
  const { id } = useParams();
  const navigate = useNavigate();
  const [fn, setFn] = useState(null);
  const [tab, setTab] = useState('overview');
  const [testPayload, setTestPayload] = useState('{}');
  const [testResult, setTestResult] = useState(null);
  const [invoking, setInvoking] = useState(false);
  const [invocations, setInvocations] = useState([]);

  useEffect(() => {
    api.get(`/lambda/functions/${id}`)
      .then(res => setFn(res.data))
      .catch(() => navigate('/lambda'));
  }, [id, navigate]);

  useEffect(() => {
    if (tab === 'invocations') {
      api.get(`/lambda/functions/${id}/invocations`)
        .then(res => setInvocations(res.data))
        .catch(() => {});
    }
  }, [tab, id]);

  const invoke = async () => {
    setInvoking(true);
    setTestResult(null);
    try {
      const payload = JSON.parse(testPayload);
      const res = await api.post(`/lambda/functions/${id}/invoke`, { payload });
      setTestResult(res.data);
    } catch (err) {
      setTestResult({ status: 'error', error: err.response?.data?.detail || err.message });
    } finally {
      setInvoking(false);
    }
  };

  if (!fn) return <div className="text-gray-400">Loading...</div>;

  const tabs = ['overview', 'test', 'invocations'];

  return (
    <div>
      <div className="flex items-center gap-4 mb-6">
        <button onClick={() => navigate('/lambda')} className="text-gray-400 hover:text-white">&larr; Back</button>
        <h1 className="text-2xl font-bold text-white">{fn.name}</h1>
        <span className="bg-purple-500/20 text-purple-400 text-xs px-2 py-1 rounded">{fn.runtime}</span>
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
              'Function ID': fn.id,
              'Runtime': fn.runtime,
              'Handler': fn.handler,
              'Timeout': `${fn.timeout}s`,
              'Memory': `${fn.memory_limit} MB`,
              'Created': new Date(fn.created_at).toLocaleString(),
            }).map(([k, v]) => (
              <div key={k}>
                <p className="text-xs text-gray-500">{k}</p>
                <p className="text-sm text-white font-mono">{v}</p>
              </div>
            ))}
          </div>
        )}

        {tab === 'test' && (
          <div className="space-y-4">
            <div>
              <label className="block text-sm text-gray-300 mb-1">Test Event (JSON)</label>
              <textarea value={testPayload} onChange={e => setTestPayload(e.target.value)} rows={6}
                className="w-full px-3 py-2 bg-gray-900 border border-gray-600 rounded text-white text-sm font-mono focus:outline-none focus:border-blue-500" />
            </div>
            <button onClick={invoke} disabled={invoking}
              className="bg-blue-600 hover:bg-blue-700 disabled:bg-blue-800 text-white px-4 py-2 rounded text-sm transition-colors">
              {invoking ? 'Invoking...' : 'Invoke'}
            </button>
            {testResult && (
              <div className={`p-4 rounded ${testResult.status === 'success' ? 'bg-green-900/30 border border-green-700' : 'bg-red-900/30 border border-red-700'}`}>
                <p className="text-sm font-semibold mb-2 text-white">
                  {testResult.status} {testResult.duration_ms && `(${testResult.duration_ms}ms)`}
                </p>
                <pre className="text-sm text-gray-300 font-mono whitespace-pre-wrap">
                  {testResult.output || testResult.error || 'No output'}
                </pre>
              </div>
            )}
          </div>
        )}

        {tab === 'invocations' && (
          <div className="space-y-2">
            {invocations.length === 0 ? (
              <p className="text-gray-400">No invocations yet.</p>
            ) : (
              invocations.map(inv => (
                <div key={inv.id} className="bg-gray-900 rounded p-3">
                  <div className="flex items-center gap-3 text-sm">
                    <span className={`px-2 py-0.5 rounded text-xs ${
                      inv.status === 'success' ? 'bg-green-500/20 text-green-400' : 'bg-red-500/20 text-red-400'
                    }`}>{inv.status}</span>
                    <span className="text-gray-400">{inv.duration_ms}ms</span>
                    <span className="text-gray-500 text-xs">{new Date(inv.completed_at).toLocaleString()}</span>
                  </div>
                </div>
              ))
            )}
          </div>
        )}
      </div>
    </div>
  );
}
