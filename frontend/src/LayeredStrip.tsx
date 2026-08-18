// The signature element: the four-layer stack as a live status strip.
// INFRA (ray cluster + object store) / ASSET (asset) /
// FACTORY (data-factory) / UI (this console). Clicking a layer jumps to its
// first page.

import { Dot, usePoll } from "./widgets";

interface AssetInfo {
  backend?: string;
  bucket?: string | null;
  source_count?: number;
  asset_count?: number;
  ready_count?: number;
  failed_count?: number;
  snapshot_count?: number;
}

interface ClusterStatus {
  initialized?: boolean;
  alive_nodes?: number;
  total_cpus?: number;
  dashboard_url?: string;
}

interface FactoryInfo {
  backend?: string;
  bucket?: string | null;
  capability_count?: number;
  workflow_count?: number;
  model_count?: number;
  report_count?: number;
}

export function LayeredStrip({
  onNavigate,
}: {
  onNavigate: (hash: string) => void;
}) {
  const assetInfo = usePoll<AssetInfo>("/api/info", 10_000);
  const cluster = usePoll<ClusterStatus>("/api/cluster/status", 10_000);
  const factory = usePoll<FactoryInfo>("/api/factory-info", 10_000);

  const infraMeta = cluster?.initialized
    ? `ray ${cluster.alive_nodes ?? 0} node · ${cluster.total_cpus ?? 0} cpu`
    : "ray down (embedded fallback)";

  const assetMeta = assetInfo
    ? `blob ${assetInfo.backend ?? "-"} · ${assetInfo.ready_count ?? 0}/${assetInfo.asset_count ?? 0} ready`
    : "unreachable";

  const factoryMeta = factory
    ? `${factory.workflow_count ?? 0} wf · ${factory.model_count ?? 0} models · ${factory.report_count ?? 0} reports`
    : "unreachable";

  const toneFor = (ok: boolean): "ok" | "warn" | "danger" => (ok ? "ok" : "danger");

  const layers = [
    { tag: "01 infra", name: "INFRA", meta: infraMeta, hash: "#/infra", tone: toneFor(cluster?.initialized ?? false) },
    { tag: "02 asset", name: "ASSET", meta: assetMeta, hash: "#/asset", tone: toneFor(!!assetInfo) },
    { tag: "03 factory", name: "FACTORY", meta: factoryMeta, hash: "#/factory", tone: toneFor(!!factory) },
    { tag: "04 ui", name: "CONSOLE", meta: "single origin · /api split", hash: "#/asset", tone: "ok" as const },
  ];

  return (
    <div
      className="grid grid-cols-2 gap-2.5 border-b-2 border-primary bg-[linear-gradient(180deg,#1a2436,#16202e)] px-4 py-3 text-[#dbe3f0] lg:grid-cols-4"
      role="navigation"
      aria-label="服务分层状态"
    >
      {layers.map((layer) => (
        <div
          key={layer.tag}
          className="cursor-pointer border border-[#33415c] border-t-2 border-t-primary bg-white/[0.03] px-3 py-2 transition-colors hover:bg-primary/20"
          onClick={() => onNavigate(layer.hash)}
          role="button"
          tabIndex={0}
          onKeyDown={(e) => {
            if (e.key === "Enter" || e.key === " ") {
              onNavigate(layer.hash);
            }
          }}
        >
          <div className="font-mono text-xs uppercase tracking-widest text-[#7f93b5]">
            {layer.tag}
          </div>
          <div className="my-0.5 flex items-center font-mono text-base font-semibold text-white">
            <Dot tone={layer.tone} />
            {layer.name}
          </div>
          <div className="truncate text-xs text-[#aab8d0]">{layer.meta}</div>
        </div>
      ))}
    </div>
  );
}
