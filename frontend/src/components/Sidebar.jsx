import { NavLink } from 'react-router-dom';
import {
  HiOutlineHome,
  HiOutlineServer,
  HiOutlineCloud,
  HiOutlineBolt,
  HiOutlineGlobeAlt,
  HiOutlineChartBar,
  HiOutlineShieldCheck,
  HiOutlineSquares2X2,
} from 'react-icons/hi2';

const navGroups = [
  {
    label: 'Overview',
    items: [
      { name: 'Dashboard', path: '/', icon: HiOutlineHome },
    ],
  },
  {
    label: 'Compute',
    items: [
      { name: 'EC2', path: '/ec2', icon: HiOutlineServer },
      { name: 'Lambda', path: '/lambda', icon: HiOutlineBolt },
    ],
  },
  {
    label: 'Storage',
    items: [
      { name: 'S3', path: '/s3', icon: HiOutlineCloud },
    ],
  },
  {
    label: 'Networking',
    items: [
      { name: 'VPC', path: '/vpc', icon: HiOutlineSquares2X2 },
      { name: 'Route 53', path: '/route53', icon: HiOutlineGlobeAlt },
    ],
  },
  {
    label: 'Management',
    items: [
      { name: 'CloudWatch', path: '/cloudwatch', icon: HiOutlineChartBar },
    ],
  },
  {
    label: 'Security',
    items: [
      { name: 'IAM', path: '/iam', icon: HiOutlineShieldCheck },
    ],
  },
];

export default function Sidebar() {
  return (
    <aside className="w-60 bg-gray-900 border-r border-gray-700 h-screen overflow-y-auto flex-shrink-0">
      <div className="px-4 py-4 border-b border-gray-700 flex items-center gap-3">
        <img src="/logo.png" alt="folateCloud" className="w-8 h-8 rounded" />
        <div>
          <h1 className="text-lg font-bold text-white">folateCloud</h1>
          <p className="text-xs text-gray-500">Console</p>
        </div>
      </div>

      <nav className="py-2">
        {navGroups.map((group) => (
          <div key={group.label} className="mb-1">
            <p className="px-4 py-2 text-xs font-semibold text-gray-500 uppercase tracking-wider">
              {group.label}
            </p>
            {group.items.map((item) => (
              <NavLink
                key={item.path}
                to={item.path}
                end={item.path === '/'}
                className={({ isActive }) =>
                  `flex items-center gap-3 px-4 py-2 text-sm transition-colors ${
                    isActive
                      ? 'bg-blue-600/20 text-blue-400 border-r-2 border-blue-400'
                      : 'text-gray-300 hover:bg-gray-800 hover:text-white'
                  }`
                }
              >
                <item.icon className="w-5 h-5" />
                {item.name}
              </NavLink>
            ))}
          </div>
        ))}
      </nav>
    </aside>
  );
}
