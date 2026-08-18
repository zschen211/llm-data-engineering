// infra pages: middleware status & console entry points. The Ray cluster
// state comes from the asset service (/api/cluster/status of :8000); the
// middleware consoles follow the ports fixed in infra/docs/contract.md.

import { Button } from "@/components/ui/button";
import {
  ErrorNote,
  InfoCard,
  Mono,
  PageContainer,
  PageSection,
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
        <InfoCard
          items={[
            ["ray", data.initialized ? "initialized" : "down"],
            ["nodes", data.alive_nodes],
            ["cpus", `${data.available_cpus}/${data.total_cpus}`],
            ["running tasks", data.running_tasks],
            ["alive actors", data.alive_actors],
            ["address", <Mono key="a">{data.address}</Mono>],
            [
              "dashboard",
              <a
                key="d"
                href={data.dashboard_url}
                target="_blank"
                rel="noreferrer"
                className="text-primary hover:underline"
              >
                {data.dashboard_url}
              </a>,
            ],
            ["metrics_port", <Mono key="m">{data.metrics_port}</Mono>],
            ["logs_dir", <Mono key="l">{data.logs_dir}</Mono>],
          ]}
        />
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
