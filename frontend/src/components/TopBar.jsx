import { useAuth } from '../context/AuthContext';

export default function TopBar() {
  const { user, logout } = useAuth();

  return (
    <header className="h-14 bg-gray-800 border-b border-gray-700 flex items-center justify-between px-6">
      <div className="text-sm text-gray-400">
        {user?.is_root && (
          <span className="bg-yellow-600/20 text-yellow-400 text-xs px-2 py-1 rounded mr-2">
            ROOT
          </span>
        )}
      </div>
      <div className="flex items-center gap-4">
        <span className="text-sm text-gray-300">{user?.username}</span>
        <button
          onClick={logout}
          className="text-sm text-gray-400 hover:text-white transition-colors"
        >
          Sign out
        </button>
      </div>
    </header>
  );
}
