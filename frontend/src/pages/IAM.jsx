import { useState, useEffect } from 'react';
import api from '../api';
import { useToast } from '../components/Toast';

const TABS = ['Users', 'Roles', 'Policies', 'API Keys'];

export default function IAM() {
  const toast = useToast();
  const [tab, setTab] = useState('Users');
  const [users, setUsers] = useState([]);
  const [roles, setRoles] = useState([]);
  const [policies, setPolicies] = useState([]);
  const [apiKeys, setApiKeys] = useState([]);
  const [showCreate, setShowCreate] = useState(false);
  const [newKeySecret, setNewKeySecret] = useState(null);

  // Role creation form
  const [roleForm, setRoleForm] = useState({ name: '', description: '' });
  // Policy creation form
  const [policyForm, setPolicyForm] = useState({
    name: '', document: '{"statements": [{"effect": "Allow", "actions": ["*"], "resources": ["*"]}]}',
  });
  // API key form
  const [keyDesc, setKeyDesc] = useState('');

  // Assignment modals
  const [assignModal, setAssignModal] = useState(null); // { type: 'role'|'policy', userId|roleId }
  const [selectedAssign, setSelectedAssign] = useState('');

  const fetchAll = () => {
    api.get('/iam/users').then(r => setUsers(Array.isArray(r.data) ? r.data : [])).catch(() => setUsers([]));
    api.get('/iam/roles').then(r => setRoles(Array.isArray(r.data) ? r.data : [])).catch(() => setRoles([]));
    api.get('/iam/policies').then(r => setPolicies(Array.isArray(r.data) ? r.data : [])).catch(() => setPolicies([]));
    api.get('/iam/api-keys').then(r => setApiKeys(Array.isArray(r.data) ? r.data : [])).catch(() => setApiKeys([]));
  };

  useEffect(() => { fetchAll(); }, []);

  const createRole = async (e) => {
    e.preventDefault();
    try {
      await api.post('/iam/roles', roleForm);
      setShowCreate(false);
      setRoleForm({ name: '', description: '' });
      fetchAll();
    } catch (err) { toast.error(err.response?.data?.detail || 'Failed'); }
  };

  const deleteRole = async (roleId) => {
    if (!confirm('Delete this role?')) return;
    try {
      await api.delete(`/iam/roles/${roleId}`);
      fetchAll();
    } catch (err) { toast.error(err.response?.data?.detail || 'Failed'); }
  };

  const createPolicy = async (e) => {
    e.preventDefault();
    try {
      JSON.parse(policyForm.document);
      await api.post('/iam/policies', policyForm);
      setShowCreate(false);
      setPolicyForm({ name: '', document: '{"statements": [{"effect": "Allow", "actions": ["*"], "resources": ["*"]}]}' });
      fetchAll();
    } catch (err) { toast.error(err.response?.data?.detail || err.message || 'Failed'); }
  };

  const deletePolicy = async (policyId) => {
    if (!confirm('Delete this policy?')) return;
    try {
      await api.delete(`/iam/policies/${policyId}`);
      fetchAll();
    } catch (err) { toast.error(err.response?.data?.detail || 'Failed'); }
  };

  const createApiKey = async (e) => {
    e.preventDefault();
    try {
      const res = await api.post('/iam/api-keys', { description: keyDesc });
      setNewKeySecret(res.data);
      setShowCreate(false);
      setKeyDesc('');
      fetchAll();
    } catch (err) { toast.error(err.response?.data?.detail || 'Failed'); }
  };

  const revokeKey = async (keyId) => {
    if (!confirm('Revoke this API key?')) return;
    try {
      await api.delete(`/iam/api-keys/${keyId}`);
      fetchAll();
    } catch (err) { toast.error(err.response?.data?.detail || 'Failed'); }
  };

  const toggleUserActive = async (userId, currentlyActive) => {
    try {
      await api.put(`/iam/users/${userId}`, { is_active: !currentlyActive });
      fetchAll();
    } catch (err) { toast.error(err.response?.data?.detail || 'Failed'); }
  };

  const assignRole = async (e) => {
    e.preventDefault();
    if (!selectedAssign) return;
    try {
      await api.post('/iam/users/assign-role', { user_id: assignModal.userId, role_id: selectedAssign });
      setAssignModal(null);
      setSelectedAssign('');
      fetchAll();
    } catch (err) { toast.error(err.response?.data?.detail || 'Failed'); }
  };

  const removeRole = async (userId, roleName) => {
    const role = roles.find(r => r.name === roleName);
    if (!role) return;
    try {
      await api.post('/iam/users/remove-role', { user_id: userId, role_id: role.id });
      fetchAll();
    } catch (err) { toast.error(err.response?.data?.detail || 'Failed'); }
  };

  const attachPolicy = async (e) => {
    e.preventDefault();
    if (!selectedAssign) return;
    try {
      await api.post('/iam/roles/attach-policy', { role_id: assignModal.roleId, policy_id: selectedAssign });
      setAssignModal(null);
      setSelectedAssign('');
      fetchAll();
    } catch (err) { toast.error(err.response?.data?.detail || 'Failed'); }
  };

  const detachPolicy = async (roleId, policyName) => {
    const policy = policies.find(p => p.name === policyName);
    if (!policy) return;
    try {
      await api.post('/iam/roles/detach-policy', { role_id: roleId, policy_id: policy.id });
      fetchAll();
    } catch (err) { toast.error(err.response?.data?.detail || 'Failed'); }
  };

  return (
    <div>
      <h1 className="text-2xl font-bold text-white mb-6">IAM - Identity & Access Management</h1>

      {/* Tabs */}
      <div className="flex gap-1 mb-4">
        {TABS.map(t => (
          <button key={t} onClick={() => { setTab(t); setShowCreate(false); setAssignModal(null); }}
            className={`px-4 py-2 text-sm rounded-t transition-colors ${
              tab === t ? 'bg-gray-800 text-white' : 'bg-gray-900 text-gray-400 hover:text-white'
            }`}>{t}</button>
        ))}
      </div>

      {/* New API Key Secret Modal */}
      {newKeySecret && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
          <div className="bg-gray-800 rounded-lg p-6 w-full max-w-md mx-4">
            <h2 className="text-lg font-semibold text-white mb-4">API Key Created</h2>
            <div className="bg-yellow-900/30 border border-yellow-600 rounded p-3 mb-4">
              <p className="text-yellow-300 text-sm">Save this secret now. It won't be shown again.</p>
            </div>
            <div className="space-y-2">
              <div>
                <p className="text-xs text-gray-500">Key ID</p>
                <p className="text-sm text-white font-mono bg-gray-900 p-2 rounded">{newKeySecret.key_id}</p>
              </div>
              <div>
                <p className="text-xs text-gray-500">Secret</p>
                <p className="text-sm text-white font-mono bg-gray-900 p-2 rounded break-all">{newKeySecret.key_secret}</p>
              </div>
              <div>
                <p className="text-xs text-gray-500">Usage</p>
                <p className="text-sm text-gray-400 font-mono bg-gray-900 p-2 rounded">X-API-Key: {newKeySecret.key_id}:{newKeySecret.key_secret}</p>
              </div>
            </div>
            <button onClick={() => setNewKeySecret(null)} className="mt-4 bg-blue-600 hover:bg-blue-700 text-white px-4 py-2 rounded text-sm w-full">Done</button>
          </div>
        </div>
      )}

      {/* Assign Role Modal */}
      {assignModal?.type === 'role' && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
          <div className="bg-gray-800 rounded-lg p-6 w-full max-w-sm mx-4">
            <h2 className="text-lg font-semibold text-white mb-4">Assign Role</h2>
            <form onSubmit={assignRole} className="space-y-3">
              <select value={selectedAssign} onChange={e => setSelectedAssign(e.target.value)} required
                className="w-full px-3 py-2 bg-gray-700 border border-gray-600 rounded text-white text-sm">
                <option value="">Select a role...</option>
                {roles.map(r => (
                  <option key={r.id} value={r.id}>{r.name}</option>
                ))}
              </select>
              <div className="flex gap-2">
                <button type="submit" className="bg-blue-600 text-white px-3 py-1.5 rounded text-sm">Assign</button>
                <button type="button" onClick={() => { setAssignModal(null); setSelectedAssign(''); }} className="bg-gray-600 text-white px-3 py-1.5 rounded text-sm">Cancel</button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* Attach Policy Modal */}
      {assignModal?.type === 'policy' && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
          <div className="bg-gray-800 rounded-lg p-6 w-full max-w-sm mx-4">
            <h2 className="text-lg font-semibold text-white mb-4">Attach Policy</h2>
            <form onSubmit={attachPolicy} className="space-y-3">
              <select value={selectedAssign} onChange={e => setSelectedAssign(e.target.value)} required
                className="w-full px-3 py-2 bg-gray-700 border border-gray-600 rounded text-white text-sm">
                <option value="">Select a policy...</option>
                {policies.map(p => (
                  <option key={p.id} value={p.id}>{p.name}</option>
                ))}
              </select>
              <div className="flex gap-2">
                <button type="submit" className="bg-blue-600 text-white px-3 py-1.5 rounded text-sm">Attach</button>
                <button type="button" onClick={() => { setAssignModal(null); setSelectedAssign(''); }} className="bg-gray-600 text-white px-3 py-1.5 rounded text-sm">Cancel</button>
              </div>
            </form>
          </div>
        </div>
      )}

      <div className="bg-gray-800 rounded-lg p-6">
        {/* Users Tab */}
        {tab === 'Users' && (
          <div>
            <table className="w-full">
              <thead>
                <tr className="border-b border-gray-700">
                  <th className="text-left px-4 py-3 text-sm font-medium text-gray-400">Username</th>
                  <th className="text-left px-4 py-3 text-sm font-medium text-gray-400">Email</th>
                  <th className="text-left px-4 py-3 text-sm font-medium text-gray-400">Root</th>
                  <th className="text-left px-4 py-3 text-sm font-medium text-gray-400">Roles</th>
                  <th className="text-left px-4 py-3 text-sm font-medium text-gray-400">Active</th>
                  <th className="text-left px-4 py-3 text-sm font-medium text-gray-400">Actions</th>
                </tr>
              </thead>
              <tbody>
                {users.map(u => (
                  <tr key={u.id} className="border-b border-gray-700/50">
                    <td className="px-4 py-3 text-sm text-white">{u.username}</td>
                    <td className="px-4 py-3 text-sm text-gray-400">{u.email || '--'}</td>
                    <td className="px-4 py-3 text-sm">{u.is_root ? <span className="text-yellow-400">Yes</span> : 'No'}</td>
                    <td className="px-4 py-3 text-sm text-gray-400">
                      <div className="flex flex-wrap gap-1">
                        {(u.roles || []).map(r => (
                          <span key={r} className="inline-flex items-center gap-1 bg-gray-700 text-xs px-2 py-0.5 rounded">
                            {r}
                            <button onClick={() => removeRole(u.id, r)} className="text-red-400 hover:text-red-300 ml-1">&times;</button>
                          </span>
                        ))}
                        {(u.roles || []).length === 0 && '--'}
                      </div>
                    </td>
                    <td className="px-4 py-3 text-sm">{u.is_active !== false ? <span className="text-green-400">Yes</span> : <span className="text-red-400">No</span>}</td>
                    <td className="px-4 py-3 text-sm space-x-2">
                      <button onClick={() => setAssignModal({ type: 'role', userId: u.id })} className="text-blue-400 hover:text-blue-300 text-xs">+ Role</button>
                      {!u.is_root && (
                        <button onClick={() => toggleUserActive(u.id, u.is_active !== false)} className="text-yellow-400 hover:text-yellow-300 text-xs">
                          {u.is_active !== false ? 'Disable' : 'Enable'}
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {/* Roles Tab */}
        {tab === 'Roles' && (
          <div>
            <button onClick={() => setShowCreate(true)} className="bg-blue-600 hover:bg-blue-700 text-white px-3 py-1.5 rounded text-sm mb-4">Create Role</button>
            {showCreate && (
              <form onSubmit={createRole} className="bg-gray-900 rounded p-4 mb-4 space-y-3">
                <input value={roleForm.name} onChange={e => setRoleForm({...roleForm, name: e.target.value})} required placeholder="Role name"
                  className="w-full px-3 py-2 bg-gray-700 border border-gray-600 rounded text-white text-sm" />
                <input value={roleForm.description} onChange={e => setRoleForm({...roleForm, description: e.target.value})} placeholder="Description"
                  className="w-full px-3 py-2 bg-gray-700 border border-gray-600 rounded text-white text-sm" />
                <div className="flex gap-2">
                  <button type="submit" className="bg-blue-600 text-white px-3 py-1.5 rounded text-sm">Create</button>
                  <button type="button" onClick={() => setShowCreate(false)} className="bg-gray-600 text-white px-3 py-1.5 rounded text-sm">Cancel</button>
                </div>
              </form>
            )}
            <table className="w-full">
              <thead>
                <tr className="border-b border-gray-700">
                  <th className="text-left px-4 py-3 text-sm font-medium text-gray-400">Name</th>
                  <th className="text-left px-4 py-3 text-sm font-medium text-gray-400">Description</th>
                  <th className="text-left px-4 py-3 text-sm font-medium text-gray-400">Policies</th>
                  <th className="text-left px-4 py-3 text-sm font-medium text-gray-400">Actions</th>
                </tr>
              </thead>
              <tbody>
                {roles.map(r => (
                  <tr key={r.id} className="border-b border-gray-700/50">
                    <td className="px-4 py-3 text-sm text-white">{r.name}</td>
                    <td className="px-4 py-3 text-sm text-gray-400">{r.description || '--'}</td>
                    <td className="px-4 py-3 text-sm text-gray-400">
                      <div className="flex flex-wrap gap-1">
                        {(r.policies || []).map(p => (
                          <span key={p} className="inline-flex items-center gap-1 bg-gray-700 text-xs px-2 py-0.5 rounded">
                            {p}
                            <button onClick={() => detachPolicy(r.id, p)} className="text-red-400 hover:text-red-300 ml-1">&times;</button>
                          </span>
                        ))}
                        {(r.policies || []).length === 0 && '--'}
                      </div>
                    </td>
                    <td className="px-4 py-3 text-sm space-x-2">
                      <button onClick={() => setAssignModal({ type: 'policy', roleId: r.id })} className="text-blue-400 hover:text-blue-300 text-xs">+ Policy</button>
                      <button onClick={() => deleteRole(r.id)} className="text-red-400 hover:text-red-300 text-xs">Delete</button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {/* Policies Tab */}
        {tab === 'Policies' && (
          <div>
            <button onClick={() => setShowCreate(true)} className="bg-blue-600 hover:bg-blue-700 text-white px-3 py-1.5 rounded text-sm mb-4">Create Policy</button>
            {showCreate && (
              <form onSubmit={createPolicy} className="bg-gray-900 rounded p-4 mb-4 space-y-3">
                <input value={policyForm.name} onChange={e => setPolicyForm({...policyForm, name: e.target.value})} required placeholder="Policy name"
                  className="w-full px-3 py-2 bg-gray-700 border border-gray-600 rounded text-white text-sm" />
                <textarea value={policyForm.document} onChange={e => setPolicyForm({...policyForm, document: e.target.value})} rows={6}
                  className="w-full px-3 py-2 bg-gray-700 border border-gray-600 rounded text-white text-sm font-mono" />
                <p className="text-xs text-gray-500">Format: {"{"}"statements": [{"{"}"effect": "Allow"|"Deny", "actions": ["service:Action"], "resources": ["*"]{"}"} ]{"}"}</p>
                <div className="flex gap-2">
                  <button type="submit" className="bg-blue-600 text-white px-3 py-1.5 rounded text-sm">Create</button>
                  <button type="button" onClick={() => setShowCreate(false)} className="bg-gray-600 text-white px-3 py-1.5 rounded text-sm">Cancel</button>
                </div>
              </form>
            )}
            <table className="w-full">
              <thead>
                <tr className="border-b border-gray-700">
                  <th className="text-left px-4 py-3 text-sm font-medium text-gray-400">Name</th>
                  <th className="text-left px-4 py-3 text-sm font-medium text-gray-400">Statements</th>
                  <th className="text-left px-4 py-3 text-sm font-medium text-gray-400">Created</th>
                  <th className="text-left px-4 py-3 text-sm font-medium text-gray-400">Actions</th>
                </tr>
              </thead>
              <tbody>
                {policies.map(p => (
                  <tr key={p.id} className="border-b border-gray-700/50">
                    <td className="px-4 py-3 text-sm text-white">{p.name}</td>
                    <td className="px-4 py-3 text-sm text-gray-400">
                      {(p.document?.statements || []).map((s, i) => (
                        <div key={i} className="text-xs">
                          <span className={s.effect === 'Allow' ? 'text-green-400' : 'text-red-400'}>{s.effect}</span>
                          {' '}{(s.actions || []).join(', ')}
                        </div>
                      ))}
                    </td>
                    <td className="px-4 py-3 text-sm text-gray-400">{new Date(p.created_at).toLocaleDateString()}</td>
                    <td className="px-4 py-3 text-sm">
                      <button onClick={() => deletePolicy(p.id)} className="text-red-400 hover:text-red-300 text-xs">Delete</button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {/* API Keys Tab */}
        {tab === 'API Keys' && (
          <div>
            <button onClick={() => setShowCreate(true)} className="bg-blue-600 hover:bg-blue-700 text-white px-3 py-1.5 rounded text-sm mb-4">Create API Key</button>
            {showCreate && (
              <form onSubmit={createApiKey} className="bg-gray-900 rounded p-4 mb-4 space-y-3">
                <input value={keyDesc} onChange={e => setKeyDesc(e.target.value)} placeholder="Description (optional)"
                  className="w-full px-3 py-2 bg-gray-700 border border-gray-600 rounded text-white text-sm" />
                <div className="flex gap-2">
                  <button type="submit" className="bg-blue-600 text-white px-3 py-1.5 rounded text-sm">Create</button>
                  <button type="button" onClick={() => setShowCreate(false)} className="bg-gray-600 text-white px-3 py-1.5 rounded text-sm">Cancel</button>
                </div>
              </form>
            )}
            <table className="w-full">
              <thead>
                <tr className="border-b border-gray-700">
                  <th className="text-left px-4 py-3 text-sm font-medium text-gray-400">Key ID</th>
                  <th className="text-left px-4 py-3 text-sm font-medium text-gray-400">Description</th>
                  <th className="text-left px-4 py-3 text-sm font-medium text-gray-400">Last Used</th>
                  <th className="text-left px-4 py-3 text-sm font-medium text-gray-400">Actions</th>
                </tr>
              </thead>
              <tbody>
                {apiKeys.map(k => (
                  <tr key={k.key_id} className="border-b border-gray-700/50">
                    <td className="px-4 py-3 text-sm text-white font-mono">{k.key_id}</td>
                    <td className="px-4 py-3 text-sm text-gray-400">{k.description || '--'}</td>
                    <td className="px-4 py-3 text-sm text-gray-400">{k.last_used_at ? new Date(k.last_used_at).toLocaleString() : 'Never'}</td>
                    <td className="px-4 py-3 text-sm">
                      <button onClick={() => revokeKey(k.key_id)} className="text-red-400 hover:text-red-300 text-xs">Revoke</button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
