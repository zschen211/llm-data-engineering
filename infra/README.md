# infra —— 中间件与运维层（声明式，无 Python 代码）

仓库四层结构的第二层（最底层）：统一管理所有服务共用的中间件（RustFS、
Ray、Prometheus、Grafana、node-exporter）与生命周期运维。**业务代码不
import 本目录**；双方通过 [docs/contract.md](docs/contract.md) 中的契约
（端口/环境变量/指标名/API 路径）对接。

## 目录

```
infra/
├── docs/contract.md          # 契约：端口 / env / 指标 / API 路径 / 数据目录
├── docker-compose.yml        # RustFS + Prometheus + Grafana + node-exporter
├── prometheus/prometheus.yml # 抓取 asset-management:8000 / data-factory:8001
│                             #   / ray-metrics:8080 / node-exporter:9100
├── grafana/provisioning/     # dashboards（asset_ 指标前缀）+ datasource
├── scripts/                  # 生命周期脚本（见下）
└── nginx/                    # 生产网关配置（frontend 静态 + /api 分流，见 Phase 5）
```

## 快速开始

```bash
cp .env.example .env          # 按需修改（RustFS 凭据 / Grafana 密码）

./scripts/up.sh               # 中间件：RustFS + Prometheus + Grafana + node-exporter
./scripts/ray-start.sh        # Ray 独立集群（GCS:6379 / dashboard:8265 / metrics:8080）
export RAY_ADDRESS=127.0.0.1:6379

# 然后在各自目录启动业务服务：
#   asset-management: scripts/serve.sh --port 8000
#   data-factory:     scripts/serve.sh --port 8001

./scripts/status.sh           # compose + ray 状态
./scripts/obs_check.sh        # 可观测性冒烟：targets + 两服务 /metrics
./scripts/backup.sh           # SQLite 一致快照 → infra/backups/<ts>/
./scripts/clean.sh            # 清理 tmp（需确认）
```

## 端口速览

| 端口 | 服务 |
| --- | --- |
| 9000/9001 | RustFS S3 API / Console |
| 9090 | Prometheus |
| 3000 | Grafana（admin / $GRAFANA_ADMIN_PASSWORD） |
| 9100 | node-exporter |
| 6379/8265/8080 | Ray GCS / Dashboard / metrics agent |
| 8000/8001 | asset-management / data-factory HTTP |

## 约定

- **声明式**：本目录只有 compose 配置、Prometheus/Grafana 配置与 bash 脚本；
  服务侧持各自的中间件适配器（StorageBackend / ClusterManager / obs），
  经契约文档的 env 变量对接。
- **Ray**：独立集群由 `ray-start.sh` 启动；服务读 `RAY_ADDRESS` 连接，未配置
  时允许内嵌集群兜底（dev/测试）。测试永远跑本地内嵌集群，不碰共享集群。
- **指标**：业务服务埋点统一 `asset_` 前缀，见契约「指标契约」节。
