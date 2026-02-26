"use client";

import { useEffect, useState } from "react";
import {
  CheckCircle2,
  ArrowUpCircle,
  AlertCircle,
  ExternalLink,
  RefreshCw,
  User,
  Terminal,
} from "lucide-react";
import { useAppStore } from "@/lib/store";
import {
  getVersionStatus,
  triggerVersionCheck,
  type ServiceVersionStatus,
  type VersionCheckResponse,
} from "@/lib/api-client";
import { formatDistanceToNow } from "date-fns";

export default function SettingsPage() {
  const { user } = useAppStore();

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold text-gray-100">Settings</h1>

      {/* Account section — all users */}
      <div className="card p-5">
        <h2 className="text-sm font-semibold text-gray-400 uppercase tracking-wider mb-4">
          Account
        </h2>
        {user ? (
          <div className="space-y-3">
            <InfoRow icon={User} label="Email" value={user.email} />
            <InfoRow icon={User} label="Display Name" value={user.display_name} />
            <InfoRow
              icon={User}
              label="Member Since"
              value={new Date(user.created_at).toLocaleDateString()}
            />
            <InfoRow
              icon={User}
              label="Role"
              value={user.is_admin ? "Admin" : "User"}
            />
          </div>
        ) : (
          <p className="text-sm text-gray-500">Loading...</p>
        )}
      </div>

      {/* Service Updates — admin only */}
      {user?.is_admin && <ServiceUpdatesCard />}
    </div>
  );
}

function ServiceUpdatesCard() {
  const [data, setData] = useState<VersionCheckResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [checking, setChecking] = useState(false);

  useEffect(() => {
    getVersionStatus()
      .then(setData)
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  async function handleCheck() {
    setChecking(true);
    try {
      const result = await triggerVersionCheck();
      setData(result);
    } catch {
      // ignore
    } finally {
      setChecking(false);
    }
  }

  const updatesAvailable = data?.services.filter((s) => s.has_update).length ?? 0;

  return (
    <div className="card p-5">
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-sm font-semibold text-gray-400 uppercase tracking-wider">
          Service Updates
        </h2>
        <div className="flex items-center gap-3">
          {data?.checked_at && (
            <span className="text-xs text-gray-600">
              Checked{" "}
              {formatDistanceToNow(new Date(data.checked_at), {
                addSuffix: true,
              })}
            </span>
          )}
          <button
            onClick={handleCheck}
            disabled={checking}
            className="flex items-center gap-1.5 text-xs text-accent hover:text-accent/80 disabled:opacity-50 transition-colors"
          >
            <RefreshCw
              className={`w-3.5 h-3.5 ${checking ? "animate-spin" : ""}`}
            />
            Check now
          </button>
        </div>
      </div>

      {loading ? (
        <div className="flex items-center justify-center py-8">
          <div className="w-5 h-5 border-2 border-accent border-t-transparent rounded-full animate-spin" />
        </div>
      ) : !data || data.services.length === 0 ? (
        <p className="text-sm text-gray-500 py-4">
          No version data available. Click &quot;Check now&quot; to scan for updates.
        </p>
      ) : (
        <>
          {updatesAvailable > 0 && (
            <div className="mb-4 p-3 rounded-lg bg-amber-500/10 border border-amber-500/20">
              <p className="text-sm text-amber-400">
                {updatesAvailable} update{updatesAvailable !== 1 ? "s" : ""}{" "}
                available
              </p>
            </div>
          )}

          <div className="space-y-2">
            {data.services.map((svc) => (
              <ServiceRow key={svc.env_var} service={svc} />
            ))}
          </div>

          {/* How to update */}
          <div className="mt-5 p-4 rounded-lg bg-surface-overlay border border-surface-border">
            <div className="flex items-center gap-2 mb-2">
              <Terminal className="w-4 h-4 text-gray-400" />
              <h3 className="text-sm font-medium text-gray-300">
                How to update
              </h3>
            </div>
            <ol className="text-xs text-gray-500 space-y-1.5 ml-6 list-decimal">
              <li>
                Edit <code className="text-gray-400">.env</code> and change the
                version variable (e.g.{" "}
                <code className="text-gray-400">VLLM_VERSION=v0.17.0</code>)
              </li>
              <li>
                Run{" "}
                <code className="text-gray-400">docker compose pull</code> to
                download the new image
              </li>
              <li>
                Run{" "}
                <code className="text-gray-400">
                  docker compose up -d
                </code>{" "}
                to restart with the new version
              </li>
            </ol>
          </div>
        </>
      )}
    </div>
  );
}

function ServiceRow({ service }: { service: ServiceVersionStatus }) {
  const { name, current, latest, has_update, changelog_url } = service;

  let StatusIcon = AlertCircle;
  let statusColor = "text-gray-600";
  if (latest === null) {
    StatusIcon = AlertCircle;
    statusColor = "text-gray-600";
  } else if (has_update) {
    StatusIcon = ArrowUpCircle;
    statusColor = "text-amber-400";
  } else {
    StatusIcon = CheckCircle2;
    statusColor = "text-emerald-400";
  }

  return (
    <div className="flex items-center gap-3 py-2 px-3 rounded-lg hover:bg-surface-overlay transition-colors">
      <StatusIcon className={`w-4 h-4 shrink-0 ${statusColor}`} />
      <span className="text-sm font-medium text-gray-200 w-28 shrink-0">
        {name}
      </span>
      <div className="flex items-center gap-2 text-xs text-gray-500 flex-1 min-w-0">
        <code className="text-gray-400">{current}</code>
        {has_update && latest && (
          <>
            <span className="text-gray-600">&rarr;</span>
            <code className="text-amber-400">{latest}</code>
          </>
        )}
        {!has_update && latest && (
          <span className="text-emerald-600">(up to date)</span>
        )}
        {latest === null && <span className="text-gray-600">(check failed)</span>}
      </div>
      <a
        href={changelog_url}
        target="_blank"
        rel="noopener noreferrer"
        className="text-gray-600 hover:text-gray-400 transition-colors shrink-0"
        title="View changelog"
      >
        <ExternalLink className="w-3.5 h-3.5" />
      </a>
    </div>
  );
}

function InfoRow({
  icon: Icon,
  label,
  value,
}: {
  icon: React.ComponentType<{ className?: string }>;
  label: string;
  value: string;
}) {
  return (
    <div className="flex items-center gap-3">
      <Icon className="w-4 h-4 text-gray-600 shrink-0" />
      <span className="text-sm text-gray-500 w-32 shrink-0">{label}</span>
      <span className="text-sm text-gray-200">{value}</span>
    </div>
  );
}
