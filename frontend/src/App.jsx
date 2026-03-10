import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { AuthProvider } from './context/AuthContext';
import { ToastProvider } from './components/Toast';
import Layout from './components/Layout';
import Login from './pages/Login';
import Dashboard from './pages/Dashboard';
import EC2 from './pages/EC2';
import EC2Detail from './pages/EC2Detail';
import S3 from './pages/S3';
import S3Bucket from './pages/S3Bucket';
import Lambda from './pages/Lambda';
import LambdaDetail from './pages/LambdaDetail';
import Route53 from './pages/Route53';
import CloudWatch from './pages/CloudWatch';
import VPC from './pages/VPC';
import VPCDetail from './pages/VPCDetail';
import IAM from './pages/IAM';
import Deploy from './pages/Deploy';
import DeployDetail from './pages/DeployDetail';

export default function App() {
  return (
    <AuthProvider>
      <ToastProvider>
      <BrowserRouter>
        <Routes>
          <Route path="/login" element={<Login />} />
          <Route element={<Layout />}>
            <Route path="/" element={<Dashboard />} />
            <Route path="/ec2" element={<EC2 />} />
            <Route path="/ec2/:id" element={<EC2Detail />} />
            <Route path="/s3" element={<S3 />} />
            <Route path="/s3/:name" element={<S3Bucket />} />
            <Route path="/lambda" element={<Lambda />} />
            <Route path="/lambda/:id" element={<LambdaDetail />} />
            <Route path="/route53" element={<Route53 />} />
            <Route path="/cloudwatch" element={<CloudWatch />} />
            <Route path="/vpc" element={<VPC />} />
            <Route path="/vpc/:id" element={<VPCDetail />} />
            <Route path="/iam" element={<IAM />} />
            <Route path="/deploy" element={<Deploy />} />
            <Route path="/deploy/:id" element={<DeployDetail />} />
          </Route>
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </BrowserRouter>
      </ToastProvider>
    </AuthProvider>
  );
}
