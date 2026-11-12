# CI 说明

这个目录是 GitHub Actions 定时聚合订阅的配置。

## 文件

| 文件 | 作用 |
|---|---|
| `ci/config.json` | 订阅源与输出格式配置，改这里就能换源 |
| `.github/workflows/aggregate.yml` | 定时任务（每 6 小时）+ 手动触发 |
| `.github/scripts/postprocess.py` | 去重、阈值校验、重建三种格式 |
| `.github/scripts/publish.py` | 把产物写进 Gist |

## 换订阅源

编辑 `ci/config.json` 里的 `sites` 数组，每项一个源：

```json
{
  "name": "源名称",
  "subscribe": ["https://example.com/sub"],
  "domain": "github.com",
  "enable": true,
  "push_to": ["proxy"],
  "ignore_default_exclude": true,
  "check_alive": true,
  "max_rate": 3.0,
  "count": 10
}
```

注意三个硬性约束（都是实测踩出来的）：

- `count` 最大 **10**，超过直接报 `must be <= 10` 退出
- `push_to` 必须写成 `["proxy"]`，留空会导致抓完节点却不出任何文件
- `domain` 只是标签，填 `github.com` 即可

改完后提交，工作流会因为 `ci/**` 变更自动跑一次。

## 改运行频率

编辑 `.github/workflows/aggregate.yml` 的 cron（**UTC 时间**，比北京时间晚 8 小时）：

```yaml
schedule:
  - cron: "0 */6 * * *"   # 每 6 小时 = 北京 02/08/14/20 点
```

## 调整可用性门槛

`MIN_NODES` 环境变量（默认 5）：去重后节点数低于它，就判定本次失败，
**不更新 Gist**，订阅内容保持上一次的结果。

## 需要配置的 Secrets

| 名称 | 内容 |
|---|---|
| `GIST_TOKEN` | 有 `gist` 权限的 GitHub Token |
| `GIST_ID` | 目标 Gist 的 ID |

注意：Gist 即使是 secret 也只是"未被索引"，任何拿到链接的人都能读。
