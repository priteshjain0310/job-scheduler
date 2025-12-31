"use client";

import { useState, useEffect, useCallback } from "react";
import { 
  RefreshCw, 
  Plus, 
  Search, 
  Clock, 
  CheckCircle, 
  XCircle, 
  AlertTriangle,
  Play,
  Pause,
  RotateCcw
} from "lucide-react";
import { formatDistanceToNow } from "date-fns";

// Types
interface Job {
  id: string;
  tenant_id: string;
  idempotency_key: string;
  payload: Record<string, unknown>;
  status: string;
  priority: string;
  attempt: number;
  max_attempts: number;
  lease_owner: string | null;
  lease_expires_at: string | null;
  scheduled_at: string | null;
  created_at: string;
  updated_at: string;
  completed_at: string | null;
  last_error: string | null;
}

interface JobStats {
  stats: Record<string, number>;
  queue_depth: number;
}

// API Configuration
const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
const WS_URL = process.env.NEXT_PUBLIC_WS_URL || "ws://localhost:8000";

// Status badge component
function StatusBadge({ status }: { status: string }) {
  const styles: Record<string, string> = {
    queued: "bg-blue-100 text-blue-800 dark:bg-blue-900 dark:text-blue-300",
    leased: "bg-yellow-100 text-yellow-800 dark:bg-yellow-900 dark:text-yellow-300",
    running: "bg-purple-100 text-purple-800 dark:bg-purple-900 dark:text-purple-300",
    succeeded: "bg-green-100 text-green-800 dark:bg-green-900 dark:text-green-300",
    failed: "bg-red-100 text-red-800 dark:bg-red-900 dark:text-red-300",
    dlq: "bg-gray-100 text-gray-800 dark:bg-gray-700 dark:text-gray-300",
  };

  const icons: Record<string, React.ReactNode> = {
    queued: <Clock className="w-3 h-3" />,
    leased: <Pause className="w-3 h-3" />,
    running: <Play className="w-3 h-3" />,
    succeeded: <CheckCircle className="w-3 h-3" />,
    failed: <XCircle className="w-3 h-3" />,
    dlq: <AlertTriangle className="w-3 h-3" />,
  };

  return (
    <span className={`inline-flex items-center gap-1 px-2 py-1 rounded-full text-xs font-medium ${styles[status] || styles.queued}`}>
      {icons[status]}
      {status.toUpperCase()}
    </span>
  );
}

// Priority badge component
function PriorityBadge({ priority }: { priority: string }) {
  const styles: Record<string, string> = {
    low: "bg-gray-100 text-gray-600",
    normal: "bg-blue-100 text-blue-600",
    high: "bg-orange-100 text-orange-600",
    critical: "bg-red-100 text-red-600",
  };

  return (
    <span className={`px-2 py-0.5 rounded text-xs font-medium ${styles[priority] || styles.normal}`}>
      {priority}
    </span>
  );
}

// Stats card component
function StatsCard({ title, value, icon, color }: { title: string; value: number; icon: React.ReactNode; color: string }) {
  return (
    <div className={`bg-white dark:bg-gray-800 rounded-lg shadow p-4 border-l-4 ${color}`}>
      <div className="flex items-center justify-between">
        <div>
          <p className="text-sm text-gray-500 dark:text-gray-400">{title}</p>
          <p className="text-2xl font-bold text-gray-900 dark:text-white">{value}</p>
        </div>
        <div className="text-gray-400">{icon}</div>
      </div>
    </div>
  );
}

// Job row component
function JobRow({ job, onRetry }: { job: Job; onRetry: (id: string) => void }) {
  const [expanded, setExpanded] = useState(false);

  return (
    <>
      <tr 
        className="hover:bg-gray-50 dark:hover:bg-gray-700 cursor-pointer"
        onClick={() => setExpanded(!expanded)}
      >
        <td className="px-4 py-3 text-sm font-mono text-gray-600 dark:text-gray-300">
          {job.id.slice(0, 8)}...
        </td>
        <td className="px-4 py-3">
          <StatusBadge status={job.status} />
        </td>
        <td className="px-4 py-3">
          <PriorityBadge priority={job.priority} />
        </td>
        <td className="px-4 py-3 text-sm text-gray-600 dark:text-gray-300">
          {job.payload.job_type as string || "unknown"}
        </td>
        <td className="px-4 py-3 text-sm text-gray-600 dark:text-gray-300">
          {job.attempt}/{job.max_attempts}
        </td>
        <td className="px-4 py-3 text-sm text-gray-500 dark:text-gray-400">
          {formatDistanceToNow(new Date(job.created_at), { addSuffix: true })}
        </td>
        <td className="px-4 py-3">
          {job.status === "dlq" && (
            <button
              onClick={(e) => {
                e.stopPropagation();
                onRetry(job.id);
              }}
              className="inline-flex items-center gap-1 px-2 py-1 text-xs font-medium text-blue-600 hover:text-blue-800 hover:bg-blue-50 rounded"
            >
              <RotateCcw className="w-3 h-3" />
              Retry
            </button>
          )}
        </td>
      </tr>
      {expanded && (
        <tr className="bg-gray-50 dark:bg-gray-800">
          <td colSpan={7} className="px-4 py-3">
            <div className="grid grid-cols-2 gap-4 text-sm">
              <div>
                <p className="font-medium text-gray-700 dark:text-gray-300">Full ID</p>
                <p className="font-mono text-gray-600 dark:text-gray-400">{job.id}</p>
              </div>
              <div>
                <p className="font-medium text-gray-700 dark:text-gray-300">Tenant</p>
                <p className="text-gray-600 dark:text-gray-400">{job.tenant_id}</p>
              </div>
              <div>
                <p className="font-medium text-gray-700 dark:text-gray-300">Idempotency Key</p>
                <p className="font-mono text-gray-600 dark:text-gray-400">{job.idempotency_key}</p>
              </div>
              <div>
                <p className="font-medium text-gray-700 dark:text-gray-300">Lease Owner</p>
                <p className="text-gray-600 dark:text-gray-400">{job.lease_owner || "None"}</p>
              </div>
              <div className="col-span-2">
                <p className="font-medium text-gray-700 dark:text-gray-300">Payload</p>
                <pre className="mt-1 p-2 bg-gray-100 dark:bg-gray-900 rounded text-xs overflow-x-auto">
                  {JSON.stringify(job.payload, null, 2)}
                </pre>
              </div>
              {job.last_error && (
                <div className="col-span-2">
                  <p className="font-medium text-red-600">Last Error</p>
                  <p className="text-red-500">{job.last_error}</p>
                </div>
              )}
            </div>
          </td>
        </tr>
      )}
    </>
  );
}

// Create job modal
function CreateJobModal({ 
  isOpen, 
  onClose, 
  onSubmit, 
  token 
}: { 
  isOpen: boolean; 
  onClose: () => void; 
  onSubmit: () => void;
  token: string;
}) {
  const [jobType, setJobType] = useState("echo");
  const [priority, setPriority] = useState("normal");
  const [payload, setPayload] = useState('{"message": "Hello, World!"}');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    setError("");

    try {
      const idempotencyKey = `dashboard-${Date.now()}-${Math.random().toString(36).slice(2)}`;
      
      const response = await fetch(`${API_URL}/v1/jobs`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Authorization": `Bearer ${token}`,
          "Idempotency-Key": idempotencyKey,
        },
        body: JSON.stringify({
          payload: {
            job_type: jobType,
            data: JSON.parse(payload),
          },
          priority,
          max_attempts: 3,
        }),
      });

      if (!response.ok) {
        throw new Error(`Failed to create job: ${response.statusText}`);
      }

      onSubmit();
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to create job");
    } finally {
      setLoading(false);
    }
  };

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50">
      <div className="bg-white dark:bg-gray-800 rounded-lg shadow-xl w-full max-w-md p-6">
        <h2 className="text-xl font-bold text-gray-900 dark:text-white mb-4">Create New Job</h2>
        
        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
              Job Type
            </label>
            <select
              value={jobType}
              onChange={(e) => setJobType(e.target.value)}
              className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-md bg-white dark:bg-gray-700 text-gray-900 dark:text-white"
            >
              <option value="echo">Echo</option>
              <option value="sleep">Sleep</option>
              <option value="http_request">HTTP Request</option>
              <option value="failing_job">Failing Job (Test)</option>
            </select>
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
              Priority
            </label>
            <select
              value={priority}
              onChange={(e) => setPriority(e.target.value)}
              className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-md bg-white dark:bg-gray-700 text-gray-900 dark:text-white"
            >
              <option value="low">Low</option>
              <option value="normal">Normal</option>
              <option value="high">High</option>
              <option value="critical">Critical</option>
            </select>
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
              Payload (JSON)
            </label>
            <textarea
              value={payload}
              onChange={(e) => setPayload(e.target.value)}
              rows={4}
              className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-md bg-white dark:bg-gray-700 text-gray-900 dark:text-white font-mono text-sm"
            />
          </div>

          {error && (
            <div className="text-red-500 text-sm">{error}</div>
          )}

          <div className="flex justify-end gap-3">
            <button
              type="button"
              onClick={onClose}
              className="px-4 py-2 text-gray-700 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700 rounded-md"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={loading}
              className="px-4 py-2 bg-blue-600 text-white rounded-md hover:bg-blue-700 disabled:opacity-50"
            >
              {loading ? "Creating..." : "Create Job"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

// Main dashboard component
export default function Dashboard() {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [stats, setStats] = useState<JobStats | null>(null);
  const [loading, setLoading] = useState(true);
  const [statusFilter, setStatusFilter] = useState<string>("");
  const [searchQuery, setSearchQuery] = useState("");
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [token, setToken] = useState("");
  const [tenantId, setTenantId] = useState("demo-tenant");
  const [isAuthenticated, setIsAuthenticated] = useState(false);

  // Authenticate
  const authenticate = useCallback(async () => {
    try {
      const response = await fetch(`${API_URL}/auth/token`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          api_key: "demo-api-key",
          tenant_id: tenantId,
        }),
      });

      if (response.ok) {
        const data = await response.json();
        setToken(data.access_token);
        setIsAuthenticated(true);
      }
    } catch (err) {
      console.error("Authentication failed:", err);
    }
  }, [tenantId]);

  // Fetch jobs
  const fetchJobs = useCallback(async () => {
    if (!token) return;

    try {
      const params = new URLSearchParams({ page: "1", page_size: "50" });
      if (statusFilter) params.append("status", statusFilter);

      const response = await fetch(`${API_URL}/v1/jobs?${params}`, {
        headers: { Authorization: `Bearer ${token}` },
      });

      if (response.ok) {
        const data = await response.json();
        setJobs(data.jobs);
      }
    } catch (err) {
      console.error("Failed to fetch jobs:", err);
    }
  }, [token, statusFilter]);

  // Fetch stats
  const fetchStats = useCallback(async () => {
    if (!token) return;

    try {
      const response = await fetch(`${API_URL}/v1/jobs/stats/summary`, {
        headers: { Authorization: `Bearer ${token}` },
      });

      if (response.ok) {
        const data = await response.json();
        setStats(data);
      }
    } catch (err) {
      console.error("Failed to fetch stats:", err);
    }
  }, [token]);

  // Retry job from DLQ
  const retryJob = async (jobId: string) => {
    try {
      const response = await fetch(`${API_URL}/v1/jobs/${jobId}/retry`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify({ reset_attempts: true }),
      });

      if (response.ok) {
        fetchJobs();
        fetchStats();
      }
    } catch (err) {
      console.error("Failed to retry job:", err);
    }
  };

  // Initial authentication
  useEffect(() => {
    authenticate();
  }, [authenticate]);

  // Fetch data when authenticated
  useEffect(() => {
    if (isAuthenticated) {
      setLoading(true);
      Promise.all([fetchJobs(), fetchStats()]).finally(() => setLoading(false));
    }
  }, [isAuthenticated, fetchJobs, fetchStats]);

  // Auto-refresh
  useEffect(() => {
    if (!isAuthenticated) return;

    const interval = setInterval(() => {
      fetchJobs();
      fetchStats();
    }, 5000);

    return () => clearInterval(interval);
  }, [isAuthenticated, fetchJobs, fetchStats]);

  // Filter jobs by search query
  const filteredJobs = jobs.filter((job) => {
    if (!searchQuery) return true;
    const query = searchQuery.toLowerCase();
    return (
      job.id.toLowerCase().includes(query) ||
      job.idempotency_key.toLowerCase().includes(query) ||
      (job.payload.job_type as string)?.toLowerCase().includes(query)
    );
  });

  if (!isAuthenticated) {
    return (
      <div className="min-h-screen flex items-center justify-center">
        <div className="bg-white dark:bg-gray-800 p-8 rounded-lg shadow-lg">
          <h1 className="text-2xl font-bold mb-4 text-gray-900 dark:text-white">Job Scheduler Dashboard</h1>
          <div className="space-y-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                Tenant ID
              </label>
              <input
                type="text"
                value={tenantId}
                onChange={(e) => setTenantId(e.target.value)}
                className="w-full px-3 py-2 border rounded-md"
              />
            </div>
            <button
              onClick={authenticate}
              className="w-full px-4 py-2 bg-blue-600 text-white rounded-md hover:bg-blue-700"
            >
              Connect
            </button>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen p-6">
      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold text-gray-900 dark:text-white">Job Scheduler Dashboard</h1>
          <p className="text-sm text-gray-500 dark:text-gray-400">Tenant: {tenantId}</p>
        </div>
        <div className="flex items-center gap-3">
          <button
            onClick={() => { fetchJobs(); fetchStats(); }}
            className="inline-flex items-center gap-2 px-3 py-2 text-gray-600 hover:text-gray-800 hover:bg-gray-100 dark:text-gray-300 dark:hover:bg-gray-700 rounded-md"
          >
            <RefreshCw className="w-4 h-4" />
            Refresh
          </button>
          <button
            onClick={() => setShowCreateModal(true)}
            className="inline-flex items-center gap-2 px-4 py-2 bg-blue-600 text-white rounded-md hover:bg-blue-700"
          >
            <Plus className="w-4 h-4" />
            New Job
          </button>
        </div>
      </div>

      {/* Stats */}
      <div className="grid grid-cols-1 md:grid-cols-4 gap-4 mb-6">
        <StatsCard
          title="Queue Depth"
          value={stats?.queue_depth || 0}
          icon={<Clock className="w-6 h-6" />}
          color="border-blue-500"
        />
        <StatsCard
          title="Running"
          value={(stats?.stats?.running || 0) + (stats?.stats?.leased || 0)}
          icon={<Play className="w-6 h-6" />}
          color="border-purple-500"
        />
        <StatsCard
          title="Succeeded"
          value={stats?.stats?.succeeded || 0}
          icon={<CheckCircle className="w-6 h-6" />}
          color="border-green-500"
        />
        <StatsCard
          title="Dead Letter Queue"
          value={stats?.stats?.dlq || 0}
          icon={<AlertTriangle className="w-6 h-6" />}
          color="border-red-500"
        />
      </div>

      {/* Filters */}
      <div className="flex items-center gap-4 mb-4">
        <div className="relative flex-1 max-w-md">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400" />
          <input
            type="text"
            placeholder="Search jobs..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="w-full pl-10 pr-4 py-2 border border-gray-300 dark:border-gray-600 rounded-md bg-white dark:bg-gray-800 text-gray-900 dark:text-white"
          />
        </div>
        <select
          value={statusFilter}
          onChange={(e) => setStatusFilter(e.target.value)}
          className="px-4 py-2 border border-gray-300 dark:border-gray-600 rounded-md bg-white dark:bg-gray-800 text-gray-900 dark:text-white"
        >
          <option value="">All Statuses</option>
          <option value="queued">Queued</option>
          <option value="leased">Leased</option>
          <option value="running">Running</option>
          <option value="succeeded">Succeeded</option>
          <option value="failed">Failed</option>
          <option value="dlq">DLQ</option>
        </select>
      </div>

      {/* Jobs table */}
      <div className="bg-white dark:bg-gray-800 rounded-lg shadow overflow-hidden">
        <table className="w-full">
          <thead className="bg-gray-50 dark:bg-gray-700">
            <tr>
              <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-300 uppercase tracking-wider">ID</th>
              <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-300 uppercase tracking-wider">Status</th>
              <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-300 uppercase tracking-wider">Priority</th>
              <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-300 uppercase tracking-wider">Type</th>
              <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-300 uppercase tracking-wider">Attempts</th>
              <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-300 uppercase tracking-wider">Created</th>
              <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-300 uppercase tracking-wider">Actions</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-200 dark:divide-gray-700">
            {loading ? (
              <tr>
                <td colSpan={7} className="px-4 py-8 text-center text-gray-500">
                  Loading...
                </td>
              </tr>
            ) : filteredJobs.length === 0 ? (
              <tr>
                <td colSpan={7} className="px-4 py-8 text-center text-gray-500">
                  No jobs found
                </td>
              </tr>
            ) : (
              filteredJobs.map((job) => (
                <JobRow key={job.id} job={job} onRetry={retryJob} />
              ))
            )}
          </tbody>
        </table>
      </div>

      {/* Create job modal */}
      <CreateJobModal
        isOpen={showCreateModal}
        onClose={() => setShowCreateModal(false)}
        onSubmit={() => { fetchJobs(); fetchStats(); }}
        token={token}
      />
    </div>
  );
}
