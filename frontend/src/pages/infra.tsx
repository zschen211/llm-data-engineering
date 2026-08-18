// infra pages: middleware status & console entry points. The Ray cluster
// state comes from the asset service (/api/cluster/status of :8000); the
// middleware consoles follow the ports fixed in infra/docs/contract.md.

import { Button } from "@/components/ui/button";
import {
  ErrorNote,
  FieldItem,
  Mono,
  PageContainer,
  PageSection,
  StatCard,
  useFetch,
} from "../widgets";

interface ClusterStatus {
  initialized: boolean;
  address: string;
  dashboard_url: string;
  logs_dir: string;
  metrics_port: number;
  total_cpus: number;
  available_cpus: number;
  alive_nodes: number;
  running_tasks: number;
  alive_actors: number;
}

const MIDDLEWARE = [
  { name: "RustFS Console", port: 9001, desc: "对象存储管理台" },
  { name: "Prometheus", port: 9090, desc: "指标抓取与查询" },
  { name: "Grafana", port: 3000, desc: "观测面板（provisioning 自动加载）" },
  { name: "Ray Dashboard", port: 8265, desc: "集群状态/任务/日志" },
];

function InfraOverviewPage() {
  const { data, error, reload } = useFetch<ClusterStatus>("/api/cluster/status");
  const host = window.location.hostname || "localhost";

  return (
    <PageContainer
      desc="infra 契约（端口/环境变量/指标名见 infra/docs/contract.md）：中间件栈 + Ray 集群状态。"
      actions={
        <Button variant="outline" onClick={reload}>
          刷新
        </Button>
      }
    >
      <ErrorNote message={error} />
      {data && (
        <>
          <div className="mb-5 grid grid-cols-[repeat(auto-fill,minmax(180px,1fr))] gap-3">
            <StatCard label="ray" value={data.initialized ? "initialized" : "down"} />
            <StatCard label="nodes" value={data.alive_nodes} />
            <StatCard
              label="cpus (available/total)"
              value={
                <span className="text-sm">
                  {data.available_cpus}/{data.total_cpus}
                </span>
              }
            />
            <StatCard label="running tasks" value={data.running_tasks} />
            <StatCard label="alive actors" value={data.alive_actors} />
          </div>
          <div className="grid grid-cols-[repeat(auto-fill,minmax(220px,1fr))] gap-2.5">
            <FieldItem label="address">
              <Mono>{data.address}</Mono>
            </FieldItem>
            <FieldItem label="dashboard">
              <a href={data.dashboard_url} target="_blank" rel="noreferrer" className="text-primary hover:underline">
                {data.dashboard_url}
              </a>
            </FieldItem>
            <FieldItem label="metrics_port">
              <Mono>{data.metrics_port}</Mono>
            </FieldItem>
            <FieldItem label="logs_dir">
              <Mono>{data.logs_dir}</Mono>
            </FieldItem>
          </div>
        </>
      )}
      <PageSection eyebrow="MIDDLEWARE" title="中间件控制台">
        <div className="grid grid-cols-[repeat(auto-fill,minmax(220px,1fr))] gap-3">
          {MIDDLEWARE.map((m) => (
            <a
              key={m.name}
              className="rounded-md border bg-card p-3.5 no-underline transition-colors hover:border-primary"
              href={`http://${host}:${m.port}`}
              target="_blank"
              rel="noreferrer"
            >
              <div className="font-mono text-xs uppercase tracking-wider text-muted-foreground">
                {m.name}
              </div>
              <div className="mt-0.5 font-mono text-sm font-semibold text-primary">
                {host}:{m.port}
              </div>
              <div className="mt-1 text-sm text-muted-foreground">{m.desc}</div>
            </a>
          ))}
        </div>
      </PageSection>
    </PageContainer>
  );
}

export const infraPages = [{ key: "overview", label: "总览", component: InfraOverviewPage }];
