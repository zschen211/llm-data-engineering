# warc-inspector CLI

`cli/warc_inspector.py` 提供两个子命令，用于查阅 WARC/WARC.GZ 文件内容。

## 用法

```bash
uv run warc-inspector <command> [options] <file>
```

### read

按顺序读取并展示 WARC records，支持 offset、范围控制和 URL 过滤。

```bash
uv run warc-inspector read <file> [--url URL] [--offset N] [--limit N] [--show-content] [--content-length N]
```

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--url` | 无 | 只显示 WARC-Target-URI 精确匹配该值的 records |
| `--offset` | `0` | 跳过前 N 条（匹配后计数） |
| `--limit` | `10` | 最多显示 N 条（匹配后计数） |
| `--show-content` | 关闭 | 打印 record payload |
| `--content-length` | `500` | 显示内容的最大字节数 |

`--offset` 和 `--limit` 作用于过滤后的结果，而非文件中的全部 records。

### stats

扫描整个文件，输出统计摘要。

```bash
uv run warc-inspector stats <file> [--top N]
```

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--top` | `20` | 显示出现次数最多的前 N 个 URL |

输出包含：总 record 数、唯一 URL 数、record 类型分布、HTTP 状态码分布、Top N URL 列表。

## 示例

```bash
# 查看前 10 条
uv run warc-inspector read data/example.warc.gz

# 查看指定 URL 的所有 records 并显示内容
uv run warc-inspector read data/example.warc.gz --url "http://example.com/" --show-content

# 从第 5 条开始读 3 条，并显示内容
uv run warc-inspector read data/example.warc.gz --offset 5 --limit 3 --show-content

# 统计摘要
uv run warc-inspector stats data/example.warc.gz

# Top 50 URL
uv run warc-inspector stats data/example.warc.gz --top 50
```
