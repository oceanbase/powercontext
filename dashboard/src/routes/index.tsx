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
  type ChartConfig,
  ChartContainer,
  ChartLegend,
  ChartLegendContent,
  ChartTooltip,
  ChartTooltipContent,
} from "@/components/ui/chart";
import { Input } from "@/components/ui/input";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

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
  const [apiKeyInput, setApiKeyInput] = useState(
    localStorage.getItem("powermem_api_key") || "",
  );

  const {
    data: stats,
    isLoading,
    error,
    refetch,
  } = useQuery({
    queryKey: ["stats", user_id, agent_id],
    queryFn: () => api.getStats({ user_id, agent_id }),
    // to instantly show the API key error without waiting
    retry: false,
  });

  const saveApiKey = () => {
    localStorage.setItem("powermem_api_key", apiKeyInput);
    refetch();
  };

  if (isLoading) {
    return (
      <div className="flex items-center justify-center min-h-[400px]">
        <RefreshCcw className="h-12 w-12 animate-spin text-primary" />
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
              Error loading statistics
            </CardTitle>
            <CardDescription className="text-destructive/80">
              {(error as Error).message}
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="flex flex-col sm:flex-row gap-2">
              <Input
                type="password"
                placeholder="Enter API Key"
                className="max-w-xs"
                value={apiKeyInput}
                onChange={(e) => setApiKeyInput(e.target.value)}
              />
              <Button variant="destructive" onClick={saveApiKey}>
                Update Key & Retry
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
            Memory Overview
            {user_id && (
              <Badge variant="secondary" className="font-mono text-[10px]">
                USER: {user_id}
              </Badge>
            )}
          </h1>
          <p className="text-muted-foreground text-sm">
            Real-time analytics for your intelligent memory system.
          </p>
        </div>
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
            Clear Filters
          </Button>
        )}
      </div>

      {/* Stats Grid */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        <StatCard
          icon={<Database className="size-4" />}
          label="Total Memories"
          value={stats.total_memories.toLocaleString()}
          description="Stored vector records"
        />
        <StatCard
          icon={<TrendingUp className="size-4" />}
          label="Avg. Importance"
          value={stats.avg_importance.toFixed(2)}
          description="Aggregate priority score"
        />
        <StatCard
          icon={<Activity className="size-4" />}
          label="Access Density"
          value={(
            stats.top_accessed.reduce(
              (acc, curr) => acc + curr.access_count,
              0,
            ) / (stats.total_memories || 1)
          ).toFixed(2)}
          description="Average hits per record"
        />
        <StatCard
          icon={<Clock className="size-4" />}
          label="Unique Dates"
          value={trendData.length.toString()}
          description="Days with activity"
        />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Growth Trend */}
        <Card>
          <CardHeader>
            <CardTitle className="text-sm font-medium flex items-center gap-2">
              <TrendingUp className="size-4 text-primary" />
              Growth Trend
            </CardTitle>
            <CardDescription>Daily memory creation volume</CardDescription>
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

        {/* Category Distribution */}
        <Card>
          <CardHeader>
            <CardTitle className="text-sm font-medium flex items-center gap-2">
              <BarChart3 className="size-4 text-primary" />
              Memory Categories
            </CardTitle>
            <CardDescription>Distribution by classification</CardDescription>
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
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Top Accessed */}
        <Card className="lg:col-span-2">
          <CardHeader>
            <CardTitle className="text-sm font-medium flex items-center gap-2">
              <Activity className="size-4 text-primary" />
              Hot Memories
            </CardTitle>
            <CardDescription>
              Top retrieved records by access count
            </CardDescription>
          </CardHeader>
          <CardContent>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Content Snippet</TableHead>
                  <TableHead className="text-right">Hits</TableHead>
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
                      No access records found
                    </TableCell>
                  </TableRow>
                )}
              </TableBody>
            </Table>
          </CardContent>
        </Card>

        {/* Age Distribution */}
        <Card>
          <CardHeader>
            <CardTitle className="text-sm font-medium flex items-center gap-2">
              <Clock className="size-4 text-primary" />
              Retention Age
            </CardTitle>
            <CardDescription>Memory lifecycle distribution</CardDescription>
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
