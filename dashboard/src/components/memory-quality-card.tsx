import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { AlertTriangle, CheckCircle2, AlertCircle } from "lucide-react";
import { useTranslation } from "react-i18next";
import type { MemoryQualityMetrics } from "../types/api";

interface MemoryQualityCardProps {
  quality?: MemoryQualityMetrics;
}

export function MemoryQualityCard({ quality }: MemoryQualityCardProps) {
  const { t } = useTranslation();

  function getQualityStatus(ratio: number) {
    if (ratio <= 0.1) {
      return {
        icon: CheckCircle2,
        text: t("dashboard.quality.statusGood"),
        className: "bg-green-500 hover:bg-green-600",
        textColor: "text-green-600",
      };
    } else if (ratio <= 0.2) {
      return {
        icon: CheckCircle2,
        text: t("dashboard.quality.statusGood"),
        className: "bg-blue-500 hover:bg-blue-600",
        textColor: "text-blue-600",
      };
    } else if (ratio <= 0.5) {
      return {
        icon: AlertCircle,
        text: t("dashboard.quality.statusWarning"),
        className: "bg-yellow-500 hover:bg-yellow-600",
        textColor: "text-yellow-600",
      };
    } else {
      return {
        icon: AlertTriangle,
        text: t("dashboard.quality.statusCritical"),
        className: "bg-red-500 hover:bg-red-600",
        textColor: "text-red-600",
      };
    }
  }

  function formatCriteriaLabel(key: string): string {
    const keyMap: Record<string, string> = {
      missing_metadata: "dashboard.quality.missingMetadata",
      empty_content: "dashboard.quality.emptyContent",
      no_embedding: "dashboard.quality.noEmbedding",
      low_importance: "dashboard.quality.lowImportance",
    };
    return keyMap[key] ? t(keyMap[key]) : key;
  }

  if (!quality) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <AlertTriangle className="size-5" />
            {t("dashboard.quality.title")}
          </CardTitle>
          <CardDescription>{t("dashboard.quality.loading")}</CardDescription>
        </CardHeader>
      </Card>
    );
  }

  const qualityStatus = getQualityStatus(quality.low_quality_ratio);
  const StatusIcon = qualityStatus.icon;
  const percentage = (quality.low_quality_ratio * 100).toFixed(1);

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <AlertTriangle className="size-5" />
          {t("dashboard.quality.title")}
        </CardTitle>
        <CardDescription>
          {quality.total_memories.toLocaleString()} {t("dashboard.quality.memoriesAnalyzed")}
        </CardDescription>
      </CardHeader>
      <CardContent>
        <div className="space-y-4">
          {/* Quality Overview */}
          <div className="flex items-center justify-between">
            <div>
              <p className="text-sm text-muted-foreground mb-1">
                {t("dashboard.quality.lowQualityRatio")}
              </p>
              <div className="flex items-baseline gap-2">
                <span className={`text-4xl font-bold ${qualityStatus.textColor}`}>
                  {percentage}%
                </span>
                <Badge className={qualityStatus.className}>
                  <StatusIcon className="size-3 mr-1" />
                  {qualityStatus.text}
                </Badge>
              </div>
              <p className="text-xs text-muted-foreground mt-1">
                {quality.low_quality_count} / {quality.total_memories}
              </p>
            </div>
          </div>

          {/* Quality Issues Distribution */}
          {quality.quality_criteria &&
            Object.keys(quality.quality_criteria).length > 0 && (
              <div className="pt-4 border-t">
                <h4 className="text-sm font-medium mb-3">{t("dashboard.quality.qualityIssues")}</h4>
                <div className="space-y-2">
                  {Object.entries(quality.quality_criteria)
                    .filter(([_, count]) => count > 0)
                    .sort(([_, a], [__, b]) => b - a)
                    .map(([key, count]) => {
                      const maxCount = Math.max(
                        ...Object.values(quality.quality_criteria)
                      );
                      const widthPercent = maxCount > 0 ? (count / maxCount) * 100 : 0;

                      return (
                        <div key={key} className="space-y-1">
                          <div className="flex items-center justify-between text-sm">
                            <span className="text-muted-foreground">
                              {formatCriteriaLabel(key)}
                            </span>
                            <span className="font-medium">{count}</span>
                          </div>
                          <div className="h-2 bg-muted rounded-full overflow-hidden">
                            <div
                              className="h-full bg-primary rounded-full transition-all"
                              style={{ width: `${widthPercent}%` }}
                            />
                          </div>
                        </div>
                      );
                    })}
                </div>
              </div>
            )}

          {/* No Issues Message */}
          {quality.quality_criteria &&
            Object.values(quality.quality_criteria).every(
              (count) => count === 0
            ) && (
              <div className="pt-4 border-t">
                <div className="flex items-center gap-2 text-sm text-green-600">
                  <CheckCircle2 className="size-4" />
                  <span>{t("dashboard.quality.statusGood")}</span>
                </div>
              </div>
            )}
        </div>
      </CardContent>
    </Card>
  );
}
