import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Activity, CheckCircle2, AlertCircle, XCircle } from "lucide-react";
import { useTranslation } from "react-i18next";
import type { SystemStatus } from "../types/api";

interface SystemHealthCardProps {
  status?: SystemStatus;
}

/**
 * Format uptime seconds into human-readable format (e.g., "2d 5h 32m")
 */
function formatUptime(seconds: number): string {
  const days = Math.floor(seconds / 86400);
  const hours = Math.floor((seconds % 86400) / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);

  const parts = [];
  if (days > 0) parts.push(`${days}d`);
  if (hours > 0) parts.push(`${hours}h`);
  if (minutes > 0) parts.push(`${minutes}m`);

  return parts.length > 0 ? parts.join(" ") : "< 1m";
}

export function SystemHealthCard({ status }: SystemHealthCardProps) {
  const { t } = useTranslation();

  function getStatusDisplay(status: string) {
    switch (status) {
      case "operational":
      case "healthy":
        return {
          variant: "default" as const,
          icon: CheckCircle2,
          text: status === "operational"
            ? t("dashboard.systemHealth.statusOperational")
            : t("dashboard.systemHealth.statusOperational"),
          className: "bg-green-500 hover:bg-green-600",
        };
      case "degraded":
        return {
          variant: "secondary" as const,
          icon: AlertCircle,
          text: t("dashboard.systemHealth.statusDegraded"),
          className: "bg-yellow-500 hover:bg-yellow-600",
        };
      case "down":
      case "unavailable":
        return {
          variant: "destructive" as const,
          icon: XCircle,
          text: t("dashboard.systemHealth.statusDown"),
          className: "bg-red-500 hover:bg-red-600",
        };
      default:
        return {
          variant: "outline" as const,
          icon: Activity,
          text: status,
          className: "",
        };
    }
  }

  if (!status) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Activity className="size-5" />
            {t("dashboard.systemHealth.title")}
          </CardTitle>
          <CardDescription>{t("dashboard.systemHealth.loading")}</CardDescription>
        </CardHeader>
      </Card>
    );
  }

  const systemStatus = getStatusDisplay(status.status);
  const StatusIcon = systemStatus.icon;

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Activity className="size-5" />
          {t("dashboard.systemHealth.title")}
        </CardTitle>
        <CardDescription>
          Real-time system status and dependency health
        </CardDescription>
      </CardHeader>
      <CardContent>
        <div className="space-y-4">
          {/* System Status Overview */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div className="space-y-1">
              <p className="text-sm text-muted-foreground">{t("dashboard.systemHealth.status")}</p>
              <Badge className={systemStatus.className}>
                <StatusIcon className="size-3 mr-1" />
                {systemStatus.text}
              </Badge>
            </div>
            <div className="space-y-1">
              <p className="text-sm text-muted-foreground">{t("dashboard.systemHealth.uptime")}</p>
              <p className="text-sm font-medium">
                {formatUptime(status.uptime_seconds)}
              </p>
            </div>
          </div>

          {/* Configuration Info */}
          {(status.storage_type || status.llm_provider) && (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4 pt-2 border-t">
              {status.storage_type && (
                <div className="space-y-1">
                  <p className="text-sm text-muted-foreground">{t("dashboard.systemHealth.vectorStore")}</p>
                  <p className="text-sm font-medium">{status.storage_type}</p>
                </div>
              )}
              {status.llm_provider && (
                <div className="space-y-1">
                  <p className="text-sm text-muted-foreground">{t("dashboard.systemHealth.llm")}</p>
                  <p className="text-sm font-medium">{status.llm_provider}</p>
                </div>
              )}
            </div>
          )}

          {/* Dependencies Table */}
          {status.dependencies &&
            Object.keys(status.dependencies).length > 0 && (
              <div className="pt-2 border-t">
                <h4 className="text-sm font-medium mb-2">{t("dashboard.systemHealth.dependencies")}</h4>
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Service</TableHead>
                      <TableHead>{t("dashboard.systemHealth.status")}</TableHead>
                      <TableHead className="text-right">{t("dashboard.systemHealth.latency")}</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {Object.entries(status.dependencies).map(([key, dep]) => {
                      const depStatus = getStatusDisplay(dep.status);
                      const DepIcon = depStatus.icon;
                      return (
                        <TableRow key={key}>
                          <TableCell className="font-medium">
                            {dep.name.replace(/_/g, " ").replace(/\b\w/g, (l) =>
                              l.toUpperCase()
                            )}
                          </TableCell>
                          <TableCell>
                            <div className="flex items-center gap-2">
                              <DepIcon
                                className={`size-4 ${
                                  dep.status === "healthy"
                                    ? "text-green-500"
                                    : dep.status === "degraded"
                                      ? "text-yellow-500"
                                      : "text-red-500"
                                }`}
                              />
                              <span className="text-sm capitalize">
                                {dep.status}
                              </span>
                            </div>
                            {dep.error_message && (
                              <p className="text-xs text-muted-foreground mt-1">
                                {dep.error_message}
                              </p>
                            )}
                          </TableCell>
                          <TableCell className="text-right">
                            {dep.latency_ms !== undefined &&
                            dep.latency_ms !== null ? (
                              <span className="text-sm">
                                {dep.latency_ms.toFixed(1)}ms
                              </span>
                            ) : (
                              <span className="text-sm text-muted-foreground">
                                -
                              </span>
                            )}
                          </TableCell>
                        </TableRow>
                      );
                    })}
                  </TableBody>
                </Table>
              </div>
            )}
        </div>
      </CardContent>
    </Card>
  );
}
