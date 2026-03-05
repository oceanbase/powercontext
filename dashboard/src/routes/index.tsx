import { useQuery } from "@tanstack/react-query";
import { createFileRoute, useNavigate } from "@tanstack/react-router";
import {
  Activity,
  AlertCircle,
  BarChart3,
  Clock,
  Database,
  RefreshCcw,
  TrendingUp,
} from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Line,
  LineChart,
  Pie,
  PieChart,
  XAxis,
  YAxis,
} from "recharts";
import { toast } from "sonner";
import { api } from "../lib/api";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  type ChartConfig,
  ChartContainer,
  ChartLegend,
  ChartLegendContent,
  ChartTooltip,
  ChartTooltipContent,
} from "@/components/ui/chart";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { SystemHealthCard } from "@/components/system-health-card";
import { MemoryQualityCard } from "@/components/memory-quality-card";
import { ErrorBoundary } from "@/components/error-boundary";

export const Route = createFileRoute("/")({
  validateSearch: (search: Record<string, unknown>) => {
    return {
      user_id: search.user_id as string | undefined,
      agent_id: search.agent_id as string | undefined,
    };
  },
  component: OverviewPage,
});

const chartConfig = {
  count: {
    label: "Memory Count",
    color: "var(--chart-1)",
  },
  value: {
    label: "Count",
    color: "var(--chart-2)",
  },
} satisfies ChartConfig;

function OverviewPage() {
  const { user_id, agent_id } = Route.useSearch();
  const navigate = useNavigate();
  const { t } = useTranslation();
  const [apiKeyInput, setApiKeyInput] = useState(
    localStorage.getItem("powermem_api_key") || "",
  );
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [timeRange, setTimeRange] = useState<string>("30d");

  const {
    data: stats,
    isLoading,
    error,
    refetch,
  } = useQuery({
    queryKey: ["stats", user_id, agent_id, timeRange],
    queryFn: () => api.getStats({ user_id, agent_id, time_range: timeRange }),
    // to instantly show the API key error without waiting
    retry: false,
  });

  const {
    data: systemStatus,
    refetch: refetchStatus,
  } = useQuery({
    queryKey: ["system-status"],
    queryFn: () => api.getSystemStatus(),
    refetchInterval: 30000, // Auto-refresh every 30 seconds
    retry: false,
  });

  const {
    data: memoryQuality,
    refetch: refetchQuality,
  } = useQuery({
    queryKey: ["memory-quality", user_id, agent_id, timeRange],
    queryFn: () => api.getMemoryQuality({ user_id, agent_id, time_range: timeRange }),
    retry: false,
  });

  const saveApiKey = () => {
    localStorage.setItem("powermem_api_key", apiKeyInput);
    refetch();
  };

  const handleRefresh = async () => {
    setIsRefreshing(true);
    try {
      await Promise.all([
        refetch(),
        refetchStatus(),
        refetchQuality(),
      ]);
      toast.success(t("dashboard.refresh"), {
        description: t("common.noData"),
      });
    } catch (error) {
      toast.error(t("common.error"), {
        description: t("common.tryAgain"),
      });
    } finally {
      setIsRefreshing(false);
    }
  };

  if (isLoading) {
    return (
      <div className="p-4 space-y-6 max-w-7xl mx-auto">
        {/* Header skeleton */}
        <div className="flex justify-between items-center">
          <div className="space-y-2">
            <Skeleton className="h-8 w-48" />
            <Skeleton className="h-4 w-96" />
          </div>
          <Skeleton className="h-9 w-24" />
        </div>
        
        {/* Stats cards skeleton */}
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
          {[...Array(4)].map((_, i) => (
            <Card key={i}>
              <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-1">
                <Skeleton className="h-4 w-24" />
                <Skeleton className="h-4 w-4" />
              </CardHeader>
              <CardContent>
                <Skeleton className="h-8 w-16 mb-1" />
                <Skeleton className="h-3 w-32" />
              </CardContent>
            </Card>
          ))}
        </div>
        
        {/* System health card skeleton */}
        <Card>
          <CardHeader>
            <Skeleton className="h-5 w-32" />
            <Skeleton className="h-4 w-48 mt-2" />
          </CardHeader>
          <CardContent>
            <Skeleton className="h-[200px] w-full" />
          </CardContent>
        </Card>
        
        {/* Charts skeleton */}
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          {[...Array(2)].map((_, i) => (
            <Card key={i}>
              <CardHeader>
                <Skeleton className="h-5 w-32" />
                <Skeleton className="h-4 w-48 mt-2" />
              </CardHeader>
              <CardContent>
                <Skeleton className="h-[300px] w-full" />
              </CardContent>
            </Card>
          ))}
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="p-6">
        <Card className="border-destructive/50 bg-destructive/5">
          <CardHeader>
            <CardTitle className="text-destructive flex items-center gap-2">
              <AlertCircle size={20} />
              {t("dashboard.error.title")}
            </CardTitle>
            <CardDescription className="text-destructive/80">
              {(error as Error).message}
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="flex flex-col sm:flex-row gap-2">
              <Input
                type="password"
                placeholder={t("dashboard.error.apiKeyPlaceholder")}
                className="max-w-xs"
                value={apiKeyInput}
                onChange={(e) => setApiKeyInput(e.target.value)}
              />
              <Button variant="destructive" onClick={saveApiKey}>
                {t("dashboard.error.updateKey")}
              </Button>
            </div>
          </CardContent>
        </Card>
      </div>
    );
  }

  if (!stats) return null;

  const typeData = Object.entries(stats.by_type).map(
    ([name, value], index) => ({
      name,
      value,
      fill: `var(--chart-${(index % 5) + 1})`,
    }),
  );

  const trendData = Object.entries(stats.growth_trend)
    .sort()
    .map(([date, count]) => ({ date, count }));

  const ageData = Object.entries(stats.age_distribution).map(
    ([name, value]) => ({ name, value }),
  );

  const dynamicChartConfig = typeData.reduce((acc, curr) => {
    acc[curr.name] = {
      label: curr.name,
      color: curr.fill,
    };
    return acc;
  }, {} as ChartConfig);

  return (
    <div className="p-4 space-y-6 max-w-7xl mx-auto">
      <div className="flex flex-col sm:flex-row justify-between items-start sm:items-center gap-4">
        <div>
          <h1 className="text-2xl font-bold tracking-tight flex items-center gap-2">
            {t("dashboard.title")}
            {user_id && (
              <Badge variant="secondary" className="font-mono text-[10px]">
                USER: {user_id}
              </Badge>
            )}
          </h1>
          <p className="text-muted-foreground text-sm">
            {t("dashboard.subtitle")}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Select value={timeRange} onValueChange={setTimeRange}>
            <SelectTrigger className="w-36">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="7d">{t("dashboard.timeRange.last7days")}</SelectItem>
              <SelectItem value="30d">{t("dashboard.timeRange.last30days")}</SelectItem>
              <SelectItem value="90d">{t("dashboard.timeRange.last90days")}</SelectItem>
              <SelectItem value="all">{t("dashboard.timeRange.allTime")}</SelectItem>
            </SelectContent>
          </Select>
          <Button
            variant="outline"
            size="sm"
            onClick={handleRefresh}
            disabled={isRefreshing}
          >
            <RefreshCcw className={`size-4 mr-2 ${isRefreshing ? 'animate-spin' : ''}`} />
            {isRefreshing ? t("dashboard.refreshing") : t("dashboard.refresh")}
          </Button>
          {user_id && (
            <Button
              variant="outline"
              size="sm"
              onClick={() =>
                navigate({
                  to: "/",
                  search: { user_id: undefined, agent_id: undefined },
                })
              }
            >
              {t("dashboard.clearFilters")}
            </Button>
          )}
        </div>
      </div>

      {/* Stats Grid */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        <StatCard
          icon={<Database className="size-4" />}
          label={t("dashboard.stats.totalMemories")}
          value={stats.total_memories.toLocaleString()}
          description={t("dashboard.stats.totalMemoriesDesc")}
        />
        <StatCard
          icon={<TrendingUp className="size-4" />}
          label={t("dashboard.stats.avgImportance")}
          value={stats.avg_importance.toFixed(2)}
          description={t("dashboard.stats.avgImportanceDesc")}
        />
        <StatCard
          icon={<Activity className="size-4" />}
          label={t("dashboard.stats.accessDensity")}
          value={(
            stats.top_accessed.reduce(
              (acc, curr) => acc + curr.access_count,
              0,
            ) / (stats.total_memories || 1)
          ).toFixed(2)}
          description={t("dashboard.stats.accessDensityDesc")}
        />
        <StatCard
          icon={<Clock className="size-4" />}
          label={t("dashboard.stats.uniqueDates")}
          value={trendData.length.toString()}
          description={t("dashboard.stats.uniqueDatesDesc")}
        />
      </div>

      {/* System Health Panel */}
      <ErrorBoundary>
        <SystemHealthCard status={systemStatus} />
      </ErrorBoundary>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Growth Trend */}
        <ErrorBoundary>
          <Card>
            <CardHeader>
              <CardTitle className="text-sm font-medium flex items-center gap-2">
                <TrendingUp className="size-4 text-primary" />
                {t("dashboard.charts.growthTrend")}
              </CardTitle>
              <CardDescription>{t("dashboard.charts.growthTrendDesc")}</CardDescription>
            </CardHeader>
            <CardContent>
              <ChartContainer config={chartConfig} className="h-[300px] w-full">
                <LineChart
                  data={trendData}
                  margin={{ top: 20, left: 12, right: 12 }}
                >
                  <CartesianGrid vertical={false} strokeDasharray="3 3" />
                  <XAxis
                    dataKey="date"
                    tickLine={false}
                    axisLine={false}
                    tickMargin={8}
                    minTickGap={32}
                  />
                  <YAxis tickLine={false} axisLine={false} tickMargin={8} />
                  <ChartTooltip content={<ChartTooltipContent />} />
                  <Line
                    type="monotone"
                    dataKey="count"
                    stroke="var(--color-count)"
                    strokeWidth={2}
                    dot={false}
                  />
                </LineChart>
              </ChartContainer>
            </CardContent>
          </Card>
        </ErrorBoundary>

        {/* Category Distribution */}
        <ErrorBoundary>
          <Card>
          <CardHeader>
            <CardTitle className="text-sm font-medium flex items-center gap-2">
              <BarChart3 className="size-4 text-primary" />
              {t("dashboard.charts.memoryCategories")}
            </CardTitle>
            <CardDescription>{t("dashboard.charts.memoryCategoriesDesc")}</CardDescription>
          </CardHeader>
          <CardContent>
            <ChartContainer
              config={dynamicChartConfig}
              className="h-[300px] w-full"
            >
              <PieChart>
                <Pie
                  data={typeData}
                  dataKey="value"
                  nameKey="name"
                  innerRadius={60}
                  outerRadius={80}
                  strokeWidth={5}
                >
                  {typeData.map((entry, index) => (
                    <Cell key={`cell-${index}`} fill={entry.fill} />
                  ))}
                </Pie>
                <ChartTooltip content={<ChartTooltipContent hideLabel />} />
                <ChartLegend
                  content={<ChartLegendContent nameKey="name" />}
                  className="-translate-y-2 flex-wrap gap-2 [&>*]:basis-1/4 [&>*]:justify-center"
                />
              </PieChart>
            </ChartContainer>
          </CardContent>
        </Card>
        </ErrorBoundary>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Top Accessed */}
        <ErrorBoundary>
          <Card className="lg:col-span-2">
          <CardHeader>
            <CardTitle className="text-sm font-medium flex items-center gap-2">
              <Activity className="size-4 text-primary" />
              {t("dashboard.charts.hotMemories")}
            </CardTitle>
            <CardDescription>
              {t("dashboard.charts.hotMemoriesDesc")}
            </CardDescription>
          </CardHeader>
          <CardContent>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>{t("dashboard.charts.contentSnippet")}</TableHead>
                  <TableHead className="text-right">{t("dashboard.charts.hits")}</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {stats.top_accessed.map((m) => (
                  <TableRow key={m.id}>
                    <TableCell className="font-mono text-[11px] italic max-w-[400px] truncate">
                      "{m.content}"
                    </TableCell>
                    <TableCell className="text-right">
                      <Badge variant="secondary" className="font-mono">
                        {m.access_count}
                      </Badge>
                    </TableCell>
                  </TableRow>
                ))}
                {stats.top_accessed.length === 0 && (
                  <TableRow>
                    <TableCell
                      colSpan={2}
                      className="text-center py-8 text-muted-foreground text-xs"
                    >
                      {t("dashboard.charts.noAccessRecords")}
                    </TableCell>
                  </TableRow>
                )}
              </TableBody>
            </Table>
          </CardContent>
        </Card>
        </ErrorBoundary>

        {/* Age Distribution */}
        <ErrorBoundary>
          <Card>
          <CardHeader>
            <CardTitle className="text-sm font-medium flex items-center gap-2">
              <Clock className="size-4 text-primary" />
              {t("dashboard.charts.retentionAge")}
            </CardTitle>
            <CardDescription>{t("dashboard.charts.retentionAgeDesc")}</CardDescription>
          </CardHeader>
          <CardContent>
            <ChartContainer config={chartConfig} className="h-[300px] w-full">
              <BarChart
                data={ageData}
                layout="vertical"
                margin={{ left: -20, right: 20 }}
              >
                <CartesianGrid horizontal={false} strokeDasharray="3 3" />
                <XAxis type="number" hide />
                <YAxis
                  dataKey="name"
                  type="category"
                  tickLine={false}
                  axisLine={false}
                  fontSize={10}
                />
                <ChartTooltip content={<ChartTooltipContent />} />
                <Bar
                  dataKey="value"
                  fill="var(--color-value)"
                  radius={[0, 4, 4, 0]}
                  barSize={16}
                />
              </BarChart>
            </ChartContainer>
          </CardContent>
        </Card>
        </ErrorBoundary>
      </div>

      {/* Memory Quality Analysis */}
      <div className="grid grid-cols-1 gap-6">
        <ErrorBoundary>
          <MemoryQualityCard quality={memoryQuality} />
        </ErrorBoundary>
      </div>
    </div>
  );
}

function StatCard({
  icon,
  label,
  value,
  description,
}: {
  icon: React.ReactNode;
  label: string;
  value: string;
  description: string;
}) {
  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-1">
        <CardTitle className="text-xs font-medium">{label}</CardTitle>
        <div className="text-muted-foreground">{icon}</div>
      </CardHeader>
      <CardContent>
        <div className="text-xl font-bold">{value}</div>
        <p className="text-[10px] text-muted-foreground mt-0.5">
          {description}
        </p>
      </CardContent>
    </Card>
  );
}
